"""Regression for the ``0002_inboxreply_draft_lifecycle`` data migration.

Before 0002 an ``InboxReply`` row existed only once a send had succeeded,
so the backfill must mark every pre-existing row ``sent`` (not the new
``draft`` default) and line its ``created_at`` up with the real send time.
"""

from __future__ import annotations

import importlib
from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.inbox.models import InboxMessage, InboxReply
from apps.social_accounts.models import SocialAccount

migration_module = importlib.import_module("apps.inbox.migrations.0002_inboxreply_draft_lifecycle")


@pytest.fixture
def workspace(db, organization):
    from apps.workspaces.models import Workspace

    return Workspace.objects.create(name="Mig WS", organization=organization)


@pytest.fixture
def message(db, workspace):
    account = SocialAccount.objects.create(
        workspace=workspace,
        platform="facebook",
        account_platform_id="page-1",
        account_name="Page",
        oauth_access_token="tok",
    )
    return InboxMessage.objects.create(
        workspace=workspace,
        social_account=account,
        platform_message_id="pm-1",
        message_type=InboxMessage.MessageType.COMMENT,
        sender_name="Ada",
        body="hi?",
        received_at=timezone.now() - timedelta(hours=2),
    )


@pytest.mark.django_db
def test_backfill_marks_existing_replies_sent(message):
    from django.apps import apps as global_apps

    sent_at = timezone.now() - timedelta(hours=1)
    reply = InboxReply.objects.create(inbox_message=message, body="delivered")
    # Simulate a pre-0002 row: it predates the status column and was only
    # ever written post-send.
    InboxReply.objects.filter(pk=reply.pk).update(
        status=InboxReply.Status.DRAFT, sent_at=sent_at, created_at=timezone.now()
    )

    migration_module._mark_existing_sent(global_apps, connection.schema_editor())

    reply.refresh_from_db()
    assert reply.status == InboxReply.Status.SENT
    assert reply.created_at == sent_at


@pytest.mark.django_db(transaction=True)
def test_upgrade_with_existing_replies_creates_status_index(message, user):
    """Run the whole migration, including schema_editor's deferred CREATE INDEX.

    Calling only the backfill misses PostgreSQL's pending-trigger failure when
    existing rows are updated before the new status index is created.
    """
    before = [("inbox", "0001_initial")]
    after = [("inbox", "0002_inboxreply_draft_lifecycle")]
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    executor.migrate(before)
    old_apps = executor.loader.project_state(before).apps
    old_reply_model = old_apps.get_model("inbox", "InboxReply")
    reply_ids = []
    sent_at = timezone.now() - timedelta(days=7)
    try:
        for number in range(2):
            reply = old_reply_model.objects.create(
                inbox_message_id=message.pk,
                author_id=user.pk,
                body=f"Already delivered {number}",
                platform_reply_id=f"platform-reply-{number}",
            )
            reply_ids.append(reply.pk)
        old_reply_model.objects.filter(pk__in=reply_ids).update(sent_at=sent_at)

        MigrationExecutor(connection).migrate(after)

        for number, reply_id in enumerate(reply_ids):
            reply = InboxReply.objects.get(pk=reply_id)
            assert reply.status == InboxReply.Status.SENT
            assert reply.created_at == reply.sent_at == sent_at
            assert reply.body == f"Already delivered {number}"
            assert reply.platform_reply_id == f"platform-reply-{number}"
            assert reply.author_id == user.pk
        with connection.cursor() as cursor:
            indexes = connection.introspection.get_constraints(cursor, "inbox_reply")
        assert any(index["index"] and index["columns"] == ["status"] for index in indexes.values())
        draft = InboxReply.objects.create(inbox_message=message, body="New draft")
        assert draft.status == InboxReply.Status.DRAFT
        assert draft.sent_at is None
        draft.delete()
    finally:
        # Restore the schema for the rest of the suite.
        old_reply_model.objects.filter(pk__in=reply_ids).delete()
        MigrationExecutor(connection).migrate(latest)
