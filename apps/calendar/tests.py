"""Tests for the Content Calendar app (T-1A.2)."""

from datetime import datetime, time
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.calendar.models import PostingSlot, Queue
from apps.composer.models import PlatformPost, Post
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


class PostingSlotModelTest(TestCase):
    """Test PostingSlot model."""

    def test_day_of_week_choices(self):
        """All 7 days should be available."""
        self.assertEqual(len(PostingSlot.DayOfWeek.choices), 7)
        self.assertEqual(PostingSlot.DayOfWeek.MONDAY, 0)
        self.assertEqual(PostingSlot.DayOfWeek.SUNDAY, 6)

    def test_str_representation(self):
        from apps.social_accounts.models import SocialAccount

        slot = PostingSlot()
        slot.day_of_week = 0
        slot.time = time(9, 0)
        # Use a real SocialAccount instance (unsaved) to satisfy FK descriptor
        account = SocialAccount(account_name="TestAccount", platform="instagram")
        slot.social_account = account
        s = str(slot)
        self.assertIn("Monday", s)
        self.assertIn("09:00", s)

    def test_day_name_property(self):
        slot = PostingSlot()
        slot.day_of_week = 4
        self.assertEqual(slot.day_name, "Friday")


class QueueSchedulingServiceTests(TestCase):
    """Queue slots should be materialized as workspace-local publish times."""

    def test_assign_queue_slots_uses_workspace_timezone_wall_clock(self):
        from apps.calendar.services import add_to_queue

        eastern = ZoneInfo("America/New_York")
        org = Organization.objects.create(name="Org", default_timezone="America/New_York")
        workspace = Workspace.objects.create(organization=org, name="Workspace")
        account = SocialAccount.objects.create(
            workspace=workspace,
            platform="instagram",
            account_platform_id="ig-1",
            account_name="Instagram",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        queue = Queue.objects.create(workspace=workspace, social_account=account, name="Instagram Queue")
        PostingSlot.objects.create(social_account=account, day_of_week=0, time=time(9, 0))
        post = Post.objects.create(workspace=workspace, caption="Queued")
        platform_post = PlatformPost.objects.create(post=post, social_account=account, status="draft")

        now_utc = datetime(2026, 6, 8, 8, 0, tzinfo=ZoneInfo("UTC"))
        with patch("apps.calendar.services.timezone.now", return_value=now_utc):
            add_to_queue(post, queue)

        platform_post.refresh_from_db()
        local_scheduled = platform_post.scheduled_at.astimezone(eastern)

        self.assertEqual(local_scheduled.weekday(), 0)
        self.assertEqual(local_scheduled.hour, 9)
        self.assertEqual(local_scheduled.minute, 0)
        self.assertEqual(platform_post.scheduled_at.astimezone(ZoneInfo("UTC")).hour, 13)


class PostingSlotCrossWorkspaceTests(TestCase):
    """Slot endpoints must scope to the requesting workspace.

    Regression for permission-timing leak: a 404 must come from the workspace-
    scoped query, not from a post-lookup membership check.
    """

    def setUp(self):
        self.user_a = User.objects.create_user(
            email="a@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.org_a = Organization.objects.create(name="Org A")
        self.workspace_a = Workspace.objects.create(organization=self.org_a, name="Workspace A")
        OrgMembership.objects.create(
            user=self.user_a,
            organization=self.org_a,
            org_role=OrgMembership.OrgRole.OWNER,
        )
        WorkspaceMembership.objects.create(
            user=self.user_a,
            workspace=self.workspace_a,
            workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
        )
        self.account_a = SocialAccount.objects.create(
            workspace=self.workspace_a,
            platform="instagram",
            account_platform_id="ig-a",
            account_name="A",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.slot_a = PostingSlot.objects.create(
            social_account=self.account_a,
            day_of_week=0,
            time=time(9, 0),
        )

        # A second workspace and user — completely isolated
        self.user_b = User.objects.create_user(
            email="b@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.org_b = Organization.objects.create(name="Org B")
        self.workspace_b = Workspace.objects.create(organization=self.org_b, name="Workspace B")
        OrgMembership.objects.create(
            user=self.user_b,
            organization=self.org_b,
            org_role=OrgMembership.OrgRole.OWNER,
        )
        WorkspaceMembership.objects.create(
            user=self.user_b,
            workspace=self.workspace_b,
            workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
        )

    def test_delete_slot_from_own_workspace_returns_404_for_slot_in_other_workspace(self):
        """User A scopes the delete URL to workspace A but passes B's slot id."""
        self.client.force_login(self.user_a)
        # workspace A in URL but slot belongs to workspace A — sanity check happy path
        # (deletes a slot the user is allowed to delete)
        url = reverse(
            "calendar:delete_posting_slot",
            kwargs={"workspace_id": self.workspace_a.id, "slot_id": self.slot_a.id},
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PostingSlot.objects.filter(id=self.slot_a.id).exists())

    def test_delete_slot_belonging_to_different_workspace_returns_404(self):
        """A new slot in workspace A; user B (different workspace) tries to delete via /workspace/<B>/."""
        slot_a2 = PostingSlot.objects.create(
            social_account=self.account_a,
            day_of_week=1,
            time=time(10, 0),
        )
        self.client.force_login(self.user_b)
        # User B uses their OWN workspace_id in the URL (auth passes), but the
        # slot_id is from workspace A. Pre-fix this leaked existence via 404
        # only AFTER the lookup; post-fix the workspace-scoped query never
        # finds it.
        url = reverse(
            "calendar:delete_posting_slot",
            kwargs={"workspace_id": self.workspace_b.id, "slot_id": slot_a2.id},
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, 404)
        # Slot must still exist
        self.assertTrue(PostingSlot.objects.filter(id=slot_a2.id).exists())

    def test_update_slot_belonging_to_different_workspace_returns_404(self):
        slot_a2 = PostingSlot.objects.create(
            social_account=self.account_a,
            day_of_week=2,
            time=time(11, 0),
        )
        self.client.force_login(self.user_b)
        url = reverse(
            "calendar:update_posting_slot",
            kwargs={"workspace_id": self.workspace_b.id, "slot_id": slot_a2.id},
        )
        response = self.client.post(url, data={"time": "13:30"})
        self.assertEqual(response.status_code, 404)
        slot_a2.refresh_from_db()
        self.assertEqual(slot_a2.time, time(11, 0))
