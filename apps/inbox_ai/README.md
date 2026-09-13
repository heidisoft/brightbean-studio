# OpenAI inbox reply extension

Optional Django app for BrightBean Studio. Adds **AI reply** beside **Send Reply**.
Choose fact-based, funny/sarcastic, friendly, professional, short, or bullet-point
replies; choosing a style immediately generates an editable suggestion. **Use
reply** copies it to the existing composer, where the user can edit, save, or send.
Generation never sends a reply or creates a database record.

## Enable

Set these in your deployment environment (or local `.env`) and restart Studio:

```dotenv
INBOX_AI_ENABLED=true
OPENAI_API_KEY=your-server-side-openai-key
INBOX_AI_MODEL=gpt-4.1-mini
```

Run your usual static asset build and `collectstatic` during deployment. No new
Python dependency, migration, background worker, or external extension service is
needed. `httpx` is already a Studio dependency. The model is configurable and must
support the OpenAI Responses API. A missing key produces an actionable error in
the picker; it does not stop Studio from starting. Disabled by default.

## Prompts and languages

Defaults live in [`prompts.json`](prompts.json). To keep local customizations
outside the feature patch, copy it to persistent deployment storage and set:

```dotenv
INBOX_AI_PROMPTS_FILE=/absolute/path/to/inbox-reply-prompts.json
```

The JSON contains `system_prompt` and a `styles` list. Each style has a unique
`id` (lowercase letters, digits, underscores), `label`, `description`, and `prompt`.
You can replace styles or add more, up to 12. Restart after editing. Invalid
configuration fails at startup with a configuration error.

The system prompt instructs the model to follow the target message's language and
script: Sinhala → Sinhala, Tamil → Tamil, English → English, and natural matching
for mixed or romanized messages. This is model behavior, not a deterministic
translation guarantee; review wording and facts before sending. Fact-based means
grounded in the supplied article, not independently fact-checked on the web.

## Article and conversation context

The extension reads `InboxMessage.related_post` → `PlatformPost` → `Post`, using
the effective platform title/caption (including overrides). For older messages,
it also resolves `stored_post_id` / `post_id` from inbox metadata within the same
workspace/account. It can inherit the post from up to five parent comments.

The picker lets you paste the full article or extra background. This supplements
the linked Studio post. An external article URL alone does **not** provide the
article's contents; paste its text. The extension does not fetch arbitrary URLs.
When there is no linked post, pasted context still works; without either, the
prompt tells the model to acknowledge missing facts rather than invent them.

The request includes the target message (up to 6,000 characters), up to five
parent messages, eight recent sent account replies (2,000 characters each), the
linked title/caption (1,000 / 24,000 characters), and pasted article text (up to
24,000 characters, validated before calling OpenAI). Longer stored context is
truncated. Internal notes, unsent drafts, credentials, arbitrary metadata, and
sender profile fields are excluded. Pasted text is kept only in the current
browser component and generation request, not persisted by the extension.

Content is sent to OpenAI when a style is selected. The API key and prompts stay
server-side. Requests use `store: false`; this disables Responses storage, not all
provider-side retention. Input JSON is separated from trusted instructions and
the prompt treats article/message text as untrusted content. No tools are exposed
to the model. API errors are sanitized and no content or credentials are logged
by this app.

## Architecture and upstream upgrades

Studio currently uses Django templates + HTMX + Alpine.js and has no general
plugin registry. This app is a conventional optional Django extension, with its
own views, URLs, context adapter, API client, prompt config, template overrides,
static JavaScript, and tests. It has no models and does not patch Python methods
or modify inbox publishing/draft services.

Only three core integration points are required:

1. `config/settings/base.py`: the `INBOX_AI_ENABLED` stanza calls
   `apps.inbox_ai.configuration.configure`, installing the app and prepending
   its template directory when enabled.
2. `config/urls.py`: conditionally includes `apps.inbox_ai.urls` at
   `workspace/<uuid:workspace_id>/inbox/ai/`.
3. `templates/inbox/partials/_reply_composer.html`: an empty
   `{% block reply_extensions %}{% endblock %}` beside Send Reply.

The extension overrides only that block and the inbox feed/detail templates' `extra_head` blocks.
Django same-name template inheritance loads the upstream template as the parent,
so the extension does not carry a copy of the composer or base layout. Its JS is
registered before Alpine starts and works with HTMX detail-panel replacements.
Templates live under `apps/`, which is already scanned by the Tailwind build.

Keep this change in one commit/PR. After upgrading an upstream checkout,
cherry-pick the feature commit, retain your environment settings, rebuild assets,
and restart. If conflicts occur, reapply the three small hooks above and restore
`apps/inbox_ai/`. The `.env.example` additions and main README link are optional
documentation changes. Core compatibility points to check after an upgrade:
`replyText` Alpine state, the composer `form`/`textarea[name=body]`, inbox
`extra_head`, message/post model fields, and workspace permission middleware.

Set `INBOX_AI_ENABLED=false` and restart to remove the routes, button, assets,
and template overrides. There is no feature data to migrate or delete. To remove
the code entirely, remove the app directory and the three hooks.

## Access, limits, and failure behavior

Uses Studio session authentication, CSRF protection, and `use_inbox` permission,
matching the existing ability to prepare drafts. Sending still goes through
Studio's existing `reply_from_inbox` permission. Message, article, and parent
lookups are scoped to both workspace and social account.

Each user/workspace may make 10 generation requests per fixed minute, backed by
Django's default cache. A shared cache is necessary to make this limit aggregate
across web workers; with Studio's default local-memory cache it applies per
process. The client disables repeat generation while a request is running.
There are no automatic paid retries. OpenAI calls have a 20-second read timeout
and a 1,800-token output budget; incomplete/refused/empty output is rejected.
The browser times out after 25 seconds and aborts on panel destruction. A browser
abort cannot guarantee cancellation of a request already received by OpenAI.

Errors leave both the current composer text and any prior suggestion intact.
Generated content is assigned to textarea values, never rendered as HTML.

## Verification

```bash
pytest apps/inbox_ai/tests apps/inbox/tests
node --test apps/inbox_ai/tests/reply.test.cjs
ruff check apps/inbox_ai config/settings/base.py config/urls.py
ruff format --check apps/inbox_ai config/settings/base.py config/urls.py
```

Tests mock OpenAI: they do not spend API credits. They cover tenant/permission
boundaries, CSRF, input validation, rate limits, contextual article selection,
exclusion of private material, Unicode payloads, API errors, response validation,
and template inheritance. Also run the existing inbox suite with
`INBOX_AI_ENABLED=true` to verify the installed configuration. For a live check,
open a Sinhala comment, paste its article, select a style, review the suggestion,
and use/save it. Check naturalness with a Sinhala speaker; mocked tests cannot
evaluate model language quality.

API contract: [OpenAI Responses create reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create).
Default model: [GPT-4.1 mini documentation](https://developers.openai.com/api/docs/models/gpt-4.1-mini).
