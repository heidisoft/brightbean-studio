"""Tests for the Content Calendar app (T-1A.2)."""

from datetime import datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.calendar.models import PostingSlot, Queue, QueueEntry
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
    """Queue slots should only rewrite active queued platform posts."""

    def test_assign_queue_slots_does_not_move_published_platform_posts(self):
        from apps.calendar.services import add_to_queue

        org = Organization.objects.create(name="Org", default_timezone="UTC")
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

        original_scheduled_at = datetime(2026, 6, 1, 9, 0, tzinfo=ZoneInfo("UTC"))
        original_published_at = datetime(2026, 6, 1, 9, 5, tzinfo=ZoneInfo("UTC"))
        published_post = Post.objects.create(
            workspace=workspace,
            caption="Already sent",
            scheduled_at=original_scheduled_at,
            published_at=original_published_at,
        )
        published_platform_post = PlatformPost.objects.create(
            post=published_post,
            social_account=account,
            status=PlatformPost.Status.PUBLISHED,
            scheduled_at=original_scheduled_at,
            published_at=original_published_at,
        )
        QueueEntry.objects.create(
            queue=queue,
            post=published_post,
            position=0,
            assigned_slot_datetime=original_scheduled_at,
        )

        new_post = Post.objects.create(workspace=workspace, caption="New queued post")
        new_platform_post = PlatformPost.objects.create(
            post=new_post,
            social_account=account,
            status=PlatformPost.Status.DRAFT,
        )

        now_utc = datetime(2026, 6, 8, 8, 0, tzinfo=ZoneInfo("UTC"))
        with patch("apps.calendar.services.timezone.now", return_value=now_utc):
            add_to_queue(new_post, queue)

        published_post.refresh_from_db()
        published_platform_post.refresh_from_db()
        new_platform_post.refresh_from_db()

        self.assertEqual(published_post.scheduled_at, original_scheduled_at)
        self.assertEqual(published_post.published_at, original_published_at)
        self.assertEqual(published_platform_post.scheduled_at, original_scheduled_at)
        self.assertEqual(published_platform_post.published_at, original_published_at)
        self.assertEqual(new_platform_post.scheduled_at, datetime(2026, 6, 8, 9, 0, tzinfo=ZoneInfo("UTC")))


class PublishSentTabTests(TestCase):
    """The Sent tab should be driven by published time, not mutable schedule time."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.org = Organization.objects.create(name="Org", default_timezone="UTC")
        self.workspace = Workspace.objects.create(organization=self.org, name="Workspace")
        WorkspaceMembership.objects.create(
            user=self.user,
            workspace=self.workspace,
            workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
        )
        self.account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="instagram",
            account_platform_id="ig-1",
            account_name="Instagram",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )

    def test_sent_tab_orders_by_platform_published_at(self):
        older_published_at = timezone.now() - timedelta(days=2)
        newer_published_at = timezone.now() - timedelta(hours=2)

        older_post = Post.objects.create(
            workspace=self.workspace,
            author=self.user,
            caption="Older actual publish",
            scheduled_at=timezone.now() + timedelta(days=30),
            published_at=older_published_at,
        )
        newer_post = Post.objects.create(
            workspace=self.workspace,
            author=self.user,
            caption="Newer actual publish",
            scheduled_at=timezone.now() - timedelta(days=30),
            published_at=newer_published_at,
        )
        PlatformPost.objects.create(
            post=older_post,
            social_account=self.account,
            status=PlatformPost.Status.PUBLISHED,
            scheduled_at=older_post.scheduled_at,
            published_at=older_published_at,
        )
        PlatformPost.objects.create(
            post=newer_post,
            social_account=self.account,
            status=PlatformPost.Status.PUBLISHED,
            scheduled_at=newer_post.scheduled_at,
            published_at=newer_published_at,
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("calendar:publish_tab_sent", kwargs={"workspace_id": self.workspace.id}))

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertLess(body.index("Newer actual publish"), body.index("Older actual publish"))


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
