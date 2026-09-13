"""Boot-time configuration, isolated from Studio's core settings."""

import json
import re
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

APP_DIR = Path(__file__).resolve().parent
MAX_ARTICLE_CHARS = 24000
MAX_MESSAGE_CHARS = 6000


def load_prompts(path):
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(config["system_prompt"], str) or not config["system_prompt"].strip():
            raise ValueError("Empty system prompt")
        styles = config["styles"]
        if not isinstance(styles, list) or not 1 <= len(styles) <= 12:
            raise ValueError("Expected 1–12 styles")
        ids = set()
        for style in styles:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", style["id"]) or style["id"] in ids:
                raise ValueError("Invalid or duplicate style ID")
            ids.add(style["id"])
            for key in ("label", "description", "prompt"):
                if not isinstance(style[key], str) or not style[key].strip():
                    raise ValueError("Empty style field")
        return config
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ImproperlyConfigured(
            "INBOX_AI_PROMPTS_FILE must contain a system_prompt and unique reply styles."
        ) from exc


def configure(namespace, env):
    namespace["INSTALLED_APPS"] = [*namespace["INSTALLED_APPS"], "apps.inbox_ai"]
    # Django's same-name template inheritance skips this override when resolving
    # its parent, so upstream composer/layout changes are inherited automatically.
    namespace["TEMPLATES"][0]["DIRS"].insert(0, APP_DIR / "templates")
    namespace["INBOX_AI_API_KEY"] = env("OPENAI_API_KEY", default="")
    namespace["INBOX_AI_MODEL"] = env("INBOX_AI_MODEL", default="gpt-4.1-mini")
    namespace["INBOX_AI_PROMPTS"] = load_prompts(env("INBOX_AI_PROMPTS_FILE", default=str(APP_DIR / "prompts.json")))
