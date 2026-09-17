"""Small provider adapters using Studio's existing HTTP client dependency."""

import json
import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class GenerationError(Exception):
    """A safe, actionable error suitable for display in the inbox."""


def generate_reply(context, style):
    provider = getattr(settings, "INBOX_AI_PROVIDER", "openai")
    if provider == "gemini":
        return _generate_gemini(context, style)
    return _generate_openai(context, style)


def _instructions(style):
    return settings.INBOX_AI_PROMPTS["system_prompt"] + "\n\nSelected style:\n" + style["prompt"]


def _generate_openai(context, style):
    if not settings.INBOX_AI_API_KEY.strip():
        raise GenerationError("AI replies are not configured. Ask your administrator to set OPENAI_API_KEY.")
    try:
        response = httpx.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {settings.INBOX_AI_API_KEY}"},
            json={
                "model": settings.INBOX_AI_MODEL,
                "instructions": _instructions(style),
                "input": [{"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
                "max_output_tokens": 1800,
                "store": False,
            },
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=False,
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise GenerationError("Generation timed out. Please try again.") from exc
    except httpx.HTTPStatusError as exc:
        # Never log bodies, prompts, or Authorization headers.
        logger.warning("Inbox AI provider returned HTTP %s", exc.response.status_code)
        if exc.response.status_code == 429:
            raise GenerationError("OpenAI is busy or the API quota is exhausted. Please try again later.") from exc
        raise GenerationError(
            "OpenAI could not generate a reply. Ask your administrator to check the API key and model."
        ) from exc
    except httpx.RequestError as exc:
        raise GenerationError("Could not reach OpenAI. Please try again.") from exc
    try:
        data = response.json()
        if data.get("status") != "completed":
            raise ValueError("Incomplete response")
        parts = []
        for item in data["output"]:
            if item.get("type") != "message":
                continue
            for content in item["content"]:
                if content.get("type") == "refusal":
                    raise GenerationError(
                        "A reply could not be generated for this content. Try another style or write your reply."
                    )
                if content.get("type") == "output_text":
                    parts.append(content["text"])
        reply = "\n".join(parts).strip()
        if not reply or len(reply) > 12000:
            raise ValueError("Empty or oversized output")
        return reply
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise GenerationError("OpenAI returned an incomplete or unreadable reply. Please try again.") from exc


def _generate_gemini(context, style):
    api_key = settings.INBOX_AI_GEMINI_API_KEY.strip()
    if not api_key:
        raise GenerationError("AI replies are not configured. Ask your administrator to set GEMINI_API_KEY.")
    try:
        response = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{settings.INBOX_AI_GEMINI_MODEL}:generateContent",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "systemInstruction": {"parts": [{"text": _instructions(style)}]},
                "contents": [{"role": "user", "parts": [{"text": json.dumps(context, ensure_ascii=False)}]}],
                "generationConfig": {"maxOutputTokens": 1800},
            },
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=False,
        )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise GenerationError("Generation timed out. Please try again.") from exc
    except httpx.HTTPStatusError as exc:
        # Never log bodies, prompts, or API keys.
        logger.warning("Inbox AI provider returned HTTP %s", exc.response.status_code)
        if exc.response.status_code == 429:
            raise GenerationError("Gemini is busy or the API quota is exhausted. Please try again later.") from exc
        raise GenerationError(
            "Gemini could not generate a reply. Ask your administrator to check the API key and model."
        ) from exc
    except httpx.RequestError as exc:
        raise GenerationError("Could not reach Gemini. Please try again.") from exc
    try:
        data = response.json()
        if data.get("promptFeedback", {}).get("blockReason"):
            raise GenerationError(
                "A reply could not be generated for this content. Try another style or write your reply."
            )
        candidate = data["candidates"][0]
        if candidate.get("finishReason") in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST"):
            raise GenerationError(
                "A reply could not be generated for this content. Try another style or write your reply."
            )
        reply = "".join(part.get("text", "") for part in candidate["content"]["parts"]).strip()
        if not reply or len(reply) > 12000:
            raise ValueError("Empty or oversized output")
        return reply
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise GenerationError("Gemini returned an incomplete or unreadable reply. Please try again.") from exc
