# Inbox Routing

Inbox routing is how tasks submitted by users (via Telegram, voice notes, or text) get dispatched to the correct project workspace for processing.

## Flow

```
User Input → AI Classification → Project Routing → Save Task → Process
```

1. User sends a message or voice note to the Telegram bot
2. The bot transcribes (if voice) and analyzes the content
3. The AI classifies the task: project, type, and priority
4. The routing handler saves the task to the appropriate inbox
5. The inbox processor picks it up and creates a GitHub issue

## Routing Modes

Controlled by `NEXUS_INBOX_BACKEND`:

| Backend | Behavior |
|---|---|
| `filesystem` | Writes `task_{id}.md` to `.nexus/inbox/{project}/` |
| `postgres` | Enqueues into `nexus_inbox_queue` table |

## Task Markdown Format

Each task is saved as a markdown file with consistent structure:

```markdown
## Task: <task_name>

- **Type:** feature | bugfix | chore | hotfix
- **Project:** <project_key>
- **Priority:** normal

### Description

<User's refined task description>

### Raw Input

<Original user text, unmodified>
```

## Project Resolution

1. The AI analyzes the task text to suggest a project
2. If the project is unambiguous → auto-route
3. If ambiguous → present the user with an inline keyboard to pick
4. Once selected → `save_resolved_task()` persists the task

## Key Functions

| Function | File | Purpose |
|---|---|---|
| `process_inbox_task()` | `inbox_routing_handler.py` | Route a new task to its project |
| `save_resolved_task()` | `inbox_routing_handler.py` | Save a task after user clarifies the project |
| `normalize_project_key()` | `common_routing.py` | Map aliases → canonical project keys |
| `get_inbox_dir()` | `config.py` | Compute filesystem path for a project's inbox |
| `enqueue_task()` | `integrations/inbox_queue.py` | Insert into postgres queue |

## Project Configuration

Projects are mapped in `config/project_config.yaml`:

```yaml
my_project:
  workspace: "../my-project"
  agents_dir: "../my-agents/agents"
  git_repo: "org/my-project"
  git_repos:
    - "org/my-project"
    - "org/my-shared-lib"
```

The routing handler uses `PROJECT_CONFIG` from `config.py` to look up workspace paths and determine where to save inbox files.
