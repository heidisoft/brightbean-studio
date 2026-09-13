from django import template
from django.conf import settings

from apps.inbox_ai.configuration import MAX_ARTICLE_CHARS

register = template.Library()


@register.inclusion_tag("inbox_ai/_button.html", takes_context=True)
def inbox_ai_button(context):
    request = context.get("request")
    membership = getattr(request, "workspace_membership", None)
    enabled = settings.INBOX_AI_ENABLED and membership and membership.effective_permissions.get("use_inbox", False)
    return {
        "enabled": enabled,
        "message": context.get("message"),
        "workspace": context.get("workspace"),
        "styles": settings.INBOX_AI_PROMPTS["styles"] if enabled else [],
        "article_limit": MAX_ARTICLE_CHARS,
    }
