# Architecture Diagrams

This document gives a diagram-first view of BrightBean Studio. It covers the
major runtime surfaces, background workers, data ownership, social integrations,
and publishing flows. For a deeper table-level ER view, see
[`db-architecture.md`](./db-architecture.md).

## System Context

```mermaid
flowchart TD
  User[Studio user] --> Web[Django web app]
  Client[Client portal user] --> Web
  Agent[External agent or automation] --> API[Agent API and MCP]

  Web --> DB[(PostgreSQL)]
  API --> DB
  Worker[Background worker] --> DB

  Web --> Storage[Media storage<br/>local or S3-compatible]
  Worker --> Storage

  Web --> Providers[Social providers]
  Worker --> Providers
  Providers --> Facebook[Facebook]
  Providers --> Instagram[Instagram]
  Providers --> LinkedIn[LinkedIn]
  Providers --> TikTok[TikTok]
  Providers --> YouTube[YouTube]
  Providers --> Pinterest[Pinterest]
  Providers --> Threads[Threads]
  Providers --> Bluesky[Bluesky]
  Providers --> Mastodon[Mastodon]
  Providers --> GoogleBusiness[Google Business]

  Providers --> InboxWebhooks[Platform webhooks]
  InboxWebhooks --> Web

  Web --> Email[Email provider or SMTP]
  Worker --> Email

  Intelligence[Intelligence service<br/>optional] <--> Web
```

## Deployment Topology

The app runs best as at least two processes: one web process and one background
worker process. PostgreSQL is both the primary data store and the job queue for
`django-background-tasks`.

```mermaid
flowchart TD
  Browser[Browser] --> Proxy[Platform router / Caddy / Render / Railway]
  Proxy --> Web[Web process<br/>gunicorn config.wsgi]

  Web --> Postgres[(PostgreSQL)]
  Worker[Worker process<br/>python manage.py process_tasks] --> Postgres
  Maintenance[Optional maintenance worker<br/>cleanup, sessions, media] --> Postgres

  Web --> Static[Static files<br/>WhiteNoise]
  Web --> Media[Media storage]
  Worker --> Media

  Worker --> SocialAPIs[Social platform APIs]
  Web --> SocialAPIs
```

Common service commands:

| Service            | Command                                                 | Purpose                                                                |
| ------------------ | ------------------------------------------------------- | ---------------------------------------------------------------------- |
| Web                | `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT` | Serves the UI, Agent API, MCP endpoint, webhooks, and admin.           |
| Worker             | `python manage.py process_tasks`                        | Polls PostgreSQL-backed background tasks, including the publish cycle. |
| One-shot publisher | `python manage.py run_publisher --once`                 | Diagnostic command for one publish polling cycle.                      |
| Inbox worker       | `python manage.py run_inbox_sync`                       | Optional polling worker for inbox sync/checks.                         |

## Application Modules

```mermaid
flowchart LR
  subgraph Core
    Accounts[accounts]
    Organizations[organizations]
    Workspaces[workspaces]
    Members[members]
    Settings[settings_manager]
  end

  subgraph Publishing
    Composer[composer]
    Calendar[calendar]
    Approvals[approvals]
    Publisher[publisher]
    Providers[providers]
  end

  subgraph Channels
    Credentials[credentials]
    SocialAccounts[social_accounts]
    Inbox[inbox]
  end

  subgraph Assets
    MediaLibrary[media_library]
  end

  subgraph ExternalAccess
    ApiKeys[api_keys]
    AgentAPI[api]
    MCP[mcp]
  end

  subgraph Experience
    ClientPortal[client_portal]
    Notifications[notifications]
    Onboarding[onboarding]
    Intelligence[intelligence]
  end

  Organizations --> Workspaces
  Workspaces --> Members
  Workspaces --> Composer
  Workspaces --> Calendar
  Workspaces --> SocialAccounts
  Workspaces --> MediaLibrary
  Workspaces --> ApiKeys

  Credentials --> SocialAccounts
  SocialAccounts --> Composer
  Composer --> Approvals
  Composer --> Calendar
  Calendar --> Publisher
  Publisher --> Providers
  Providers --> SocialAccounts
  Providers --> Inbox

  AgentAPI --> Composer
  AgentAPI --> MediaLibrary
  MCP --> AgentAPI
  ClientPortal --> Approvals
  Notifications --> Accounts
```

## Web Request Flow

```mermaid
sequenceDiagram
  participant B as Browser
  participant D as Django URLs
  participant M as Middleware
  participant V as View
  participant DB as PostgreSQL
  participant T as Templates

  B->>D: GET/POST page or HTMX request
  D->>M: route through auth, TOS, RBAC, HTMX
  M->>V: attach user, workspace membership, permissions
  V->>DB: read/write scoped models
  DB-->>V: model data
  V->>T: render full page or partial
  T-->>B: HTML response
```

The frontend is mostly server-rendered Django templates. HTMX swaps partials for
composer actions, calendar updates, approvals, inbox views, and settings screens.
Alpine.js handles local interactions such as modals, dropdowns, and drag/drop.

## Data Ownership and Permissions

```mermaid
flowchart TD
  User[User] --> OrgMembership[OrgMembership]
  User --> WorkspaceMembership[WorkspaceMembership]

  OrgMembership --> Organization[Organization]
  WorkspaceMembership --> Workspace[Workspace]
  Organization --> Workspace

  WorkspaceMembership --> Role[Built-in role or CustomRole]
  Role --> Permissions[Effective permissions]

  Permissions --> ComposerActions[Create/edit/schedule posts]
  Permissions --> ChannelActions[Manage social accounts]
  Permissions --> MediaActions[Upload/use media]
  Permissions --> ApiKeyActions[Issue API keys]

  ApiKey[ApiKey] --> Workspace
  ApiKey --> AllowedAccounts[Allowed SocialAccounts]
  ApiKey --> KeyPermissions[Permission subset]
```

Data is scoped primarily by organization and workspace. API keys are additionally
restricted to one workspace and an explicit allowlist of connected social
accounts.

## Social Account Connection Flow

```mermaid
sequenceDiagram
  participant U as User
  participant Web as Django web app
  participant Cred as PlatformCredential/env
  participant P as Provider module
  participant S as Social platform
  participant DB as PostgreSQL

  U->>Web: Click connect platform
  Web->>Cred: Resolve app credentials
  Web->>P: Build OAuth URL
  P-->>U: Redirect to social platform
  U->>S: Approve app permissions
  S-->>Web: OAuth callback with code
  Web->>P: Exchange code for tokens
  P->>S: Token/profile/page requests
  S-->>P: token + profile/account data
  Web->>DB: Save SocialAccount with encrypted tokens
```

For Facebook, `SocialAccount.account_platform_id` stores the Page ID. During
publishing, the provider uses that value as `page_id`.

## Create, Schedule, Publish

```mermaid
flowchart TD
  A[Create post in UI/API/MCP] --> B[composer_post]
  B --> C[composer_platform_post per channel]
  B --> D[composer_post_media for attachments]

  C --> E{Needs approval?}
  E -->|yes| F[pending_review / pending_client]
  F --> G[ApprovalAction + comments]
  G --> H[approved]
  E -->|no| I[draft or scheduled]
  H --> I

  I -->|scheduled_at set| J[status = scheduled]
  J --> K[background task run_publish_cycle]
  K --> L[PublishEngine polls due PlatformPost]
  L --> M[status = publishing]
  M --> N[Provider publish_post]
  N -->|success| O[status = published]
  N -->|failure| P[PublishLog + retry]
  P -->|retry due| K
  P -->|max retries| Q[status = failed]
```

Key rule: the publisher does not publish `Post` rows directly. It publishes due
`PlatformPost` rows. This lets one base post target several social accounts,
with each channel having its own status, schedule, errors, and external post ID.

## Background Jobs

```mermaid
flowchart TD
  Worker[python manage.py process_tasks] --> TaskTable[(background_task tables)]

  TaskTable --> PublishCycle[run_publish_cycle<br/>every 15s]
  TaskTable --> HealthChecks[schedule_all_health_checks<br/>every 6h]
  TaskTable --> IdempotencySweep[sweep_idempotency<br/>scheduled interval]
  TaskTable --> Intelligence[Intelligence sync/tasks<br/>optional]
  TaskTable --> OrgDeletion[scheduled org deletion]
  TaskTable --> MediaCleanup[media cleanup tasks]

  PublishCycle --> Publisher[PublishEngine.poll_and_publish]
  HealthChecks --> SocialAccounts[Social account token/profile checks]
  IdempotencySweep --> ApiKeys[Agent API idempotency records]
```

The worker is a long-running process. It is not triggered by web requests and is
not a platform cron job. It polls PostgreSQL for due background tasks.

## Media Pipeline

```mermaid
flowchart TD
  Upload[Upload media] --> Validate[Validate type, size, quota]
  Validate --> Store[Store file locally or S3-compatible]
  Store --> Asset[media_library_media_asset]
  Asset --> Process[Optional image/video processing]
  Process --> Variants[processed_variants + thumbnails]

  Asset --> Attach[composer_post_media]
  Attach --> Publish[Publisher reads media attachments]
  Publish --> TempFiles[Temporary local files]
  Publish --> MediaURLs[Public or presigned media URLs]
  TempFiles --> Provider[Provider upload/publish]
  MediaURLs --> Provider
```

Providers use media differently. Some upload temporary files; some require
fetchable URLs. Production deployments using S3/R2 should ensure generated media
URLs are reachable by the target platform.

## Agent API and MCP

```mermaid
flowchart TD
  Agent[External agent] --> Auth[Bearer ApiKeyAuth]
  Auth --> RateLimit[HTTP and workspace rate limits]
  RateLimit --> Router{Surface}

  Router --> REST[REST-ish Ninja routers]
  Router --> MCP[MCP JSON-RPC endpoint]

  REST --> AccountsRouter[accounts]
  REST --> PostsRouter[posts]
  REST --> MediaRouter[media]
  REST --> MeRouter[me]

  MCP --> ToolsList[tools/list]
  MCP --> ToolsCall[tools/call]

  AccountsRouter --> DB[(PostgreSQL)]
  PostsRouter --> DB
  MediaRouter --> Storage[Media storage]
  ToolsCall --> DB

  Auth --> Audit[ApiKeyAuditLog]
  REST --> Audit
  MCP --> Audit
```

Both API styles use the same API key model, workspace scope, social-account
allowlist, rate limits, and audit log. MCP changes the wire protocol, not the
authorization model.

## Inbox and Webhooks

```mermaid
flowchart TD
  Platform[Social platform] --> Webhook[/webhooks/]
  Webhook --> Verify[Signature / verify token checks]
  Verify --> Dispatch[Platform-specific handler]
  Dispatch --> InboxMessage[inbox_message]

  Poller[run_inbox_sync optional worker] --> Provider[Provider get_messages]
  Provider --> InboxMessage

  InboxMessage --> Feed[Workspace inbox UI]
  Feed --> Reply[Reply composer]
  Reply --> ProviderReply[Provider reply_to_message]
  ProviderReply --> Platform
```

Some inbox events arrive by webhook, and some can be collected by the optional
polling sync worker. Replies go back through the same provider abstraction used
for publishing.

## Notifications and Approvals

```mermaid
flowchart TD
  Post[Post / PlatformPost] --> ApprovalAction[ApprovalAction]
  Post --> PostComment[PostComment]
  ApprovalAction --> Notification[Notification]
  PostComment --> Notification

  Notification --> Preference[NotificationPreference / quiet hours]
  Preference --> Delivery[NotificationDelivery]
  Delivery --> Email[Email]
  Delivery --> UI[Notification drawer]

  ReminderWorker[Approval reminder command/task] --> ApprovalReminder
  ApprovalReminder --> Notification
```

Approval state lives on `PlatformPost`; approval history and comments attach to
the base `Post`, optionally pointing at a specific `PlatformPost` when an action
targets one channel.

## Provider Abstraction

```mermaid
classDiagram
  class SocialProvider {
    <<abstract>>
    get_auth_url()
    exchange_code()
    refresh_token()
    get_profile()
    publish_post()
    publish_comment()
    get_post_metrics()
    get_account_metrics()
    get_messages()
    reply_to_message()
    revoke_token()
  }

  SocialProvider <|-- FacebookProvider
  SocialProvider <|-- InstagramProvider
  SocialProvider <|-- LinkedInProvider
  SocialProvider <|-- TikTokProvider
  SocialProvider <|-- YouTubeProvider
  SocialProvider <|-- PinterestProvider
  SocialProvider <|-- ThreadsProvider
  SocialProvider <|-- BlueskyProvider
  SocialProvider <|-- MastodonProvider
  SocialProvider <|-- GoogleBusinessProvider
```

The publisher, inbox, analytics, and connection flows call the provider
interface instead of each platform directly. Adding a platform should primarily
mean adding a provider module, registering it, and exposing it through the
platform enum and UI.

## End-to-End Publishing Sequence

```mermaid
sequenceDiagram
  participant U as User/API/MCP
  participant Web as Django app
  participant DB as PostgreSQL
  participant W as process_tasks worker
  participant PE as PublishEngine
  participant P as Provider
  participant S as Social platform

  U->>Web: Create or schedule post
  Web->>DB: Save Post, PlatformPost, PostMedia
  W->>DB: Poll background_task table
  W->>PE: run_publish_cycle
  PE->>DB: Find due PlatformPost rows
  PE->>DB: Mark scheduled rows as publishing
  PE->>DB: Read Post, media, SocialAccount, credentials
  PE->>P: publish_post(access_token, content)
  P->>S: Platform API request
  S-->>P: success or error
  P-->>PE: PublishResult or exception
  PE->>DB: Save PublishLog
  PE->>DB: Set published or schedule retry/failed
```
