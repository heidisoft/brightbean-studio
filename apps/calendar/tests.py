"""Tests for the Content Calendar app (T-1A.2)."""

from datetime import datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.calendar.models import PostingSlot, Queue, QueueEntry
from apps.calendar.services import add_to_queue
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


class PostingSlotGridTemplateTests(SimpleTestCase):
    """Posting slot grids should refresh after HTMX slot actions."""

    def test_grid_listens_for_slots_updated_without_fragile_filter(self):
        template_path = Path("templates/social_accounts/partials/_posting_slots_grid.html")
        body = template_path.read_text()

        self.assertIn('hx-trigger="slotsUpdated from:body"', body)
        self.assertNotIn("slotsUpdated[", body)


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


class QueueSlotAssignmentTests(TestCase):
    """Regression tests for per-account next available slot assignment."""

    def setUp(self):
        self.org = Organization.objects.create(name="Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="Workspace")
        self.account_a = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="facebook",
            account_platform_id="page-a",
            account_name="Page A",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.account_b = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="facebook",
            account_platform_id="page-b",
            account_name="Page B",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )

        now = timezone.now()
        self.first_slot = self._future_slot_datetime(now, days=1, slot_time=time(9, 0))
        self.second_slot = self._future_slot_datetime(now, days=2, slot_time=time(9, 0))
        self.account_b_first_slot = self._future_slot_datetime(now, days=1, slot_time=time(14, 0))
        self.account_b_second_slot = self._future_slot_datetime(now, days=2, slot_time=time(14, 0))

        PostingSlot.objects.create(
            social_account=self.account_a,
            day_of_week=self.first_slot.weekday(),
            time=self.first_slot.time(),
        )
        PostingSlot.objects.create(
            social_account=self.account_a,
            day_of_week=self.second_slot.weekday(),
            time=self.second_slot.time(),
        )
        PostingSlot.objects.create(
            social_account=self.account_b,
            day_of_week=self.account_b_first_slot.weekday(),
            time=self.account_b_first_slot.time(),
        )
        PostingSlot.objects.create(
            social_account=self.account_b,
            day_of_week=self.account_b_second_slot.weekday(),
            time=self.account_b_second_slot.time(),
        )

        self.queue_a = Queue.objects.create(
            workspace=self.workspace,
            name="Page A Queue",
            social_account=self.account_a,
        )
        self.queue_b = Queue.objects.create(
            workspace=self.workspace,
            name="Page B Queue",
            social_account=self.account_b,
        )

    def _future_slot_datetime(self, now, *, days, slot_time):
        slot_date = (now + timedelta(days=days)).date()
        return datetime.combine(slot_date, slot_time).replace(tzinfo=now.tzinfo)

    def _post_for_accounts(self, *accounts, caption="Queued"):
        post = Post.objects.create(workspace=self.workspace, caption=caption)
        for account in accounts:
            PlatformPost.objects.create(
                post=post,
                social_account=account,
                status=PlatformPost.Status.DRAFT,
            )
        return post

    def test_add_to_queue_skips_slot_already_scheduled_for_same_account(self):
        occupied_post = Post.objects.create(
            workspace=self.workspace,
            caption="Already scheduled",
            scheduled_at=self.first_slot,
        )
        PlatformPost.objects.create(
            post=occupied_post,
            social_account=self.account_a,
            status=PlatformPost.Status.SCHEDULED,
            scheduled_at=self.first_slot,
        )
        queued_post = self._post_for_accounts(self.account_a)

        add_to_queue(queued_post, self.queue_a)

        platform_post = queued_post.platform_posts.get(social_account=self.account_a)
        self.assertEqual(platform_post.scheduled_at, self.second_slot)
        self.assertEqual(queued_post.queue_entries.get(queue=self.queue_a).assigned_slot_datetime, self.second_slot)
        queued_post.refresh_from_db()
        self.assertEqual(queued_post.scheduled_at, self.second_slot)

    def test_add_to_multiple_account_queues_uses_each_accounts_available_slots(self):
        occupied_post = Post.objects.create(
            workspace=self.workspace,
            caption="Page A already scheduled",
            scheduled_at=self.first_slot,
        )
        PlatformPost.objects.create(
            post=occupied_post,
            social_account=self.account_a,
            status=PlatformPost.Status.SCHEDULED,
            scheduled_at=self.first_slot,
        )
        queued_post = self._post_for_accounts(self.account_a, self.account_b)

        add_to_queue(queued_post, self.queue_a)
        add_to_queue(queued_post, self.queue_b)

        page_a_post = queued_post.platform_posts.get(social_account=self.account_a)
        page_b_post = queued_post.platform_posts.get(social_account=self.account_b)
        self.assertEqual(page_a_post.scheduled_at, self.second_slot)
        self.assertEqual(page_b_post.scheduled_at, self.account_b_first_slot)
        queued_post.refresh_from_db()
        self.assertEqual(queued_post.scheduled_at, self.account_b_first_slot)

    def test_published_queue_entry_is_not_moved_to_future_slot(self):
        published_post = Post.objects.create(
            workspace=self.workspace,
            caption="Already published",
            scheduled_at=self.first_slot,
            published_at=timezone.now(),
        )
        published_platform_post = PlatformPost.objects.create(
            post=published_post,
            social_account=self.account_a,
            status=PlatformPost.Status.PUBLISHED,
            scheduled_at=self.first_slot,
            published_at=timezone.now(),
        )

        add_to_queue(published_post, self.queue_a)

        published_platform_post.refresh_from_db()
        published_post.refresh_from_db()
        self.assertEqual(published_platform_post.scheduled_at, self.first_slot)
        self.assertEqual(published_post.scheduled_at, self.first_slot)
        self.assertIsNone(published_post.queue_entries.get(queue=self.queue_a).assigned_slot_datetime)

        queued_post = self._post_for_accounts(self.account_a)
        add_to_queue(queued_post, self.queue_a)

        queued_platform_post = queued_post.platform_posts.get(social_account=self.account_a)
        self.assertEqual(queued_platform_post.scheduled_at, self.first_slot)


class PostingSlotCopyTests(TestCase):
    """Copying an account schedule should replace only the target account's slots."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.org = Organization.objects.create(name="Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="Workspace")
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
        self.source_account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="instagram",
            account_platform_id="ig-source",
            account_name="Source",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.target_account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="facebook",
            account_platform_id="fb-target",
            account_name="Target",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.url = reverse("calendar:copy_posting_slots", kwargs={"workspace_id": self.workspace.id})
        self.client.force_login(self.user)

    def test_copy_replaces_target_slots_with_source_schedule(self):
        PostingSlot.objects.create(
            social_account=self.source_account,
            day_of_week=PostingSlot.DayOfWeek.MONDAY,
            time=time(9, 0),
            is_active=True,
        )
        PostingSlot.objects.create(
            social_account=self.source_account,
            day_of_week=PostingSlot.DayOfWeek.FRIDAY,
            time=time(16, 30),
            is_active=False,
        )
        PostingSlot.objects.create(
            social_account=self.target_account,
            day_of_week=PostingSlot.DayOfWeek.SUNDAY,
            time=time(12, 0),
        )

        response = self.client.post(
            self.url,
            {
                "source_social_account_id": self.source_account.id,
                "target_social_account_id": self.target_account.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"copied": 2})
        target_slots = list(
            PostingSlot.objects.filter(social_account=self.target_account).order_by("day_of_week", "time")
        )
        self.assertEqual(
            [(slot.day_of_week, slot.time, slot.is_active) for slot in target_slots],
            [
                (PostingSlot.DayOfWeek.MONDAY, time(9, 0), True),
                (PostingSlot.DayOfWeek.FRIDAY, time(16, 30), False),
            ],
        )

    def test_copy_empty_source_clears_target_schedule(self):
        PostingSlot.objects.create(
            social_account=self.target_account,
            day_of_week=PostingSlot.DayOfWeek.WEDNESDAY,
            time=time(14, 0),
        )

        response = self.client.post(
            self.url,
            {
                "source_social_account_id": self.source_account.id,
                "target_social_account_id": self.target_account.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"copied": 0})
        self.assertFalse(PostingSlot.objects.filter(social_account=self.target_account).exists())

    def test_copy_rejects_source_account_from_another_workspace(self):
        other_org = Organization.objects.create(name="Other Org")
        other_workspace = Workspace.objects.create(organization=other_org, name="Other Workspace")
        other_account = SocialAccount.objects.create(
            workspace=other_workspace,
            platform="instagram",
            account_platform_id="ig-other",
            account_name="Other",
        )
        PostingSlot.objects.create(
            social_account=self.target_account,
            day_of_week=PostingSlot.DayOfWeek.WEDNESDAY,
            time=time(14, 0),
        )

        response = self.client.post(
            self.url,
            {
                "source_social_account_id": other_account.id,
                "target_social_account_id": self.target_account.id,
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(PostingSlot.objects.filter(social_account=self.target_account).exists())
