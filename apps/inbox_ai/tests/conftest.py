from copy import deepcopy

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.inbox.models import InboxMessage
from apps.inbox_ai.configuration import APP_DIR, load_prompts
from apps.members.models import WorkspaceMembership
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


@pytest.fixture(autouse=True)
def ai_settings(settings):
    settings.INBOX_AI_ENABLED = True
    settings.INBOX_AI_PROVIDER = "openai"
    settings.INBOX_AI_API_KEY = "test-placeholder"
    settings.INBOX_AI_MODEL = "gpt-4.1-mini"
    settings.INBOX_AI_GEMINI_API_KEY = "test-placeholder"
    settings.INBOX_AI_GEMINI_MODEL = "gemini-2.5-pro"
    settings.INBOX_AI_PROMPTS = load_prompts(APP_DIR / "prompts.json")
    if "apps.inbox_ai" not in settings.INSTALLED_APPS:
        settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "apps.inbox_ai"]
    templates = deepcopy(settings.TEMPLATES)
    if APP_DIR / "templates" not in templates[0]["DIRS"]:
        templates[0]["DIRS"].insert(0, APP_DIR / "templates")
    settings.TEMPLATES = templates
    settings.ROOT_URLCONF = "apps.inbox_ai.tests.urls"
    cache.clear()
    yield settings
    cache.clear()


@pytest.fixture
def workspace(db, organization):
    return Workspace.objects.create(name="AI workspace", organization=organization)


@pytest.fixture
def account(workspace):
    return SocialAccount.objects.create(
        workspace=workspace, platform="facebook", account_platform_id="page-1", account_name="Page"
    )


@pytest.fixture
def message(account):
    return InboxMessage.objects.create(
        workspace=account.workspace,
        social_account=account,
        platform_message_id="message-1",
        sender_name="Reader",
        body="මේ ගැන තව විස්තර කියන්න පුළුවන්ද?",
        received_at=timezone.now(),
    )


@pytest.fixture
def member_client(client, workspace, org_owner):
    WorkspaceMembership.objects.create(user=org_owner, workspace=workspace, workspace_role="owner")
    client.force_login(org_owner)
    return client
