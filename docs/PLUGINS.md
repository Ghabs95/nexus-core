# Plugin Architecture

Nexus Core supports plugin-driven integrations so product-specific components can live outside the framework.

## Goals

- Keep core workflow execution provider-agnostic
- Move deployment-specific integrations (Telegram, GitHub CLI usage, provider prompts) to plugins
- Enable independent versioning/release of integrations

## Extension Points

`nexus.plugins.PluginKind` defines the supported kinds:

- `ai_provider`
- `git_platform`
- `notification_channel`
- `storage_backend`
- `input_adapter`

A plugin is registered as a `PluginSpec` with:

- `kind`: plugin type
- `name`: normalized plugin name
- `version`: plugin implementation version
- `factory(config)`: returns the adapter/provider instance

## Core API

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

## Entry Point Discovery

Plugins can be discovered via setuptools entry points (`nexus_core.plugins`).

Entry-point objects can be either:

1. A callable taking `PluginRegistry`
2. An object implementing `register_plugins(registry)`

Example plugin package `pyproject.toml`:

```toml
[project.entry-points."nexus_core.plugins"]
github_integration = "nexus_github_plugin:register_plugins"
telegram_integration = "nexus_telegram_plugin:register_plugins"
```

## Migration Guidance

### Move into nexus-core (contracts only)

- Plugin contracts and registry
- Provider orchestration policies
- Retry/fallback/cooldown state logic

### Keep in plugins/adapters

- Telegram interaction flows
- GitHub/GitLab concrete API and CLI command behavior
- Provider-specific prompts and CLI command wiring

## Recommended Migration Sequence

1. Wrap existing integrations behind plugin registration in app code.
2. Keep behavior unchanged while switching call sites to `PluginRegistry` lookups.
3. Extract integrations to separate packages once interfaces stabilize.
4. Pin plugin versions per deployment.

## Backward Compatibility

- Existing adapter interfaces remain valid.
- Plugin registry is additive and optional.
- Apps can mix direct adapter construction with registry-based loading during migration.

## Built-in Plugins

- AI runtime orchestrator plugin: `nexus.plugins.builtin.ai_runtime_plugin`
    - Classes: `AIOrchestrator`, `AIProvider`
    - Registration entry: `register_plugins(registry)`
- Agent launch policy plugin: `nexus.plugins.builtin.agent_launch_policy_plugin`
    - Class: `AgentLaunchPolicyPlugin`
    - Registration entry: `register_plugins(registry)`
- GitHub issue creation plugin: `nexus.plugins.builtin.github_issue_plugin`
    - Class: `GitHubIssueCLIPlugin`
    - Registration entry: `register_plugins(registry)`
- GitHub workflow policy plugin: `nexus.plugins.builtin.github_workflow_policy_plugin`
    - Class: `GithubWorkflowPolicyPlugin`
    - Registration entry: `register_plugins(registry)`
- Telegram notification plugin: `nexus.plugins.builtin.telegram_notification_plugin`
    - Class: `TelegramNotificationPlugin`
    - Registration entry: `register_plugins(registry)`
- Telegram interactive client plugin: `nexus.plugins.builtin.telegram_interactive_plugin`
    - Class: `TelegramInteractivePlugin`
    - Registration entry: `register_plugins(registry)`
- Discord interactive client plugin: `nexus.plugins.builtin.discord_interactive_plugin`
    - Class: `DiscordInteractivePlugin`
    - Registration entry: `register_plugins(registry)`
- JSON state storage plugin: `nexus.plugins.builtin.json_state_plugin`
    - Class: `JsonStateStorePlugin`
    - Registration entry: `register_plugins(registry)`
- Runtime ops/process guard plugin: `nexus.plugins.builtin.runtime_ops_plugin`
    - Class: `RuntimeOpsPlugin`
    - Registration entry: `register_plugins(registry)`
- Workflow policy plugin: `nexus.plugins.builtin.workflow_policy_plugin`
    - Class: `WorkflowPolicyPlugin`
    - Registration entry: `register_plugins(registry)`
- Workflow state engine adapter plugin: `nexus.plugins.builtin.workflow_state_engine_plugin`
    - Class: `WorkflowStateEnginePlugin`
    - Registration entry: `register_plugins(registry)`
