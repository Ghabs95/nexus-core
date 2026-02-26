# Storage Backends

The system supports two storage backends, controlled by `NEXUS_STORAGE_BACKEND`:

| Value        | Description                                |
|--------------|--------------------------------------------|
| `filesystem` | JSON files on disk (default, simplest)     |
| `postgres`   | PostgreSQL database (production, scalable) |

> The `both` option was removed — if you had it configured, it silently maps to `postgres`.

## Configuration

```bash
# Primary backend (controls all sub-backends by default)
NEXUS_STORAGE_BACKEND=postgres    # or "filesystem"

# Override per subsystem (optional — defaults to NEXUS_STORAGE_BACKEND)
NEXUS_WORKFLOW_BACKEND=postgres
NEXUS_INBOX_BACKEND=postgres

# PostgreSQL connection (required when using postgres)
NEXUS_STORAGE_DSN=postgresql://user:pass@localhost:5432/nexus
```

## Database Tables (Postgres)

When `NEXUS_STORAGE_BACKEND=postgres`, the following tables are auto-created:

### `nexus_workflows` — Workflow state machine

| Column         | Type      | Description                                 |
|----------------|-----------|---------------------------------------------|
| `id`           | PK string | Workflow ID (`project-issue-type`)          |
| `name`         | string    | Human-readable name                         |
| `state`        | string    | `pending`, `running`, `paused`, `completed` |
| `current_step` | int       | Index of active step                        |
| `data`         | JSON text | Full serialized workflow                    |
| `created_at`   | timestamp |                                             |

### `nexus_completions` — Agent completion summaries

| Column         | Type          | Description                          |
|----------------|---------------|--------------------------------------|
| `id`           | PK int        | Auto-increment                       |
| `issue_number` | string        | GitHub issue number                  |
| `agent_type`   | string        | Agent that completed                 |
| `status`       | string        | `complete`, `in-progress`, `blocked` |
| `summary_text` | text          | One-line summary                     |
| `data`         | JSON text     | Full payload                         |
| `dedup_key`    | unique string | `issue:agent:status`                 |
| `created_at`   | timestamp     |                                      |

### `nexus_host_state` — Key-value host runtime state

| Column       | Type      | Description                                          |
|--------------|-----------|------------------------------------------------------|
| `key`        | PK string | State key (e.g. `launched_agents`, `tracked_issues`) |
| `data`       | JSON text | Serialized state blob                                |
| `updated_at` | timestamp | Auto-updated                                         |

### `nexus_task_files` — Task markdown files

| Column         | Type      | Description           |
|----------------|-----------|-----------------------|
| `id`           | PK int    | Auto-increment        |
| `project`      | string    | Project key           |
| `issue_number` | string    | Optional issue number |
| `filename`     | string    | Original filename     |
| `content`      | text      | Markdown content      |
| `state`        | string    | `active` or `closed`  |
| `created_at`   | timestamp |                       |
| `updated_at`   | timestamp |                       |

### `nexus_inbox_queue` — Inbox task queue

| Column             | Type   | Description                     |
|--------------------|--------|---------------------------------|
| `id`               | PK int | Auto-increment                  |
| `project_key`      | string | Target project                  |
| `workspace`        | string | Workspace path                  |
| `filename`         | string | Task filename                   |
| `markdown_content` | text   | Full task markdown              |
| `status`           | string | `pending`, `processing`, `done` |

## Filesystem Layout

When `NEXUS_STORAGE_BACKEND=filesystem`:

```
.nexus/
├── state/
│   ├── launched_agents.json    → nexus_host_state (key: launched_agents)
│   └── tracked_issues.json    → nexus_host_state (key: tracked_issues)
├── tasks/
│   └── {project}/
│       ├── active/            → nexus_task_files (state: active)
│       ├── closed/            → nexus_task_files (state: closed)
│       └── completions/       → nexus_completions
│           └── completion_summary_{issue}.json
└── inbox/
    └── {project}/             → nexus_inbox_queue
        └── task_{id}.md
```

## How Routing Works

Each subsystem checks the backend at runtime:

- **Completions**: `CompletionStore` facade routes `save()`/`scan()` to filesystem or postgres
- **Host State**: `HostStateManager._load_json_state()`/`_save_json_state()` checks `NEXUS_STORAGE_BACKEND`
- **Inbox**: `inbox_routing_handler.py` uses `get_inbox_storage_backend()` to decide
- **Workflows**: Uses `NEXUS_WORKFLOW_BACKEND` via the workflow state plugin

Fallback: if postgres is unavailable, host state and completions fall back to filesystem with a warning.
