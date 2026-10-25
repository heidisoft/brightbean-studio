"""Content retention — deletion dates, the sweep, and the settings page.

The promise under test: expiring content frees storage without costing the
workspace its analytics. Published posts keep their rows and insight snapshots;
only never-published posts disappear entirely. Nothing in flight is touched,
and an "archive" policy never degrades into a silent delete.
"""

import json
import shutil
import tempfile
from datetime import date, timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.analytics.models import PostInsightsSnapshot
from apps.approvals.models import PostComment
from apps.composer.models import PlatformPost, Post, PostMedia, PostVersion
from apps.content_retention import services as retention
from apps.media_library.models import MediaAsset
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.settings_manager.models import WorkspaceSetting
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

LONG_CAPTION = "x" * 500


def _make_workspace():
    org = Organization.objects.create(name="Org")
    ws = Workspace.objects.create(organization=org, name="WS")
    sa = SocialAccount.objects.create(
        workspace=ws,
        platform="linkedin_personal",
        account_platform_id="li-1",
        account_name="Acct",
        connection_status=SocialAccount.ConnectionStatus.CONNECTED,
    )
    return org, ws, sa


class RetentionTestBase(TestCase):
    def setUp(self):
        self.org, self.ws, self.sa = _make_workspace()
        self.past = timezone.now() - timedelta(days=1)

    def _asset(self, name="a.jpg"):
        return MediaAsset.objects.create(
            organization=self.org,
            workspace=self.ws,
            file=ContentFile(b"\xff\xd8\xff-image-bytes", name=name),
            filename=name,
            media_type=MediaAsset.MediaType.IMAGE,
            file_size=17,
        )

    def _post(self, status, *, delete_after=None, edited_days_ago=365, caption=LONG_CAPTION, **pp_kwargs):
        post = Post.objects.create(workspace=self.ws, caption=caption, internal_notes="secret plan")
        pp = PlatformPost.objects.create(post=post, social_account=self.sa, status=status, **pp_kwargs)
        # queryset.update: save() would reset updated_at, which is what the
        # draft period is measured from.
        Post.objects.filter(pk=post.pk).update(
            delete_after=delete_after or self.past,
            updated_at=timezone.now() - timedelta(days=edited_days_ago),
        )
        post.refresh_from_db()
        return post, pp


class DefaultDeleteAfterTests(RetentionTestBase):
    def test_new_post_defaults_to_sixty_days(self):
        post = Post.objects.create(workspace=self.ws, caption="hi")
        expected = timezone.now() + timedelta(days=60)
        self.assertAlmostEqual(post.delete_after.timestamp(), expected.timestamp(), delta=60)

    def test_workspace_override(self):
        WorkspaceSetting.objects.create(workspace=self.ws, key="retention.days", value=14)
        post = Post.objects.create(workspace=self.ws, caption="hi")
        expected = timezone.now() + timedelta(days=14)
        self.assertAlmostEqual(post.delete_after.timestamp(), expected.timestamp(), delta=60)

    @override_settings(CONTENT_RETENTION_DEFAULT_DAYS=90)
    def test_deployment_default(self):
        post = Post.objects.create(workspace=self.ws, caption="hi")
        expected = timezone.now() + timedelta(days=90)
        self.assertAlmostEqual(post.delete_after.timestamp(), expected.timestamp(), delta=60)

    def test_disabled_means_no_date(self):
        WorkspaceSetting.objects.create(workspace=self.ws, key="retention.enabled", value=False)
        post = Post.objects.create(workspace=self.ws, caption="hi")
        self.assertIsNone(post.delete_after)

    def test_explicit_date_is_kept(self):
        when = timezone.now() + timedelta(days=3)
        post = Post.objects.create(workspace=self.ws, caption="hi", delete_after=when)
        self.assertEqual(post.delete_after, when)


class SweepTests(RetentionTestBase):
    def test_published_post_keeps_stats_and_loses_content(self):
        post, pp = self._post("published", platform_post_id="urn:li:1", published_at=self.past)
        asset = self._asset()
        storage_name = asset.file.name
        PostMedia.objects.create(post=post, media_asset=asset)
        PostVersion.objects.create(post=post, version_number=1, snapshot={"caption": LONG_CAPTION})
        PostComment.objects.create(post=post, body="looks good")
        PostInsightsSnapshot.objects.create(platform_post=pp, metric_key="impressions", date=date.today(), value=42)

        results = retention.run_retention()

        self.assertEqual(results[self.ws.id]["purged"], 1)
        post.refresh_from_db()
        pp.refresh_from_db()
        self.assertIsNotNone(post.content_purged_at)
        self.assertEqual(len(post.caption), retention.CAPTION_SNIPPET_LENGTH + 1)
        self.assertEqual(post.internal_notes, "")
        self.assertEqual(pp.platform_post_id, "urn:li:1")
        self.assertEqual(pp.status, "published")
        self.assertEqual(PostInsightsSnapshot.objects.get(platform_post=pp).value, 42)
        self.assertFalse(post.media_attachments.exists())
        self.assertFalse(post.versions.exists())
        self.assertFalse(post.comments.exists())
        # The media file was unreferenced after the purge, so it is gone too.
        self.assertFalse(MediaAsset.objects.filter(id=asset.id).exists())
        self.assertFalse(storages["default"].exists(storage_name))
        self.assertEqual(results[self.ws.id]["bytes_freed"], 17)

    def test_never_published_post_is_deleted(self):
        post, _ = self._post("draft")
        results = retention.run_retention()
        self.assertEqual(results[self.ws.id]["deleted"], 1)
        self.assertFalse(Post.objects.filter(id=post.id).exists())

    def test_in_flight_and_not_yet_due_posts_are_untouched(self):
        scheduled, _ = self._post("scheduled", scheduled_at=timezone.now() + timedelta(days=1))
        in_review, _ = self._post("pending_review")
        not_due, _ = self._post("published", platform_post_id="1", delete_after=timezone.now() + timedelta(days=5))

        retention.run_retention()

        for post in (scheduled, in_review, not_due):
            post.refresh_from_db()
            self.assertIsNone(post.content_purged_at)
            self.assertEqual(post.caption, LONG_CAPTION)

    def test_shared_media_survives(self):
        post, _ = self._post("published", platform_post_id="1")
        other, _ = self._post("draft", edited_days_ago=1)
        asset = self._asset()
        PostMedia.objects.create(post=post, media_asset=asset)
        PostMedia.objects.create(post=other, media_asset=asset)

        retention.run_retention()

        self.assertTrue(MediaAsset.objects.filter(id=asset.id).exists())

    def test_draft_period_counts_from_last_edit(self):
        # delete_after passed, but the draft was edited yesterday: still in use.
        recent, _ = self._post("draft", edited_days_ago=1)
        # delete_after is a month out, but the draft was abandoned 31 days ago.
        abandoned, _ = self._post("draft", delete_after=timezone.now() + timedelta(days=30), edited_days_ago=31)

        retention.run_retention()

        self.assertTrue(Post.objects.filter(id=recent.id).exists())
        self.assertFalse(Post.objects.filter(id=abandoned.id).exists())

    def test_rejected_and_failed_follow_draft_period(self):
        rejected, _ = self._post("rejected", edited_days_ago=31)
        failed, _ = self._post("failed", edited_days_ago=31)
        retention.run_retention()
        self.assertFalse(Post.objects.filter(id__in=[rejected.id, failed.id]).exists())

    def test_custom_draft_period(self):
        WorkspaceSetting.objects.create(workspace=self.ws, key="retention.draft_days", value=90)
        kept, _ = self._post("draft", edited_days_ago=60)
        gone, _ = self._post("draft", edited_days_ago=91)
        retention.run_retention()
        self.assertTrue(Post.objects.filter(id=kept.id).exists())
        self.assertFalse(Post.objects.filter(id=gone.id).exists())

    def test_draft_cleanup_off_keeps_drafts_but_still_purges_published(self):
        WorkspaceSetting.objects.create(workspace=self.ws, key="retention.drafts_enabled", value=False)
        draft, _ = self._post("draft")
        published, _ = self._post("published", platform_post_id="1")

        retention.run_retention()

        self.assertTrue(Post.objects.filter(id=draft.id).exists())
        published.refresh_from_db()
        self.assertIsNotNone(published.content_purged_at)

    def test_draft_without_date_is_kept(self):
        draft, _ = self._post("draft")
        Post.objects.filter(pk=draft.pk).update(delete_after=None)
        retention.run_retention()
        self.assertTrue(Post.objects.filter(id=draft.id).exists())

    def test_disabled_workspace_is_skipped(self):
        WorkspaceSetting.objects.create(workspace=self.ws, key="retention.enabled", value=False)
        post, _ = self._post("draft")
        retention.run_retention()
        self.assertTrue(Post.objects.filter(id=post.id).exists())

    def test_archive_without_storage_never_deletes(self):
        WorkspaceSetting.objects.create(workspace=self.ws, key="retention.action", value="archive")
        post, _ = self._post("draft")
        retention.run_retention()
        self.assertTrue(Post.objects.filter(id=post.id).exists())

    def test_dry_run_changes_nothing(self):
        post, _ = self._post("draft")
        results = retention.run_retention(dry_run=True)
        self.assertEqual(results[self.ws.id], {"due": 1})
        self.assertTrue(Post.objects.filter(id=post.id).exists())

    def test_last_run_accumulates(self):
        self._post("draft")
        retention.run_retention()
        self._post("draft")
        retention.run_retention()
        last = retention.get_last_run(self.ws.id)
        self.assertEqual(last["deleted"], 1)
        self.assertEqual(last["total_deleted"], 2)


class ArchiveTests(RetentionTestBase):
    def setUp(self):
        super().setUp()
        self.archive_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.archive_dir, ignore_errors=True)
        storages_setting = {
            **settings.STORAGES,
            "archive": {
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": self.archive_dir},
            },
        }
        override = override_settings(STORAGES=storages_setting)
        override.enable()
        self.addCleanup(override.disable)

    def test_default_action_is_archive_when_available(self):
        self.assertEqual(retention.get_policy(self.ws.id).action, "archive")

    def test_archive_copies_content_and_media_before_purge(self):
        post, _ = self._post("published", platform_post_id="1")
        asset = self._asset()
        PostMedia.objects.create(post=post, media_asset=asset, alt_text="a cat")

        retention.run_retention()

        post.refresh_from_db()
        self.assertTrue(post.content_archive_path.endswith("post.json"))
        archive = storages["archive"]
        snapshot = json.loads(archive.open(post.content_archive_path).read())
        self.assertEqual(snapshot["post"]["caption"], LONG_CAPTION)
        self.assertEqual(snapshot["post"]["internal_notes"], "secret plan")
        [media] = snapshot["media"]
        self.assertEqual(media["alt_text"], "a cat")
        self.assertEqual(archive.open(media["archived_path"]).read(), b"\xff\xd8\xff-image-bytes")

    def test_archive_failure_leaves_post_intact(self):
        post, _ = self._post("draft")
        asset = self._asset()
        PostMedia.objects.create(post=post, media_asset=asset)
        asset.file.storage.delete(asset.file.name)  # source file vanished → copy fails

        results = retention.run_retention()

        self.assertEqual(results[self.ws.id]["errors"], 1)
        self.assertTrue(Post.objects.filter(id=post.id).exists())


class SavePolicyTests(RetentionTestBase):
    def test_apply_to_existing_redates_unpurged_posts(self):
        post = Post.objects.create(workspace=self.ws, caption="hi")
        count = retention.save_policy(self.ws, enabled=True, days=10, action="delete", apply_to_existing=True)
        self.assertEqual(count, 1)
        post.refresh_from_db()
        self.assertEqual(post.delete_after, post.created_at + timedelta(days=10))

    def test_without_apply_existing_dates_stay(self):
        post = Post.objects.create(workspace=self.ws, caption="hi")
        original = post.delete_after
        retention.save_policy(self.ws, enabled=True, days=10, action="delete")
        post.refresh_from_db()
        self.assertEqual(post.delete_after, original)

    def test_archive_requires_storage(self):
        with self.assertRaises(ValueError):
            retention.save_policy(self.ws, enabled=True, days=60, action="archive")


class RetentionSettingsViewTests(RetentionTestBase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            email="owner@example.com", password="testpass123", tos_accepted_at=timezone.now()
        )
        OrgMembership.objects.create(user=self.user, organization=self.org, org_role=OrgMembership.OrgRole.OWNER)
        self.membership = WorkspaceMembership.objects.create(
            user=self.user, workspace=self.ws, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
        )
        self.client.force_login(self.user)
        self.url = reverse("workspaces:retention_settings", kwargs={"workspace_id": self.ws.id})

    def test_get_renders(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Content Retention")
        self.assertContains(response, 'value="60"')
        self.assertContains(response, 'name="draft_retention_days" value="30"')

    def test_post_saves_policy(self):
        response = self.client.post(
            self.url,
            {
                "retention_enabled": "1",
                "retention_days": "90",
                "retention_action": "delete",
                "drafts_enabled": "1",
                "draft_retention_days": "14",
            },
        )
        self.assertEqual(response.status_code, 302)
        policy = retention.get_policy(self.ws.id)
        self.assertEqual(
            (policy.enabled, policy.days, policy.action, policy.drafts_enabled, policy.draft_days),
            (True, 90, "delete", True, 14),
        )

    def test_post_can_turn_draft_cleanup_off(self):
        self.client.post(
            self.url,
            {
                "retention_enabled": "1",
                "retention_days": "60",
                "retention_action": "delete",
                "draft_retention_days": "30",
            },
        )
        self.assertFalse(retention.get_policy(self.ws.id).drafts_enabled)

    def test_out_of_range_draft_days_rejected(self):
        self.client.post(
            self.url,
            {
                "retention_enabled": "1",
                "retention_days": "60",
                "retention_action": "delete",
                "draft_retention_days": "2",
            },
        )
        self.assertEqual(retention.get_policy(self.ws.id).draft_days, 30)

    def test_out_of_range_days_rejected(self):
        self.client.post(
            self.url,
            {
                "retention_enabled": "1",
                "retention_days": "1",
                "retention_action": "delete",
                "draft_retention_days": "30",
            },
        )
        self.assertEqual(retention.get_policy(self.ws.id).days, 60)

    def test_non_manager_cannot_change(self):
        self.membership.workspace_role = WorkspaceMembership.WorkspaceRole.EDITOR
        self.membership.save()
        response = self.client.post(
            self.url, {"retention_enabled": "1", "retention_days": "30", "retention_action": "delete"}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(retention.get_policy(self.ws.id).days, 60)
