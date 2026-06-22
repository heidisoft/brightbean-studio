"""Repair stored Facebook upstream post IDs for analytics imports."""

from __future__ import annotations

import ast
import json
from typing import Any
from uuid import UUID

from django.core.management.base import BaseCommand

from apps.composer.models import PlatformPost
from providers import get_provider


class Command(BaseCommand):
    help = (
        "Backfill Facebook PlatformPost.platform_post_id and platform_extra.facebook_upstream_ids "
        "from stored publish metadata. Dry-run by default."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Persist repairs. Default is dry-run.")
        parser.add_argument("--account-id", help="Only repair posts for one Facebook SocialAccount UUID.")
        parser.add_argument("--platform-post-id", help="Only repair one PlatformPost UUID.")
        parser.add_argument(
            "--resolve-graph",
            action="store_true",
            help="For unresolved rows, call Facebook Graph with the page token to resolve post_id.",
        )

    def handle(self, *args, **opts):
        qs = (
            PlatformPost.objects.select_related("social_account", "post")
            .prefetch_related("publish_logs")
            .filter(social_account__platform="facebook")
            .exclude(status=PlatformPost.Status.DRAFT)
        )
        if opts["account_id"]:
            qs = qs.filter(social_account_id=opts["account_id"])
        if opts["platform_post_id"]:
            qs = qs.filter(id=opts["platform_post_id"])

        total = changed = unresolved = 0
        for platform_post in qs.iterator(chunk_size=200):
            total += 1
            repair = self._repair_for(platform_post, resolve_graph=opts["resolve_graph"])
            if not repair:
                unresolved += 1
                self.stdout.write(f"MISS {platform_post.id} current={platform_post.platform_post_id!r}")
                continue

            new_platform_post_id, upstream_ids = repair
            if new_platform_post_id == platform_post.platform_post_id and upstream_ids == (
                platform_post.platform_extra or {}
            ).get("facebook_upstream_ids", {}):
                continue

            changed += 1
            self.stdout.write(
                f"{'FIX' if opts['apply'] else 'DRY'} {platform_post.id} "
                f"{platform_post.platform_post_id!r} -> {new_platform_post_id!r}"
            )
            if opts["apply"]:
                platform_extra = {**(platform_post.platform_extra or {})}
                platform_extra["facebook_upstream_ids"] = upstream_ids
                platform_post.platform_post_id = new_platform_post_id
                platform_post.platform_extra = platform_extra
                platform_post.save(update_fields=["platform_post_id", "platform_extra", "updated_at"])

        verb = "Repaired" if opts["apply"] else "Would repair"
        self.stdout.write(self.style.SUCCESS(f"{verb} {changed} of {total} Facebook platform post(s)."))
        if unresolved:
            self.stdout.write(f"{unresolved} row(s) had no stored resolvable Facebook post ID.")

    def _repair_for(self, platform_post: PlatformPost, *, resolve_graph: bool) -> tuple[str, dict[str, Any]] | None:
        current_id = platform_post.platform_post_id or ""
        upstream_ids = self._facebook_upstream_ids(platform_post)
        new_id = self._best_insights_id(current_id, upstream_ids)

        if new_id is None:
            response_ids = self._ids_from_publish_logs(platform_post)
            upstream_ids = {**response_ids, **upstream_ids}
            new_id = self._best_insights_id(current_id, upstream_ids)

        if new_id is None and resolve_graph and current_id:
            graph_ids = self._ids_from_graph(platform_post, current_id)
            upstream_ids = {**graph_ids, **upstream_ids}
            new_id = self._best_insights_id(current_id, upstream_ids)

        if new_id is None:
            return None

        upstream_ids = {**upstream_ids, "insights_post_id": new_id}
        return new_id, upstream_ids

    @staticmethod
    def _facebook_upstream_ids(platform_post: PlatformPost) -> dict[str, Any]:
        extra = platform_post.platform_extra or {}
        ids = extra.get("facebook_upstream_ids", {})
        return ids if isinstance(ids, dict) else {}

    def _ids_from_publish_logs(self, platform_post: PlatformPost) -> dict[str, Any]:
        ids: dict[str, Any] = {}
        for log in platform_post.publish_logs.all():
            body = _parse_response_body(log.response_body)
            if not body:
                continue
            ids.update(_ids_from_response(body))
            if ids.get("post_id") or ids.get("feed_post_id"):
                break
        return ids

    def _ids_from_graph(self, platform_post: PlatformPost, current_id: str) -> dict[str, Any]:
        account = platform_post.social_account
        try:
            provider = get_provider("facebook", {"page_id": account.account_platform_id})
            data = provider._request(  # noqa: SLF001 - repair command intentionally uses provider's Graph wrapper.
                "GET",
                f"https://graph.facebook.com/v25.0/{current_id}",
                access_token=account.oauth_access_token,
                params={"fields": "id,post_id,permalink_url"},
            ).json()
        except Exception as exc:
            self.stderr.write(f"Graph lookup failed for PlatformPost {platform_post.id}: {exc}")
            return {}
        return _ids_from_response(data)

    @staticmethod
    def _best_insights_id(current_id: str, upstream_ids: dict[str, Any]) -> str | None:
        for key in ("insights_post_id", "feed_post_id", "post_id"):
            value = upstream_ids.get(key)
            if isinstance(value, str) and _looks_like_facebook_feed_post_id(value):
                return value
        if _looks_like_facebook_feed_post_id(current_id):
            return current_id
        return None


def _parse_response_body(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    for parser in (json.loads, ast.literal_eval):
        try:
            value = parser(raw)
        except (SyntaxError, ValueError, TypeError, json.JSONDecodeError):
            continue
        return value if isinstance(value, dict) else {}
    return {}


def _ids_from_response(data: dict[str, Any]) -> dict[str, Any]:
    ids: dict[str, Any] = {}
    if isinstance(data.get("post_id"), str):
        ids["post_id"] = data["post_id"]
        ids["feed_post_id"] = data["post_id"]
    if isinstance(data.get("id"), str):
        ids["response_id"] = data["id"]
    if isinstance(data.get("video_id"), str):
        ids["video_id"] = data["video_id"]
    if isinstance(data.get("photo_ids"), list):
        ids["photo_ids"] = data["photo_ids"]
    return ids


def _looks_like_facebook_feed_post_id(value: str) -> bool:
    if not value or "_" not in value:
        return False
    with_context = value.replace("_", "")
    return with_context.isdigit() and not _looks_like_uuid(value)


def _looks_like_uuid(value: str) -> bool:
    try:
        UUID(str(value))
    except (TypeError, ValueError):
        return False
    return True
