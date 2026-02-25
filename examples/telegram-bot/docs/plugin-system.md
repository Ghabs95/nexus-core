# Plugin System

Nexus Core is built on a plugin architecture that keeps the core workflow engine provider-agnostic. The telegram-bot example uses these plugins to wire up Telegram, GitHub, and AI integrations without modifying the framework.

## Plugin Kinds

| Kind | Purpose | Example |
|---|---|---|
| `ai_provider` | AI execution backends | Copilot CLI, Gemini CLI, OpenAI API |
| `git_platform` | Git hosting integration | GitHub, GitLab, Bitbucket |
| `notification_channel` | User notifications | Telegram, Slack, Discord |
| `storage_backend` | State persistence | File, PostgreSQL, Redis |
| `input_adapter` | Task input sources | Telegram, Webhook, CLI |

## How Plugins Work

A plugin is registered as a `PluginSpec` with:

- **kind**: One of the above kinds
- **name**: Normalized plugin name (e.g. `copilot-cli`)
- **version**: Implementation version
- **factory(config)**: Returns the adapter/provider instance

```python
from nexus.plugins import PluginKind, PluginRegistry

registry = PluginRegistry()

registry.register_factory(
    kind=PluginKind.AI_PROVIDER,
    name="copilot-cli",
    version="1.0.0",
    factory=lambda config: CopilotProvider(**config),
)

provider = registry.create(
    kind=PluginKind.AI_PROVIDER,
    name="copilot-cli",
    config={"cli_path": "copilot"},
)
```

## Built-in Plugins

| Plugin | Module | Purpose |
|---|---|---|
| **AI Runtime** | `ai_runtime_plugin` | Orchestrates AI provider selection with fallback |
| **Agent Launch Policy** | `agent_launch_policy_plugin` | Builds prompts and launch arguments per agent |
| **GitHub Issue** | `github_issue_plugin` | Creates and manages GitHub issues via `gh` CLI |
| **GitHub Workflow Policy** | `github_workflow_policy_plugin` | Enforces merge approval policies |
| **Telegram Notification** | `telegram_notification_plugin` | Sends updates via Telegram bot API |
| **Telegram Interactive** | `telegram_interactive_plugin` | Handles inline keyboards and callbacks |
| **Discord Interactive** | `discord_interactive_plugin` | Discord bot alternative |
| **JSON State Store** | `json_state_plugin` | Read/write JSON state files |
| **Runtime Ops** | `runtime_ops_plugin` | Process management, PID tracking |
| **Workflow Policy** | `workflow_policy_plugin` | Workflow type selection and enforcement |
| **Workflow State Engine** | `workflow_state_engine_plugin` | State machine persistence and transitions |

## Entry Point Discovery

Plugins can be published as separate packages and discovered via setuptools entry points:

```toml
# pyproject.toml of a custom plugin package
[project.entry-points."nexus_core.plugins"]
my_custom_adapter = "my_package:register_plugins"
```

## The Bot's Plugin Runtime

The telegram-bot uses `orchestration/plugin_runtime.py` as a plugin broker:

```python
from orchestration.plugin_runtime import get_profiled_plugin

# Get a cached plugin instance
state_plugin = get_profiled_plugin(
    "state_store_default",
    cache_key="state:json-store",
)

# Use it
data = state_plugin.load_json("path/to/state.json", default={})
state_plugin.save_json("path/to/state.json", data)
```

The `get_profiled_plugin()` function caches instances by `cache_key` to avoid re-initialization.

## Adding Custom Plugins

1. Implement the adapter/provider interface for your kind
2. Register it in a `register_plugins(registry)` function
3. Wire it up via entry points or explicit registration in your app startup
4. The framework handles selection, fallback, and lifecycle
