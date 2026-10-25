"""Workspace content retention: expire old post content to cut storage cost.

Every post is stamped with a ``delete_after`` date when it is created
(``Post.save`` → :func:`default_delete_after`). A daily sweep
(:func:`run_retention`) then expires posts whose date has passed, according to
the workspace's policy:

* ``archive`` — copy the post's content (a JSON snapshot plus the original
  media files and comment attachments) to the ``archive`` storage alias, a
  cheaper cold bucket, then purge it.
* ``delete`` — purge without keeping a copy.

Drafts have a period of their own. A post that never reached a platform has no
analytics and usually little value once abandoned, so it is judged by
``retention.draft_days`` counted from its *last edit* (``updated_at``) rather
than by ``delete_after``: a draft someone is still working on is never
removed, however old it is. ``delete_after`` IS NULL still opts a post out
entirely, drafts included, so posts that predate the feature stay put.

Purging never costs the workspace its analytics. ``PostInsightsSnapshot`` and
``PublishLog`` cascade from ``PlatformPost``, so a post that reached a platform
keeps its ``Post`` and ``PlatformPost`` rows (status, platform IDs, publish
dates, category, tags, a caption snippet for analytics tables) and loses only
the heavy content: media, version history, comments, full captions. A post that
never reached a platform has no stats to keep and is deleted outright.

Media that a purge leaves unreferenced is deleted at the end of the run, using
the orphan sweep's own definition of "in use" so a shared asset still attached
to another post, idea, or template is never removed.
"""

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile, File
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone

logger = logging.getLogger(__name__)

ACTION_ARCHIVE = "archive"
ACTION_DELETE = "delete"
ACTIONS = (ACTION_ARCHIVE, ACTION_DELETE)

MIN_RETENTION_DAYS = 7
MAX_RETENTION_DAYS = 3650

KEY_ENABLED = "retention.enabled"
KEY_DAYS = "retention.days"
KEY_ACTION = "retention.action"
KEY_DRAFTS_ENABLED = "retention.drafts_enabled"
KEY_DRAFT_DAYS = "retention.draft_days"
KEY_LAST_RUN = "retention.last_run"

ARCHIVE_STORAGE_ALIAS = "archive"
ARCHIVE_PREFIX = "content-archive"

# Long enough to recognise a post in an analytics table, short enough that a
# purged post costs next to nothing.
CAPTION_SNIPPET_LENGTH = 100

# ``platform_extra`` keys that point at media assets. Must match what
# ``apps.media_library.services`` scans, or a purge would leave an asset that
# the orphan sweep still treats as referenced.
_PLATFORM_EXTRA_ASSET_KEYS = ("thumbnail_asset_id", "cover_image_asset_id")

# Platform-post statuses that mean the post is still moving through the
# workflow. A post with any of these is skipped even when its date has passed:
# the date is a storage policy, and it must never pull a post out from under
# a reviewer or the publisher. The next run picks it up once it settles.
_IN_FLIGHT_STATUSES = (
    "pending_review",
    "pending_client",
    "approved",
    "changes_requested",
    "scheduled",
    "publishing",
    "on_hold",
)


@dataclass(frozen=True)
class RetentionPolicy:
    enabled: bool
    days: int
    action: str
    drafts_enabled: bool
    draft_days: int


def archive_available():
    """Whether the operator configured cold storage (``ARCHIVE_STORAGE_BACKEND``)."""
    return ARCHIVE_STORAGE_ALIAS in settings.STORAGES


def _archive_storage():
    from django.core.files.storage import storages

    return storages[ARCHIVE_STORAGE_ALIAS]


def clamp_days(value, default=None):
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = default or settings.CONTENT_RETENTION_DEFAULT_DAYS
    return max(MIN_RETENTION_DAYS, min(MAX_RETENTION_DAYS, days))


def get_policy(workspace_id, organization_id=None):
    from apps.settings_manager.helpers import get_setting

    enabled = get_setting(workspace_id, KEY_ENABLED, organization_id)
    days = get_setting(workspace_id, KEY_DAYS, organization_id, default=settings.CONTENT_RETENTION_DEFAULT_DAYS)
    action = get_setting(
        workspace_id,
        KEY_ACTION,
        organization_id,
        default=ACTION_ARCHIVE if archive_available() else ACTION_DELETE,
    )
    if action not in ACTIONS:
        action = ACTION_DELETE
    drafts_enabled = get_setting(workspace_id, KEY_DRAFTS_ENABLED, organization_id)
    draft_default = settings.CONTENT_RETENTION_DRAFT_DEFAULT_DAYS
    draft_days = get_setting(workspace_id, KEY_DRAFT_DAYS, organization_id, default=draft_default)
    return RetentionPolicy(
        enabled=bool(enabled),
        days=clamp_days(days),
        action=action,
        drafts_enabled=bool(drafts_enabled),
        draft_days=clamp_days(draft_days, draft_default),
    )


def default_delete_after(workspace_id, organization_id=None):
    """Deletion date for a post created now, or ``None`` when retention is off."""
    policy = get_policy(workspace_id, organization_id)
    if not policy.enabled:
        return None
    return timezone.now() + timedelta(days=policy.days)


def save_policy(workspace, *, enabled, days, action, drafts_enabled=None, draft_days=None, apply_to_existing=False):
    """Persist the workspace's policy; optionally re-date its existing posts.

    ``drafts_enabled`` / ``draft_days`` left as ``None`` keep their current
    values. They need no re-dating: the draft period is measured from each
    draft's last edit at sweep time, so a change applies to every draft at once.

    Without ``apply_to_existing`` a change affects only posts created from now
    on — changing the period must never silently expire a backlog. With it,
    every not-yet-purged post is re-dated to ``created_at + days`` (or cleared,
    when retention is being turned off). Returns the number of posts re-dated.
    """
    from apps.composer.models import Post
    from apps.settings_manager.models import WorkspaceSetting

    if action not in ACTIONS:
        raise ValueError(f"Unknown retention action {action!r}.")
    if action == ACTION_ARCHIVE and not archive_available():
        raise ValueError("Archive storage is not configured on this deployment.")
    days = clamp_days(days)

    values = [(KEY_ENABLED, bool(enabled)), (KEY_DAYS, days), (KEY_ACTION, action)]
    if drafts_enabled is not None:
        values.append((KEY_DRAFTS_ENABLED, bool(drafts_enabled)))
    if draft_days is not None:
        values.append((KEY_DRAFT_DAYS, clamp_days(draft_days, settings.CONTENT_RETENTION_DRAFT_DEFAULT_DAYS)))
    for key, value in values:
        WorkspaceSetting.objects.update_or_create(workspace=workspace, key=key, defaults={"value": value})

    if not apply_to_existing:
        return 0
    posts = Post.objects.for_workspace(workspace.id).filter(content_purged_at__isnull=True)
    if enabled:
        return posts.update(delete_after=F("created_at") + timedelta(days=days))
    return posts.update(delete_after=None)


def _published_exists():
    """Subquery: the post reached a platform (the SQL twin of :func:`_has_stats`)."""
    from apps.analytics.models import PostInsightsSnapshot
    from apps.composer.models import PlatformPost

    reached = PlatformPost.objects.filter(post=OuterRef("pk")).filter(Q(status="published") | ~Q(platform_post_id=""))
    measured = PostInsightsSnapshot.objects.filter(platform_post__post=OuterRef("pk"))
    return Exists(reached) | Exists(measured)


def eligible_posts(workspace_id, *, policy=None, now=None):
    """Posts in the workspace that are due and safe to purge right now.

    Published posts are due once ``delete_after`` passes; never-published ones
    once they have gone ``policy.draft_days`` without an edit (and only when
    draft cleanup is on).
    """
    from apps.composer.models import Post

    now = now or timezone.now()
    policy = policy or get_policy(workspace_id)
    due = Q(was_published=True, delete_after__lte=now)
    if policy.drafts_enabled:
        due |= Q(was_published=False, updated_at__lte=now - timedelta(days=policy.draft_days))
    return (
        Post.objects.for_workspace(workspace_id)
        .filter(content_purged_at__isnull=True, delete_after__isnull=False)
        .annotate(was_published=_published_exists())
        .filter(due)
        .exclude(platform_posts__status__in=_IN_FLIGHT_STATUSES)
        .exclude(scheduled_at__gt=now)
        .exclude(platform_posts__scheduled_at__gt=now)
        .distinct()
    )


def retention_overview(workspace):
    """Numbers for the settings page."""
    from apps.composer.models import Post

    now = timezone.now()
    policy = get_policy(workspace.id, workspace.organization_id)
    posts = Post.objects.for_workspace(workspace.id)
    due_now = eligible_posts(workspace.id, policy=policy, now=now).count()
    return {
        "due_now": due_now,
        # An estimate: assumes nothing is edited or scheduled in the meantime.
        "due_next_30_days": eligible_posts(workspace.id, policy=policy, now=now + timedelta(days=30)).count() - due_now,
        "without_date": posts.filter(content_purged_at__isnull=True, delete_after__isnull=True).count(),
        "purged": posts.filter(content_purged_at__isnull=False).count(),
    }


def get_last_run(workspace_id):
    from apps.settings_manager.models import WorkspaceSetting

    row = WorkspaceSetting.objects.filter(workspace_id=workspace_id, key=KEY_LAST_RUN).first()
    return row.value if row and row.value else None


# ---------------------------------------------------------------------------
# Purging a single post
# ---------------------------------------------------------------------------


def _snippet(text):
    if not text or len(text) <= CAPTION_SNIPPET_LENGTH:
        return text
    return text[:CAPTION_SNIPPET_LENGTH] + "…"


def _post_asset_ids(post):
    """Every media asset the post references, by FK or inside JSON fields."""
    from apps.media_library.services import _to_uuid

    ids = set(post.media_attachments.values_list("media_asset_id", flat=True))
    for pp in post.platform_posts.all():
        extra = pp.platform_extra or {}
        for key in _PLATFORM_EXTRA_ASSET_KEYS:
            ids.add(_to_uuid(extra.get(key)))
        if isinstance(pp.platform_specific_media, list):
            ids.update(_to_uuid(v) for v in pp.platform_specific_media)
    ids.discard(None)
    return ids


def _has_stats(post):
    """Whether any part of the post reached a platform (and so may have analytics)."""
    from apps.analytics.models import PostInsightsSnapshot

    if post.platform_posts.filter(Q(status="published") | ~Q(platform_post_id="")).exists():
        return True
    return PostInsightsSnapshot.objects.filter(platform_post__post=post).exists()


def _copy_to_archive(storage, file_field, name):
    """Copy a stored file into the archive without holding it in memory."""
    from apps.media_library.storage import download_to_path

    fd, tmp_path = tempfile.mkstemp()
    os.close(fd)
    try:
        download_to_path(file_field, tmp_path)
        with open(tmp_path, "rb") as fh:
            return storage.save(name, File(fh))
    finally:
        os.unlink(tmp_path)


def _file_ext(name):
    base = os.path.basename(name or "")
    return os.path.splitext(base)[1].lower()


def archive_post(post, storage=None):
    """Write the post's full content to cold storage; return the snapshot path.

    Raises on any storage failure so the caller leaves the post untouched —
    content is only purged once its archive copy is known to exist.
    """
    from apps.media_library.models import MediaAsset

    storage = storage or _archive_storage()
    created = post.created_at or timezone.now()
    base = f"{ARCHIVE_PREFIX}/{post.workspace.organization_id}/{post.workspace_id}/{created:%Y/%m}/{post.id}"

    positions = {pm.media_asset_id: pm for pm in post.media_attachments.all()}
    media = []
    for asset in MediaAsset.objects.filter(id__in=_post_asset_ids(post)):
        stored = ""
        if asset.file:
            stored = _copy_to_archive(storage, asset.file, f"{base}/media/{asset.id}{_file_ext(asset.file.name)}")
        pm = positions.get(asset.id)
        media.append(
            {
                "asset_id": asset.id,
                "filename": asset.filename,
                "media_type": asset.media_type,
                "mime_type": asset.mime_type,
                "file_size": asset.file_size,
                "width": asset.width,
                "height": asset.height,
                "duration": asset.duration,
                "alt_text": pm.alt_text if pm else asset.alt_text,
                "position": pm.position if pm else None,
                "archived_path": stored,
            }
        )

    comments = []
    for comment in post.comments.select_related("author").order_by("created_at"):
        stored = ""
        if comment.attachment:
            stored = _copy_to_archive(
                storage, comment.attachment, f"{base}/comments/{comment.id}{_file_ext(comment.attachment.name)}"
            )
        comments.append(
            {
                "id": comment.id,
                "parent_id": comment.parent_comment_id,
                "author": comment.author.email if comment.author else None,
                "body": comment.body,
                "visibility": comment.visibility,
                "created_at": comment.created_at,
                "deleted_at": comment.deleted_at,
                "attachment_archived_path": stored,
            }
        )

    snapshot = {
        "format": "brightbean.content-archive.v1",
        "archived_at": timezone.now(),
        "post": {
            "id": post.id,
            "workspace_id": post.workspace_id,
            "author": post.author.email if post.author else None,
            "title": post.title,
            "caption": post.caption,
            "first_comment": post.first_comment,
            "internal_notes": post.internal_notes,
            "tags": post.tags,
            "category": post.category.name if post.category else None,
            "scheduled_at": post.scheduled_at,
            "published_at": post.published_at,
            "created_at": post.created_at,
            "updated_at": post.updated_at,
        },
        "platform_posts": [
            {
                "id": pp.id,
                "platform": pp.social_account.platform,
                "account_name": pp.social_account.account_name,
                "status": pp.status,
                "platform_post_id": pp.platform_post_id,
                "published_at": pp.published_at,
                "scheduled_at": pp.scheduled_at,
                "title": pp.platform_specific_title,
                "caption": pp.platform_specific_caption,
                "first_comment": pp.platform_specific_first_comment,
                "media": pp.platform_specific_media,
                "extra": pp.platform_extra,
            }
            for pp in post.platform_posts.select_related("social_account")
        ],
        "media": media,
        "versions": [
            {"version_number": v.version_number, "snapshot": v.snapshot, "created_at": v.created_at}
            for v in post.versions.order_by("version_number")
        ],
        "comments": comments,
    }
    payload = json.dumps(snapshot, cls=DjangoJSONEncoder, indent=2).encode()
    return storage.save(f"{base}/post.json", ContentFile(payload))


def _strip_content(post, *, now, archive_path):
    """Remove the post's heavy content in place, keeping the rows analytics hang off."""
    for pp in post.platform_posts.all():
        extra = {k: v for k, v in (pp.platform_extra or {}).items() if k not in _PLATFORM_EXTRA_ASSET_KEYS}
        pp.platform_specific_caption = _snippet(pp.platform_specific_caption)
        pp.platform_specific_first_comment = None
        pp.platform_specific_media = None
        pp.platform_extra = extra
        pp.save(
            update_fields=[
                "platform_specific_caption",
                "platform_specific_first_comment",
                "platform_specific_media",
                "platform_extra",
                "updated_at",
            ]
        )

    for comment in post.comments.exclude(attachment=""):
        if comment.attachment:
            comment.attachment.delete(save=False)
    post.comments.all().delete()
    post.media_attachments.all().delete()
    post.versions.all().delete()
    post.approval_reminders.all().delete()

    post.caption = _snippet(post.caption)
    post.first_comment = ""
    post.internal_notes = ""
    post.content_purged_at = now
    post.content_archive_path = archive_path
    post.save(
        update_fields=[
            "caption",
            "first_comment",
            "internal_notes",
            "content_purged_at",
            "content_archive_path",
            "updated_at",
        ]
    )


def purge_post(post, *, action, now=None, storage=None):
    """Expire one post. Returns ``(outcome, released_asset_ids)``.

    ``outcome`` is ``"purged"`` (content stripped, rows and stats kept) or
    ``"deleted"`` (never published, removed entirely). ``released_asset_ids``
    are the media assets this post no longer holds on to; whether they are
    actually unreferenced is decided once per run by the caller.
    """
    from apps.composer.models import Post

    now = now or timezone.now()
    archive_path = archive_post(post, storage) if action == ACTION_ARCHIVE else ""
    released = _post_asset_ids(post)

    with transaction.atomic():
        post = Post.objects.select_for_update().get(pk=post.pk)
        if post.content_purged_at is not None:
            return "skipped", set()
        if not _has_stats(post):
            for comment in post.comments.exclude(attachment=""):
                if comment.attachment:
                    comment.attachment.delete(save=False)
            post.delete()
            return "deleted", released
        _strip_content(post, now=now, archive_path=archive_path)
    return "purged", released


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


def _record_last_run(workspace_id, summary):
    from apps.settings_manager.models import WorkspaceSetting

    previous = get_last_run(workspace_id) or {}
    summary = {
        **summary,
        "total_purged": previous.get("total_purged", 0) + summary["purged"],
        "total_deleted": previous.get("total_deleted", 0) + summary["deleted"],
        "total_bytes_freed": previous.get("total_bytes_freed", 0) + summary["bytes_freed"],
    }
    WorkspaceSetting.objects.update_or_create(
        workspace_id=workspace_id,
        key=KEY_LAST_RUN,
        defaults={"value": json.loads(json.dumps(summary, cls=DjangoJSONEncoder))},
    )


def _delete_released_media(released_by_workspace, emit):
    """Delete released assets nothing else references; return bytes freed per workspace."""
    from apps.media_library.models import MediaAsset
    from apps.media_library.services import ProtectedAssetError, delete_asset, referenced_asset_ids

    freed = {}
    all_released = set().union(*released_by_workspace.values()) if released_by_workspace else set()
    if not all_released:
        return freed

    referenced = referenced_asset_ids()
    # Shared org-library assets (workspace NULL) are left to the orphan sweep:
    # they belong to the organization, not to the workspace whose policy ran.
    candidates = MediaAsset.objects.filter(id__in=all_released - referenced, workspace__isnull=False)
    for asset in candidates:
        size = asset.file_size
        workspace_id = asset.workspace_id
        try:
            delete_asset(asset)
        except ProtectedAssetError:
            continue
        except Exception as exc:
            emit(f"Error deleting media {asset.id}: {exc}")
            continue
        freed[workspace_id] = freed.get(workspace_id, 0) + size
    return freed


def run_retention(*, workspace_id=None, now=None, dry_run=False, batch_size=200, log=None):
    """Expire due content in every workspace (or just ``workspace_id``).

    Returns ``{workspace_id: summary}`` where each summary holds ``purged``,
    ``deleted``, ``errors`` and ``bytes_freed`` (``due`` in dry-run mode).
    """
    from apps.workspaces.models import Workspace

    emit = log or (lambda msg: logger.debug("%s", msg))
    now = now or timezone.now()

    workspaces = Workspace.objects.all()
    if workspace_id:
        workspaces = workspaces.filter(id=workspace_id)

    results = {}
    released_by_workspace = {}
    for workspace in workspaces.select_related("organization"):
        policy = get_policy(workspace.id, workspace.organization_id)
        if not policy.enabled:
            continue
        if policy.action == ACTION_ARCHIVE and not archive_available():
            # Never fall back to deleting: the workspace asked for a copy.
            logger.warning("Retention skipped for workspace %s: archive storage is not configured", workspace.id)
            continue

        due = eligible_posts(workspace.id, policy=policy, now=now)
        if dry_run:
            results[workspace.id] = {"due": due.count()}
            emit(f"[{workspace.name}] {results[workspace.id]['due']} post(s) due ({policy.action})")
            continue

        summary = {"ran_at": now, "action": policy.action, "purged": 0, "deleted": 0, "errors": 0, "bytes_freed": 0}
        released = set()
        # Re-query each batch: purged/deleted posts drop out of ``due``, and a
        # post that keeps failing is excluded so it can't stall the loop.
        failed_ids = set()
        while True:
            batch = list(due.exclude(id__in=failed_ids).select_related("workspace", "author", "category")[:batch_size])
            if not batch:
                break
            for post in batch:
                try:
                    outcome, post_released = purge_post(post, action=policy.action, now=now)
                except Exception:
                    logger.exception("Retention failed for post %s", post.id)
                    summary["errors"] += 1
                    failed_ids.add(post.id)
                    continue
                if outcome == "skipped":
                    failed_ids.add(post.id)
                    continue
                summary[outcome] += 1
                released |= post_released
        released_by_workspace[workspace.id] = released
        results[workspace.id] = summary
        emit(
            f"[{workspace.name}] {summary['purged']} purged, {summary['deleted']} deleted, "
            f"{summary['errors']} errors ({policy.action})"
        )

    if not dry_run:
        for ws_id, freed in _delete_released_media(released_by_workspace, emit).items():
            if ws_id in results:
                results[ws_id]["bytes_freed"] = freed
        for ws_id, summary in results.items():
            _record_last_run(ws_id, summary)
    return results
