# Quick Start Guide

## Installation

```bash
cd nexus-core
pip install -e .
```

## Run the Example

```bash
python examples/basic_workflow.py
```

This will:
1. Create a 3-step workflow (Triage → Design → Implementation)
2. Execute each step with simulated outputs
3. Show audit log of all events
4. Demonstrate pause/resume functionality
5. Store all state in `./data` directory

## Check the Results

After running, inspect the persisted data:

```bash
# View workflow state
cat data/workflows/demo-workflow-001.json | python -m json.tool

# View audit log
cat data/audit/demo-workflow-001.jsonl
```

## Next Steps

1. Check out the [main README](README.md) for overview and features
2. Review [Usage Guide](docs/USAGE.md) for detailed examples and integration patterns
3. See [Architecture](docs/ARCHITECTURE.md) for system design
4. Read [Comparison](docs/COMPARISON.md) to see how nexus-core differs from other frameworks

## Key Concepts

### Workflow
A multi-step process with state management, audit logging, and error handling.

### Agents
AI-powered workers that execute tasks (Copilot, GPT-4, Claude, Gemini, etc.)

### Adapters
- **Storage**: Where workflow state is stored (File, Postgres, Redis, S3)
- **Git**: Issue tracking platform (GitHub, GitLab, Bitbucket)
- **AI Providers**: Which AI tool executes agents (Copilot, OpenAI, Anthropic)
- **Notifications**: How users get updates (Telegram, Slack, Email)

### Orchestrator
Intelligently routes work to best AI provider with automatic fallback on failure.

## Architecture

```
Input → Workflow Engine → AI Orchestrator → Provider
  ↓            ↓              ↓                ↓
Storage    Audit Log     Fallback         Copilot/GPT-4
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed architecture diagrams.
