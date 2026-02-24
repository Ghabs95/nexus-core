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

## Dynamic Hot-Reload

Nexus Core supports loading and updating plugins from a directory at runtime without restarting services. This is useful for rapid development and production environments where downtime must be minimized.

### Requirements

Hot-reload requires the `watchdog` package:

```bash
pip install nexus-core[hotreload]
```

### Usage

The `HotReloadWatcher` monitors a directory for `.py` file changes and reloads matching plugins into the `PluginRegistry`.

```python
from nexus.plugins import PluginRegistry
from nexus.plugins.plugin_runtime import HotReloadWatcher

registry = PluginRegistry()
watcher = HotReloadWatcher(registry, watch_dir="/path/to/plugins")

# Start the background watcher thread
watcher.start()

# Stop the watcher when finished
watcher.stop()
```

### Plugin File Format

For a file in the watched directory to be loaded, it must expose one of the following:

1.  A function named `register_plugins(registry)` (the `RegistryContributor` protocol).
2.  The module itself is a callable that accepts a `PluginRegistry`.

**Example `my_plugin.py`:**

```python
from nexus.plugins import PluginKind

def register_plugins(registry):
    registry.register_factory(
        kind=PluginKind.AI_PROVIDER,
        name="custom-provider",
        version="1.0.0",
        factory=lambda config: MyProvider(**config),
        force=True  # Ensure we can overwrite during hot-reload
    )
```

### Isolation

Each reload uses a fresh module object created via `importlib.util.spec_from_file_location` and `module_from_spec`. This ensures that stale references do not leak into `sys.modules`.

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
