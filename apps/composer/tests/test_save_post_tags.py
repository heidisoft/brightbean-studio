"""HTTP-level tests for composer save_post and autosave tag normalization.

Verifies that excess tags are silently truncated and that XSS payloads survive
to storage and render escaped.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import ApprovalAction
from apps.common.validators import (
    MAX_TAG_LENGTH,
    MAX_TAGS,
    MAX_YT_TAGS_TOTAL_CHARS,
)
from apps.composer.models import PlatformPost, Post
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


class SavePostTagsTests(TestCase):
    """POST /workspace/<id>/composer/compose/save/"""

    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.org = Organization.objects.create(name="Test Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="Test Workspace")
        OrgMembership.objects.create(
            user=self.user,
            organization=self.org,
            org_role=OrgMembership.OrgRole.OWNER,
        )
        WorkspaceMembership.objects.create(
            user=self.user,
            workspace=self.workspace,
            workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
        )
        self.client.force_login(self.user)
        self.save_url = reverse("composer:save_post", kwargs={"workspace_id": self.workspace.id})

    def _save_payload(self, tags_value, extra=None):
        payload = {
            "action": "save_draft",
            "title": "Test post",
            "caption": "body",
            "tags": tags_value,
        }
        if extra:
            payload.update(extra)
        return payload

    def test_30_tags_truncated_to_max(self):
        raw_tags = ",".join(f"tag{i}" for i in range(30))
        response = self.client.post(self.save_url, data=self._save_payload(raw_tags))
        self.assertIn(response.status_code, (200, 204, 302))
        post = Post.objects.filter(workspace=self.workspace).order_by("-created_at").first()
        self.assertIsNotNone(post)
        self.assertEqual(len(post.tags), MAX_TAGS)
        self.assertEqual(post.tags[0], "tag0")
        self.assertEqual(post.tags[-1], f"tag{MAX_TAGS - 1}")

    def test_oversized_tag_truncated(self):
        long_tag = "x" * (MAX_TAG_LENGTH + 50)
        response = self.client.post(self.save_url, data=self._save_payload(long_tag))
        self.assertIn(response.status_code, (200, 204, 302))
        post = Post.objects.filter(workspace=self.workspace).order_by("-created_at").first()
        self.assertEqual(post.tags, ["x" * MAX_TAG_LENGTH])

    def test_xss_payload_persists_verbatim(self):
        payload = "<script>alert(1)</script>"
        response = self.client.post(self.save_url, data=self._save_payload(payload))
        self.assertIn(response.status_code, (200, 204, 302))
        post = Post.objects.filter(workspace=self.workspace).order_by("-created_at").first()
        self.assertEqual(post.tags, [payload])

    def test_xss_payload_renders_escaped_in_compose_edit(self):
        """Post a tag with HTML, then GET the compose edit page; assert escaped."""
        payload = "<script>alert(1)</script>"
        save_response = self.client.post(self.save_url, data=self._save_payload(payload))
        self.assertIn(save_response.status_code, (200, 204, 302))
        post = Post.objects.filter(workspace=self.workspace).order_by("-created_at").first()

        edit_url = reverse(
            "composer:compose_edit",
            kwargs={"workspace_id": self.workspace.id, "post_id": post.id},
        )
        response = self.client.get(edit_url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        # The page renders the tag list inside an x-data attribute via the
        # json_attr filter — must be HTML-escaped.
        self.assertIn("&lt;script&gt;", body)
        self.assertNotIn(f'"{payload}"', body)
        self.assertNotIn("<script>alert(1)</script>", body)

    def test_empty_tags_stores_empty_list(self):
        response = self.client.post(self.save_url, data=self._save_payload(""))
        self.assertIn(response.status_code, (200, 204, 302))
        post = Post.objects.filter(workspace=self.workspace).order_by("-created_at").first()
        self.assertEqual(post.tags, [])

    def test_compose_edit_renders_json_script_containers(self):
        """Follow-up 1: the four |json_script containers must be present in extra_js."""
        save_response = self.client.post(self.save_url, data=self._save_payload(""))
        self.assertIn(save_response.status_code, (200, 204, 302))
        post = Post.objects.filter(workspace=self.workspace).order_by("-created_at").first()

        edit_url = reverse(
            "composer:compose_edit",
            kwargs={"workspace_id": self.workspace.id, "post_id": post.id},
        )
        response = self.client.get(edit_url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        # Each json_script container must render as a <script type="application/json" id="...">
        self.assertIn('<script id="composer-selected-accounts" type="application/json">', body)
        self.assertIn('<script id="composer-char-limits" type="application/json">', body)
        self.assertIn('<script id="composer-platform-extras" type="application/json">', body)
        self.assertIn('<script id="composer-media-items" type="application/json">', body)
        # The JS readers must reference the same IDs
        self.assertIn("document.getElementById('composer-selected-accounts')", body)
        self.assertIn("document.getElementById('composer-char-limits')", body)
        self.assertIn("document.getElementById('composer-platform-extras')", body)
        self.assertIn("document.getElementById('composer-media-items')", body)
        # No |safe leftovers
        self.assertNotIn("{{ selected_account_ids|safe }}", body)
        self.assertNotIn("{{ char_limits_json|safe }}", body)
        self.assertNotIn("{{ media_items_json|safe }}", body)


class YouTubePlatformTagsTests(TestCase):
    """POST /workspace/<id>/composer/compose/save/ with yt_tags_<acc_id>."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.org = Organization.objects.create(name="Test Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="Test Workspace")
        OrgMembership.objects.create(
            user=self.user,
            organization=self.org,
            org_role=OrgMembership.OrgRole.OWNER,
        )
        WorkspaceMembership.objects.create(
            user=self.user,
            workspace=self.workspace,
            workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
        )
        self.youtube_account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="youtube",
            account_platform_id="yt-1",
            account_name="Test YT Channel",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.client.force_login(self.user)
        self.save_url = reverse("composer:save_post", kwargs={"workspace_id": self.workspace.id})

    def test_yt_tags_truncated_to_total_chars_cap(self):
        # 25 tags × 30 chars + 24 delimiters = 774 > 500. Helper must truncate.
        tags_raw = ",".join("y" * 30 for _ in range(25))
        payload = {
            "action": "save_draft",
            "title": "YT post",
            "caption": "body",
            "tags": "",
            "selected_accounts": str(self.youtube_account.id),
            f"yt_tags_{self.youtube_account.id}": tags_raw,
        }
        response = self.client.post(self.save_url, data=payload)
        self.assertIn(response.status_code, (200, 204, 302))
        post = Post.objects.filter(workspace=self.workspace).order_by("-created_at").first()
        platform_post = PlatformPost.objects.get(post=post, social_account=self.youtube_account)
        stored_tags = platform_post.platform_extra.get("tags", [])
        total = sum(len(t) for t in stored_tags) + max(0, len(stored_tags) - 1)
        self.assertLessEqual(total, MAX_YT_TAGS_TOTAL_CHARS)
        self.assertGreater(len(stored_tags), 0)


class ScopedComposerEditTests(TestCase):
    """Scoped account edits must not drop sibling PlatformPost rows."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.org = Organization.objects.create(name="Test Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="Test Workspace")
        OrgMembership.objects.create(
            user=self.user,
            organization=self.org,
            org_role=OrgMembership.OrgRole.OWNER,
        )
        WorkspaceMembership.objects.create(
            user=self.user,
            workspace=self.workspace,
            workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
        )
        self.account_a = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="facebook",
            account_platform_id="fb-1",
            account_name="Facebook Page",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.account_b = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="linkedin_company",
            account_platform_id="li-1",
            account_name="LinkedIn Page",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.post = Post.objects.create(
            workspace=self.workspace,
            author=self.user,
            title="Original title",
            caption="Original caption",
        )
        self.pp_a = PlatformPost.objects.create(
            post=self.post,
            social_account=self.account_a,
            status=PlatformPost.Status.SCHEDULED,
            scheduled_at=timezone.now() + timedelta(days=1),
        )
        self.pp_b = PlatformPost.objects.create(
            post=self.post,
            social_account=self.account_b,
            status=PlatformPost.Status.SCHEDULED,
            scheduled_at=timezone.now() + timedelta(days=2),
        )
        self.client.force_login(self.user)

    def _save_url(self):
        return reverse(
            "composer:save_post_edit",
            kwargs={"workspace_id": self.workspace.id, "post_id": self.post.id},
        )

    def _autosave_url(self):
        return reverse(
            "composer:autosave_edit",
            kwargs={"workspace_id": self.workspace.id, "post_id": self.post.id},
        )

    def _payload(self, extra=None):
        payload = {
            "action": "save_draft",
            "title": "Edited title",
            "caption": "Edited caption",
            "tags": "",
            "selected_accounts": str(self.account_a.id),
        }
        if extra:
            payload.update(extra)
        return payload

    def test_scoped_save_preserves_other_platform_posts(self):
        response = self.client.post(
            self._save_url(),
            data=self._payload({"account_scope": str(self.account_a.id)}),
        )

        self.assertIn(response.status_code, (200, 204, 302))
        self.assertTrue(PlatformPost.objects.filter(id=self.pp_a.id).exists())
        self.assertTrue(PlatformPost.objects.filter(id=self.pp_b.id).exists())

    def test_scoped_autosave_preserves_other_platform_posts(self):
        response = self.client.post(
            self._autosave_url(),
            data=self._payload({"account_scope": str(self.account_a.id)}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(PlatformPost.objects.filter(id=self.pp_a.id).exists())
        self.assertTrue(PlatformPost.objects.filter(id=self.pp_b.id).exists())

    def test_scoped_schedule_preserves_other_platform_schedule(self):
        original_sibling_schedule = self.pp_b.scheduled_at
        scheduled_for = timezone.now() + timedelta(days=3)
        response = self.client.post(
            self._save_url(),
            data=self._payload(
                {
                    "action": "schedule",
                    "account_scope": str(self.account_a.id),
                    "scheduled_date": scheduled_for.date().isoformat(),
                    "scheduled_time": scheduled_for.strftime("%H:%M"),
                }
            ),
        )

        self.assertIn(response.status_code, (200, 204, 302))
        self.pp_a.refresh_from_db()
        self.pp_b.refresh_from_db()
        self.assertEqual(self.pp_a.status, PlatformPost.Status.SCHEDULED)
        self.assertEqual(self.pp_b.scheduled_at, original_sibling_schedule)

    def test_scoped_schedule_materializes_omitted_sibling_parent_fallback(self):
        original_parent_schedule = timezone.now() + timedelta(days=2)
        self.post.scheduled_at = original_parent_schedule
        self.post.save(update_fields=["scheduled_at"])
        self.pp_b.scheduled_at = None
        self.pp_b.save(update_fields=["scheduled_at"])
        scheduled_for = timezone.now() + timedelta(days=3)

        response = self.client.post(
            self._save_url(),
            data=self._payload(
                {
                    "action": "schedule",
                    "account_scope": str(self.account_a.id),
                    "scheduled_date": scheduled_for.date().isoformat(),
                    "scheduled_time": scheduled_for.strftime("%H:%M"),
                }
            ),
        )

        self.assertIn(response.status_code, (200, 204, 302))
        self.pp_b.refresh_from_db()
        self.assertEqual(self.pp_b.scheduled_at, original_parent_schedule)

    def test_scoped_submit_for_approval_only_moves_selected_platform_post(self):
        self.pp_a.status = PlatformPost.Status.DRAFT
        self.pp_a.save(update_fields=["status"])
        self.pp_b.status = PlatformPost.Status.DRAFT
        self.pp_b.save(update_fields=["status"])

        response = self.client.post(
            self._save_url(),
            data=self._payload(
                {
                    "action": "submit_for_approval",
                    "account_scope": str(self.account_a.id),
                }
            ),
        )

        self.assertIn(response.status_code, (200, 204, 302))
        self.pp_a.refresh_from_db()
        self.pp_b.refresh_from_db()
        self.assertEqual(self.pp_a.status, PlatformPost.Status.PENDING_REVIEW)
        self.assertEqual(self.pp_b.status, PlatformPost.Status.DRAFT)
        action = ApprovalAction.objects.get(post=self.post)
        self.assertEqual(action.platform_post_id, self.pp_a.id)

    def test_scoped_resubmit_for_approval_only_moves_selected_platform_post(self):
        self.pp_a.status = PlatformPost.Status.CHANGES_REQUESTED
        self.pp_a.save(update_fields=["status"])
        self.pp_b.status = PlatformPost.Status.CHANGES_REQUESTED
        self.pp_b.save(update_fields=["status"])

        response = self.client.post(
            self._save_url(),
            data=self._payload(
                {
                    "action": "resubmit_for_approval",
                    "account_scope": str(self.account_a.id),
                }
            ),
        )

        self.assertIn(response.status_code, (200, 204, 302))
        self.pp_a.refresh_from_db()
        self.pp_b.refresh_from_db()
        self.assertEqual(self.pp_a.status, PlatformPost.Status.PENDING_REVIEW)
        self.assertEqual(self.pp_b.status, PlatformPost.Status.CHANGES_REQUESTED)
        action = ApprovalAction.objects.get(post=self.post)
        self.assertEqual(action.platform_post_id, self.pp_a.id)

    def test_unscoped_save_still_removes_deselected_platform_posts(self):
        response = self.client.post(self._save_url(), data=self._payload())

        self.assertIn(response.status_code, (200, 204, 302))
        self.assertTrue(PlatformPost.objects.filter(id=self.pp_a.id).exists())
        self.assertFalse(PlatformPost.objects.filter(id=self.pp_b.id).exists())


class PublishedPostDeleteTests(TestCase):
    """Deleting in Studio should delete the remote published post first."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.org = Organization.objects.create(name="Test Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="Test Workspace")
        OrgMembership.objects.create(
            user=self.user,
            organization=self.org,
            org_role=OrgMembership.OrgRole.OWNER,
        )
        WorkspaceMembership.objects.create(
            user=self.user,
            workspace=self.workspace,
            workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
        )
        self.client.force_login(self.user)

    def _make_published_post(self, platform):
        account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform=platform,
            account_platform_id=f"{platform}-acct-1",
            account_name=f"{platform.title()} Account",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        post = Post.objects.create(
            workspace=self.workspace,
            author=self.user,
            title="Original title",
            caption="Original caption",
            published_at=timezone.now(),
        )
        pp = PlatformPost.objects.create(
            post=post,
            social_account=account,
            status=PlatformPost.Status.PUBLISHED,
            platform_post_id=f"{platform}-remote-old",
            published_at=timezone.now(),
            publish_error="Previous transient error",
        )
        return post, pp, account

    def _delete_url(self, post):
        return reverse(
            "composer:post_delete",
            kwargs={"workspace_id": self.workspace.id, "post_id": post.id},
        )

    @patch("providers.facebook.FacebookProvider._request")
    def test_delete_published_post_defaults_to_local_only(self, mock_request):
        post, pp, _account = self._make_published_post("facebook")

        response = self.client.post(self._delete_url(post))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Post.objects.filter(id=post.id).exists())
        self.assertFalse(PlatformPost.objects.filter(id=pp.id).exists())
        mock_request.assert_not_called()

    @patch("providers.facebook.FacebookProvider._request")
    def test_delete_published_facebook_post_deletes_remote_when_requested(self, mock_request):
        response_mock = MagicMock()
        response_mock.json.return_value = {"success": True}
        mock_request.return_value = response_mock
        post, pp, _account = self._make_published_post("facebook")

        response = self.client.post(self._delete_url(post), data={"delete_remote": "true"})

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Post.objects.filter(id=post.id).exists())
        self.assertFalse(PlatformPost.objects.filter(id=pp.id).exists())
        args, kwargs = mock_request.call_args
        self.assertEqual(args[0], "DELETE")
        self.assertTrue(args[1].endswith("/facebook-remote-old"))
        self.assertEqual(kwargs["access_token"], "")

    @patch("providers.facebook.FacebookProvider._request")
    def test_remote_delete_failure_keeps_local_post_for_retry(self, mock_request):
        mock_request.side_effect = RuntimeError("remote unavailable")
        post, pp, _account = self._make_published_post("facebook")

        response = self.client.post(self._delete_url(post), data={"delete_remote": "true"})

        self.assertEqual(response.status_code, 502)
        self.assertTrue(Post.objects.filter(id=post.id).exists())
        self.assertTrue(PlatformPost.objects.filter(id=pp.id).exists())
        self.assertIn("remote unavailable", response.json()["errors"]["delete"][0])

    @patch("providers.instagram.InstagramProvider._request")
    def test_unsupported_instagram_remote_delete_does_not_block_local_delete(self, mock_request):
        post, pp, _account = self._make_published_post("instagram")

        response = self.client.post(self._delete_url(post), data={"delete_remote": "true"})

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Post.objects.filter(id=post.id).exists())
        self.assertFalse(PlatformPost.objects.filter(id=pp.id).exists())
        mock_request.assert_not_called()

    def test_delete_dialog_marks_instagram_remote_delete_unsupported(self):
        post, _pp, _account = self._make_published_post("instagram")
        edit_url = reverse(
            "composer:compose_edit",
            kwargs={"workspace_id": self.workspace.id, "post_id": post.id},
        )

        response = self.client.get(edit_url)

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Remote delete is not supported for:", body)
        self.assertIn("Instagram", body)

    @patch("providers.facebook.FacebookProvider._request")
    def test_delete_single_platform_post_deletes_only_that_remote_target(self, mock_request):
        response_mock = MagicMock()
        response_mock.json.return_value = {"success": True}
        mock_request.return_value = response_mock
        post, pp, account = self._make_published_post("facebook")
        other_account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="mastodon",
            account_platform_id="mastodon-acct-1",
            account_name="Mastodon Account",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        other_pp = PlatformPost.objects.create(
            post=post,
            social_account=other_account,
            status=PlatformPost.Status.DRAFT,
        )
        url = reverse(
            "composer:post_delete",
            kwargs={"workspace_id": self.workspace.id, "post_id": post.id},
        )

        response = self.client.post(f"{url}?account={account.id}", data={"delete_remote": "true"})

        self.assertEqual(response.status_code, 204)
        self.assertTrue(Post.objects.filter(id=post.id).exists())
        self.assertFalse(PlatformPost.objects.filter(id=pp.id).exists())
        self.assertTrue(PlatformPost.objects.filter(id=other_pp.id).exists())
