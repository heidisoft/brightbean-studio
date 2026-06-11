"""Tests for the live composer preview endpoint."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import Post, PostMedia
from apps.media_library.models import MediaAsset
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


class ComposerPreviewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="preview-owner@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.org = Organization.objects.create(name="Preview Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="Preview Workspace")
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
        self.account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="instagram",
            account_platform_id="ig-preview",
            account_name="Sinhala Preview",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.client.force_login(self.user)
        self.preview_url = reverse("composer:preview", kwargs={"workspace_id": self.workspace.id})

    def test_post_preview_renders_sinhala_caption(self):
        caption = "සිංහල අන්තර්ගතයක් සඳහා පෙරදසුන"

        response = self.client.post(
            self.preview_url,
            data={
                "caption": caption,
                "selected_accounts": str(self.account.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn(caption, body)
        self.assertIn("Sinhala Preview", body)

    def test_compose_page_uses_form_post_for_live_preview(self):
        response = self.client.get(reverse("composer:compose", kwargs={"workspace_id": self.workspace.id}))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn(f'hx-post="{self.preview_url}"', body)
        self.assertIn('hx-include="#composer-form"', body)
        self.assertNotIn("hx-vals=", body)
        self.assertIn("no-cache", response.headers["Cache-Control"])

    def test_schedule_rejects_media_over_selected_provider_upload_limit(self):
        tiktok = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="tiktok",
            account_platform_id="tk-preview",
            account_name="TikTok Preview",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        post = Post.objects.create(
            workspace=self.workspace,
            author=self.user,
            caption="Oversized video",
        )
        asset = MediaAsset.objects.create(
            organization=self.org,
            workspace=self.workspace,
            uploaded_by=self.user,
            file="media_library/tests/large.mp4",
            filename="large.mp4",
            media_type=MediaAsset.MediaType.VIDEO,
            mime_type="video/mp4",
            file_size=65_000_000,
            source="upload",
        )
        PostMedia.objects.create(post=post, media_asset=asset, position=0)
        scheduled_at = timezone.localtime(timezone.now() + timedelta(days=1))

        response = self.client.post(
            reverse("composer:save_post_edit", kwargs={"workspace_id": self.workspace.id, "post_id": post.id}),
            data={
                "action": "schedule",
                "caption": post.caption,
                "selected_accounts": str(tiktok.id),
                "scheduled_date": scheduled_at.date().isoformat(),
                "scheduled_time": scheduled_at.strftime("%H:%M"),
            },
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertIn("media", body["errors"])
        self.assertIn("large.mp4", body["errors"]["media"][0])
        self.assertIn("TikTok Preview", body["errors"]["media"][0])
