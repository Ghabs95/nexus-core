# Configuration Reference

All configuration lives in environment variables (loaded from `.env`) and `config/project_config.yaml`.

## Environment Variables

### Core

| Variable           | Required | Default | Description                                |
|--------------------|----------|---------|--------------------------------------------|
| `TELEGRAM_TOKEN`   | ✅        | —       | Telegram Bot API token                     |
| `TELEGRAM_CHAT_ID` | ✅        | —       | Target Telegram chat ID                    |
| `ALLOWED_USER`     | ✅        | —       | Authorized Telegram user ID                |
| `AI_API_KEY`       | ✅        | —       | AI provider API key (Gemini, OpenAI, etc.) |

### Storage

| Variable                 | Required | Default         | Description                                               |
|--------------------------|----------|-----------------|-----------------------------------------------------------|
| `NEXUS_STORAGE_BACKEND`  | ❌        | `filesystem`    | Primary storage backend: `filesystem` or `postgres`       |
| `NEXUS_WORKFLOW_BACKEND` | ❌        | follows primary | Workflow state backend override                           |
| `NEXUS_INBOX_BACKEND`    | ❌        | follows primary | Inbox queue backend override                              |
| `NEXUS_STORAGE_DSN`      | 🟡       | —               | PostgreSQL connection string (required if using postgres) |

### Webhook Server

| Variable         | Required | Default | Description                                           |
|------------------|----------|---------|-------------------------------------------------------|
| `WEBHOOK_PORT`   | ❌        | `8081`  | Port for the webhook server                           |
| `WEBHOOK_SECRET` | ❌        | —       | GitHub webhook HMAC secret for signature verification |

### Project

| Variable                 | Required | Default                      | Description                           |
|--------------------------|----------|------------------------------|---------------------------------------|
| `PROJECT_CONFIG_PATH`    | ❌        | `config/project_config.yaml` | Path to project configuration file    |
| `BASE_DIR`               | ❌        | `.`                          | Base directory for project workspaces |
| `NEXUS_CORE_STORAGE_DIR` | ❌        | `.nexus`                     | Root directory for Nexus state files  |

### Agent Runtime

| Variable                        | Required | Default   | Description                           |
|---------------------------------|----------|-----------|---------------------------------------|
| `AGENT_RECENT_WINDOW`           | ❌        | `120`     | Seconds to retain launched agent PIDs |
| `AGENT_TIMEOUT`                 | ❌        | `3600`    | Default agent timeout in seconds      |
| `NEXUS_AGENT_TIMEOUT`           | ❌        | `3600`    | Core engine default timeout fallback  |
| `COPILOT_PROVIDER`              | ❌        | `copilot` | AI CLI provider binary name           |
| `NEXUS_FULL_WORKFLOW_CONTEXT`   | ❌        | `false`   | Set `true` to inject full step schema |
| `INBOX_CHECK_INTERVAL`          | ❌        | `10`      | Polling interval for completions      |
| `AUTO_CHAIN_CYCLE`              | ❌        | `60`      | Polling interval for auto-chaining    |

## Project Config (`project_config.yaml`)

```yaml
# Each key is a project identifier (used in inbox routing)
my_project:
  workspace: "../my-project"          # Relative path to workspace root
  agents_dir: "../my-agents/agents"   # Path to agent YAML definitions
  git_repo: "org/my-project"          # Primary GitHub repo (owner/name)
  git_repos: # Additional repos for multi-repo projects
    - "org/my-project"
    - "org/my-shared-lib"

another_project:
  workspace: "../another-project"
  agents_dir: "../another-agents/agents"
  git_repo: "org/another-project"

# Global settings
require_human_merge_approval: always  # always | workflow-based | never
```

## State Files (filesystem backend)

| File                   | Location        | Contents                            |
|------------------------|-----------------|-------------------------------------|
| `launched_agents.json` | `.nexus/state/` | Active agent PIDs with timestamps   |
| `tracked_issues.json`  | `.nexus/state/` | User-subscribed issue notifications |
| `audit.log`            | `logs/`         | Append-only event log               |
| `workflow_state.json`  | `.nexus/state/` | Pause/resume/stop state per issue   |

## Rate Limits

| Scope      | Limit        | Window        |
|------------|--------------|---------------|
| Global     | 30 req/min   | 60s sliding   |
| Logs       | 5 req/min    | 60s sliding   |
| Stats      | 10 req/min   | 60s sliding   |
| Direct     | 3 req/min    | 60s sliding   |
| GitHub API | 100 req/hour | 3600s sliding |

## Service Endpoints

| Endpoint               | Method | Description                    |
|------------------------|--------|--------------------------------|
| `/health`              | GET    | Health check                   |
| `/status`              | GET    | Detailed system status         |
| `/metrics`             | GET    | Prometheus-compatible metrics  |
| `/webhook`             | POST   | GitHub webhook receiver        |
| `/api/v1/completion`   | POST   | Agent completion reporting     |
| `/visualizer`          | GET    | Workflow visualizer UI         |
| `/visualizer/snapshot` | GET    | Workflow state snapshot (JSON) |
