"""Tests for the live composer preview endpoint."""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
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
