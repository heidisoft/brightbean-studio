"""Read only the conversation and article visible within the selected workspace."""

from apps.composer.models import PlatformPost
from apps.inbox.models import InboxMessage, InboxReply

from .configuration import MAX_ARTICLE_CHARS, MAX_MESSAGE_CHARS


def related_post(message):
    posts = PlatformPost.objects.select_related("post").filter(
        post__workspace_id=message.workspace_id, social_account_id=message.social_account_id
    )
    if message.related_post_id:
        return posts.filter(pk=message.related_post_id).first()
    # Older webhook messages may predate related_post linking during inbox sync.
    extra = message.extra if isinstance(message.extra, dict) else {}
    post_id = extra.get("stored_post_id") or extra.get("post_id")
    return posts.filter(platform_post_id=str(post_id)).first() if post_id else None


def build_context(message, article_text=""):
    parents = []
    current = message
    seen = {message.id}
    post = related_post(message)
    for _ in range(5):
        if not current.parent_message_id or current.parent_message_id in seen:
            break
        current = InboxMessage.objects.filter(
            pk=current.parent_message_id,
            workspace_id=message.workspace_id,
            social_account_id=message.social_account_id,
        ).first()
        if current is None:
            break
        seen.add(current.id)
        parents.append(current.body[:MAX_MESSAGE_CHARS])
        if post is None:
            post = related_post(current)
    sent = list(
        message.replies.filter(status=InboxReply.Status.SENT)
        .order_by("-sent_at", "-created_at")
        .values_list("body", flat=True)[:8]
    )
    return {
        "platform": message.social_account.platform,
        "target_message": message.body[:MAX_MESSAGE_CHARS],
        "parent_messages": list(reversed(parents)),
        "previous_account_replies": [body[:2000] for body in reversed(sent)],
        "linked_post": {
            "title": post.effective_title[:1000],
            "article_text": post.effective_caption[:MAX_ARTICLE_CHARS],
        }
        if post
        else None,
        "additional_article_text": article_text,
    }
