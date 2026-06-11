# Database Architecture

This document explains the core BrightBean Studio database shape for creating,
scheduling, and publishing posts to connected social channels. It is intentionally
focused on the posting and publishing path rather than every table in the app.

## Core Entity Relationship

```mermaid
erDiagram
  Organization ||--o{ Workspace : owns
  Organization ||--o{ PlatformCredential : configures
  Organization ||--o{ MediaAsset : shared_media

  Workspace ||--o{ SocialAccount : connects
  Workspace ||--o{ Post : contains
  Workspace ||--o{ MediaAsset : owns
  Workspace ||--o{ Queue : has
  Workspace ||--o{ ApiKey : issues

  SocialAccount ||--o{ PlatformPost : targets
  SocialAccount ||--o{ PostingSlot : schedules
  SocialAccount ||--o{ Queue : owns
  SocialAccount ||--o{ RateLimitState : tracks

  Post ||--o{ PlatformPost : expands_to
  Post ||--o{ PostMedia : attaches
  Post ||--o{ QueueEntry : queued_as

  MediaAsset ||--o{ PostMedia : used_by

  Queue ||--o{ QueueEntry : orders
  PlatformPost ||--o{ PublishLog : records
  ApiKey }o--o{ SocialAccount : allows
```

## Main Tables

| Model | Table | Purpose |
| --- | --- | --- |
| `Organization` | `organizations_organization` | Top-level owner for workspaces, credentials, and shared media. |
| `Workspace` | `workspaces_workspace` | Operational space where posts, connected accounts, queues, and API keys live. |
| `PlatformCredential` | `credentials_platform_credential` | Org-level app credentials for platforms, falling back to environment credentials when absent. |
| `SocialAccount` | `social_accounts_social_account` | A connected social channel, such as a Facebook Page, with encrypted OAuth tokens. |
| `Post` | `composer_post` | Shared/base content: caption, title, first comment, tags, and aggregate schedule/published timestamps. |
| `PlatformPost` | `composer_platform_post` | Per-channel post variant and the source of truth for editorial/publishing status. |
| `MediaAsset` | `media_library_media_asset` | Uploaded image/video/GIF/document asset for an organization or workspace. |
| `PostMedia` | `composer_post_media` | Ordered join table connecting media assets to a post. |
| `PostingSlot` | `calendar_posting_slot` | Recurring default publish time for a connected social account. |
| `Queue` | `calendar_queue` | Named queue for a workspace/social account, optionally tied to a content category. |
| `QueueEntry` | `calendar_queue_entry` | A post's position inside a queue and computed assigned slot datetime. |
| `PublishLog` | `publisher_publish_log` | One row per publish attempt, including retries and platform errors. |
| `RateLimitState` | `publisher_rate_limit_state` | Per-account/platform rate limit state learned from platform API responses. |
| `ApiKey` | `api_keys_api_key` | Scoped Agent API credential tied to one workspace and an allowlist of social accounts. |

## Publishing Workflow

```mermaid
flowchart TD
  A[User or Agent API creates content] --> B[composer_post]
  B --> C[composer_platform_post per selected SocialAccount]
  B --> D[composer_post_media for attached MediaAsset rows]

  C --> E{PlatformPost status}
  E -->|draft| F[Editable in composer/calendar]
  E -->|scheduled| G[scheduled_at set]

  G --> H[Worker runs python manage.py process_tasks]
  H --> I[run_publish_cycle background task]
  I --> J[PublishEngine polls due PlatformPost rows]
  J --> K[status: scheduled to publishing]
  K --> L[Resolve SocialAccount token and PlatformCredential]
  L --> M[Build platform payload from Post, PlatformPost, PostMedia]
  M --> N[Call platform provider]

  N -->|success| O[status: published, published_at set]
  N -->|failure| P[PublishLog row with error]
  P --> Q[retry_count and next_retry_at updated]
  Q -->|retry due| I
  Q -->|max retries| R[status: failed]
```

The important distinction is:

- `Post` is the shared content container.
- `PlatformPost` is the per-channel publish unit and owns the real status.
- `PublishLog` records every attempt, including failures and retries.

A single `Post` can have several `PlatformPost` rows. For example, one base
post can target Facebook, LinkedIn, and Instagram at different times. Each
channel can publish, fail, retry, or remain scheduled independently.

## Scheduling and Queueing

```mermaid
flowchart LR
  A[SocialAccount] --> B[PostingSlot]
  A --> C[Queue]
  C --> D[QueueEntry]
  D --> E[Post]
  D --> F[assigned_slot_datetime]
  F --> G[PlatformPost.scheduled_at]
```

Queues are convenience scheduling tools. A queue calculates the next available
slot for a post, then keeps the matching `PlatformPost.scheduled_at` in sync.
The publisher itself does not publish queue entries directly; it only polls due
`PlatformPost` rows.

## Facebook Publishing Path

For Facebook, the data path to check is:

```text
social_accounts_social_account
  -> composer_platform_post
  -> publisher_publish_log
```

The Facebook-specific fields that matter most are:

| Table | Field | Meaning |
| --- | --- | --- |
| `social_accounts_social_account` | `platform` | Must be `facebook`. |
| `social_accounts_social_account` | `account_platform_id` | Facebook Page ID used as `page_id` during publish. |
| `social_accounts_social_account` | `oauth_access_token` | Encrypted token used by the Facebook provider. |
| `social_accounts_social_account` | `connection_status` | Should be `connected` for scheduling/publishing. |
| `composer_platform_post` | `status` | Must be `scheduled` before the worker picks it up. |
| `composer_platform_post` | `scheduled_at` | Must be due, or null with a due `composer_post.scheduled_at` fallback. |
| `composer_platform_post` | `publish_error` | Last publish error copied onto the platform post. |
| `publisher_publish_log` | `error_message` | Detailed error from the publish attempt. |
| `publisher_publish_log` | `response_body` | Truncated platform API response body. |

The Facebook provider injects `page_id` from
`SocialAccount.account_platform_id`. Depending on the content type, it then
calls the Facebook Graph API page feed, photos, or videos endpoint.

```mermaid
flowchart TD
  A[Due Facebook PlatformPost] --> B[SocialAccount token]
  B --> C[account_platform_id becomes page_id]
  C --> D[FacebookProvider]
  D --> E{Post type}
  E -->|text or link| F[POST /PAGE_ID/feed]
  E -->|image| G[POST /PAGE_ID/photos]
  E -->|video| H[POST /PAGE_ID/videos]
  F --> I[PublishLog + PlatformPost status]
  G --> I
  H --> I
```

## Debugging Scheduled Posts

If a scheduled post does not publish, inspect in this order:

1. Confirm the worker process is running: `python manage.py process_tasks`.
2. Confirm a `composer_platform_post` row exists with `status = scheduled`.
3. Confirm `scheduled_at` is due, using UTC-aware timestamps.
4. Confirm the related `social_accounts_social_account.connection_status` is `connected`.
5. Check `publisher_publish_log` for the related `platform_post_id`.
6. For Facebook, check that the connected Page token has publish permission and
   `account_platform_id` is the Page ID.

No web request or cron trigger publishes scheduled posts directly. The long
running worker polls background tasks, and the publish cycle polls due
`PlatformPost` rows.
