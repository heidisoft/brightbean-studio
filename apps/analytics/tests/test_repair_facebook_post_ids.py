from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.composer.models import PlatformPost, Post
from apps.organizations.models import Organization
from apps.publisher.models import PublishLog
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


@pytest.fixture
def facebook_account() -> SocialAccount:
    org = Organization.objects.create(name="Analytics Repair Org")
    workspace = Workspace.objects.create(organization=org, name="Analytics Repair Workspace")
    return SocialAccount.objects.create(
        workspace=workspace,
        platform="facebook",
        account_platform_id="123",
        account_name="Facebook Page",
        oauth_access_token="page-token",
    )


def _platform_post(account: SocialAccount, *, platform_post_id: str, platform_extra=None) -> PlatformPost:
    post = Post.objects.create(workspace=account.workspace, caption="hello")
    return PlatformPost.objects.create(
        post=post,
        social_account=account,
        status=PlatformPost.Status.PUBLISHED,
        platform_post_id=platform_post_id,
        platform_extra=platform_extra or {},
        published_at=timezone.now(),
    )


@pytest.mark.django_db
def test_repair_facebook_post_ids_dry_run_does_not_persist_publish_log_id(facebook_account):
    platform_post = _platform_post(facebook_account, platform_post_id="1668168861075953")
    PublishLog.objects.create(
        platform_post=platform_post,
        attempt_number=1,
        status_code=200,
        response_body='{"id": "1668168861075953", "post_id": "123_456"}',
    )

    out = StringIO()
    call_command("repair_facebook_post_ids", stdout=out)

    platform_post.refresh_from_db()
    assert platform_post.platform_post_id == "1668168861075953"
    assert "DRY" in out.getvalue()
    assert "'1668168861075953' -> '123_456'" in out.getvalue()


@pytest.mark.django_db
def test_repair_facebook_post_ids_applies_publish_log_id(facebook_account):
    platform_post = _platform_post(facebook_account, platform_post_id="1668168861075953")
    PublishLog.objects.create(
        platform_post=platform_post,
        attempt_number=1,
        status_code=200,
        response_body="{'id': '1668168861075953', 'post_id': '123_456'}",
    )

    call_command("repair_facebook_post_ids", "--apply", stdout=StringIO())

    platform_post.refresh_from_db()
    assert platform_post.platform_post_id == "123_456"
    assert platform_post.platform_extra["facebook_upstream_ids"] == {
        "post_id": "123_456",
        "feed_post_id": "123_456",
        "response_id": "1668168861075953",
        "insights_post_id": "123_456",
    }


@pytest.mark.django_db
def test_repair_facebook_post_ids_applies_existing_platform_extra_id(facebook_account):
    platform_post = _platform_post(
        facebook_account,
        platform_post_id="1668168861075953",
        platform_extra={"facebook_upstream_ids": {"feed_post_id": "123_789", "video_id": "1668168861075953"}},
    )

    call_command("repair_facebook_post_ids", "--apply", stdout=StringIO())

    platform_post.refresh_from_db()
    assert platform_post.platform_post_id == "123_789"
    assert platform_post.platform_extra["facebook_upstream_ids"] == {
        "feed_post_id": "123_789",
        "video_id": "1668168861075953",
        "insights_post_id": "123_789",
    }
