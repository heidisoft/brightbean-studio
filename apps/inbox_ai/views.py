"""Generate suggestions; publishing and draft persistence remain in the inbox."""

import time

from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.inbox.models import InboxMessage
from apps.members.decorators import require_permission

from .configuration import MAX_ARTICLE_CHARS
from .context import build_context
from .service import GenerationError, generate_reply


class GenerationForm(forms.Form):
    style = forms.CharField(max_length=40)
    article_text = forms.CharField(required=False, max_length=MAX_ARTICLE_CHARS)


@login_required
@require_permission("use_inbox")
@require_POST
@never_cache
def generate(request, workspace_id, message_id):
    if not settings.INBOX_AI_ENABLED:
        raise Http404
    message = get_object_or_404(
        InboxMessage.objects.select_related("social_account"),
        id=message_id,
        workspace_id=workspace_id,
        social_account__workspace_id=workspace_id,
    )
    form = GenerationForm(request.POST)
    if not form.is_valid():
        return JsonResponse(
            {"error": f"Choose a reply style and keep article text under {MAX_ARTICLE_CHARS:,} characters."}, status=400
        )
    style = next((s for s in settings.INBOX_AI_PROMPTS["styles"] if s["id"] == form.cleaned_data["style"]), None)
    if style is None:
        return JsonResponse({"error": "Unknown reply style. Refresh the inbox and try again."}, status=400)
    # Bound paid requests, independently of Studio's DEBUG/RATELIMIT_ENABLE.
    key = f"inbox-ai:{workspace_id}:{request.user.pk}:{int(time.time()) // 60}"
    cache.add(key, 0, timeout=120)
    if cache.incr(key) > 10:
        response = JsonResponse({"error": "Please wait a minute before generating more replies."}, status=429)
        response["Retry-After"] = "60"
        return response
    try:
        reply = generate_reply(build_context(message, form.cleaned_data["article_text"]), style)
    except GenerationError as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    return JsonResponse({"reply": reply})
