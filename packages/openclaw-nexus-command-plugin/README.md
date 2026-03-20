# OpenClaw Nexus Command Plugin

This package forwards `/nexus` commands from OpenClaw to the Nexus ARC HTTP command bridge.

It now matches OpenClaw's actual plugin contract:

- `openclaw.plugin.json` manifest with inline JSON Schema
- `package.json` `openclaw.extensions` entry
- `api.registerCommand(...)` command registration
- config loaded from `plugins.entries.nexus-arc-command.config`

Recommended plugin config:

```json5
{
  plugins: {
    entries: {
      "nexus-arc-command": {
        enabled: true,
        config: {
          bridgeUrl: "http://127.0.0.1:8091",
          authToken: "replace-me",
          timeoutMs: 15000,
          sourcePlatform: "openclaw"
        }
      }
    }
  }
}
```

Install locally during development:

```bash
openclaw plugins install ./packages/openclaw-nexus-command-plugin
openclaw plugins enable nexus-arc-command
openclaw config validate
```

Examples:

- `/nexus status demo`
- `/nexus plan demo#42`
- `/nexus implement demo#42`
- `/nexus pause demo#42`
- `/nexus resume demo#42`
- `/nexus stop demo#42`
- `/nexus logs demo#42`
- `/nexus show me the workflow state for demo#42`
