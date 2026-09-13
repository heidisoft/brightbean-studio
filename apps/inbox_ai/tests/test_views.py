from unittest.mock import patch

from django.test import Client
from django.urls import reverse

from apps.composer.models import PlatformPost, Post
from apps.inbox.models import InboxMessage, InboxReply, InternalNote
from apps.inbox_ai.configuration import MAX_ARTICLE_CHARS
from apps.inbox_ai.context import build_context
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace


def url(message):
    return reverse("inbox_ai:generate", kwargs={"workspace_id": message.workspace_id, "message_id": message.id})


def test_generation_does_not_send_save_or_change_message(member_client, message):
    with patch("apps.inbox_ai.views.generate_reply", return_value="ලිපිය අනුව…") as generate:
        response = member_client.post(url(message), {"style": "factual", "article_text": "Full article"})
    assert response.status_code == 200
    assert response.json()["reply"] == "ලිපිය අනුව…"
    assert "no-store" in response["Cache-Control"]
    assert generate.call_args.args[0]["additional_article_text"] == "Full article"
    assert not InboxReply.objects.exists()
    message.refresh_from_db()
    assert message.status == "unread"


def test_workspace_isolation(member_client, message, organization):
    other = Workspace.objects.create(name="Other", organization=organization)
    message.workspace = other
    message.save(update_fields=["workspace"])
    with patch("apps.inbox_ai.views.generate_reply") as generate:
        response = member_client.post(url(message), {"style": "factual"})
    assert response.status_code == 403
    generate.assert_not_called()


def test_foreign_message_in_authorized_workspace_returns_404(member_client, message, organization, workspace):
    other = Workspace.objects.create(name="Other", organization=organization)
    request_url = url(message)
    message.workspace = other
    message.save(update_fields=["workspace"])
    with patch("apps.inbox_ai.views.generate_reply") as generate:
        response = member_client.post(request_url, {"style": "factual"})
    assert response.status_code == 404
    generate.assert_not_called()


def test_viewer_is_denied(member_client, message):
    WorkspaceMembership.objects.update(workspace_role="viewer")
    with patch("apps.inbox_ai.views.generate_reply") as generate:
        response = member_client.post(url(message), {"style": "factual"})
    assert response.status_code == 403
    generate.assert_not_called()


def test_login_post_and_csrf_required(client, member_client, message, user):
    client.logout()
    assert client.post(url(message), {"style": "factual"}).status_code == 302
    client.force_login(user)
    assert client.get(url(message)).status_code == 405
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(user)
    with patch("apps.inbox_ai.views.generate_reply") as generate:
        assert secure.post(url(message), {"style": "factual"}).status_code == 403
    generate.assert_not_called()


def test_validation_and_disabled_mode_never_call_provider(member_client, message, ai_settings):
    with patch("apps.inbox_ai.views.generate_reply") as generate:
        assert member_client.post(url(message), {"style": "injected prompt"}).status_code == 400
        assert (
            member_client.post(
                url(message), {"style": "factual", "article_text": "x" * (MAX_ARTICLE_CHARS + 1)}
            ).status_code
            == 400
        )
        ai_settings.INBOX_AI_ENABLED = False
        assert member_client.post(url(message), {"style": "factual"}).status_code == 404
    generate.assert_not_called()


def test_rate_limit(member_client, message):
    with patch("apps.inbox_ai.views.generate_reply", return_value="Hello") as generate:
        for _ in range(10):
            assert member_client.post(url(message), {"style": "friendly"}).status_code == 200
        response = member_client.post(url(message), {"style": "friendly"})
    assert response.status_code == 429
    assert response["Retry-After"] == "60"
    assert generate.call_count == 10


def test_context_uses_platform_article_and_excludes_private_material(message, user, workspace):
    post = Post.objects.create(workspace=workspace, title="Base title", caption="Base body", author=user)
    platform_post = PlatformPost.objects.create(
        post=post,
        social_account=message.social_account,
        platform_specific_title="Article",
        platform_specific_caption="Article facts",
    )
    message.related_post = platform_post
    message.save(update_fields=["related_post"])
    InboxReply.objects.create(inbox_message=message, body="Sent", status="sent")
    InboxReply.objects.create(inbox_message=message, body="Private draft")
    InternalNote.objects.create(inbox_message=message, author=user, body="Private internal note")
    context = build_context(message)
    assert context["linked_post"] == {"title": "Article", "article_text": "Article facts"}
    assert context["previous_account_replies"] == ["Sent"]
    assert "Private" not in str(context)
    assert "oauth" not in str(context)


def test_parent_article_fallback_and_foreign_context_rejected(message, user, workspace, organization):
    other = Workspace.objects.create(name="Other", organization=organization)
    post = Post.objects.create(workspace=workspace, title="Title", caption="Facts", author=user)
    PlatformPost.objects.create(post=post, social_account=message.social_account, platform_post_id="stored-post")
    parent = InboxMessage.objects.create(
        workspace=workspace,
        social_account=message.social_account,
        platform_message_id="parent",
        body="Parent comment",
        sender_name="Reader",
        received_at=message.received_at,
        extra={"post_id": "stored-post"},
    )
    message.parent_message = parent
    message.save(update_fields=["parent_message"])
    context = build_context(message)
    assert context["parent_messages"] == ["Parent comment"]
    assert context["linked_post"]["article_text"] == "Facts"
    post.workspace = other
    post.save(update_fields=["workspace"])
    assert build_context(message)["linked_post"] is None
    parent.workspace = other
    parent.save(update_fields=["workspace"])
    assert build_context(message)["parent_messages"] == []


def test_template_override_preserves_core_composer(member_client, message, ai_settings):
    detail = reverse("inbox:message_detail", kwargs={"workspace_id": message.workspace_id, "message_id": message.pk})
    response = member_client.get(detail, HTTP_HX_REQUEST="true")
    html = response.content.decode()
    assert response.status_code == 200
    assert "AI reply" in html and "Send Reply" in html and "Save as draft" in html
    assert "Funny / sarcastic" in html and "Article text (optional)" in html
    assert "test-placeholder" not in html and "Selected style:" not in html
    assert 'name="body"' in html
    response = member_client.get(detail)
    assert response.status_code == 200
    assert response.content.count(b"inbox_ai/reply.js") == 1
    ai_settings.INBOX_AI_ENABLED = False
    response = member_client.get(detail, HTTP_HX_REQUEST="true")
    assert b"AI reply" not in response.content
    assert b"Send Reply" in response.content
