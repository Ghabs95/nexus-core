# Nexus Core

**Production-grade framework for orchestrating AI agents in multi-step workflows**

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)

---

## What is Nexus Core?

Nexus Core is the **Git-native AI orchestration framework**. Unlike other frameworks that log agent actions to ephemeral files, Nexus creates permanent, traceable artifacts in your Git platform (GitHub, GitLab, Bitbucket).

### Why Git-Native?

Every agent action becomes part of your development history:
- 🎯 **Issues** track what was requested and decided
- 💬 **Comments** preserve agent reasoning and handoffs
- 🔀 **Pull Requests** contain actual code changes
- ✅ **Reviews** create approval gates with full context
- 📊 **Git History** provides permanent audit trail

**The result:** Complete traceability, searchability, and accountability for AI workflows.

### Production-Ready Features

- ✅ **Reliability**: Auto-retry, timeout detection, graceful failure handling
- ✅ **State Management**: Persistent workflow state with audit trails
- ✅ **AI Orchestration**: Route work to the best AI tool (Copilot, GPT-4, Claude, Gemini, local models)
- ✅ **Fallback Support**: Automatic failover when tools are rate-limited or unavailable
- ✅ **Pluggable Architecture**: Bring your own storage, git platform, notification system

**Think of it as Temporal meets GitHub Actions for AI agents** — workflows that integrate seamlessly with your development process.

> 📖 **Documentation:**
> - [Usage Guide & Examples](docs/USAGE.md) - How to use nexus-core in your project
> - [Comparison with Google ADK, LangChain, and others](docs/COMPARISON.md)
> - [Positioning & Messaging](docs/POSITIONING.md)

---

## Quick Start

### Installation

```bash
pip install nexus-core

# With optional adapters
pip install nexus-core[telegram,postgres,openai]
```

### Your First Workflow

```python
from nexus import WorkflowEngine, FileStorage, GitHubPlatform
from nexus.adapters.ai import OpenAIProvider

# Configure adapters
storage = FileStorage(base_path="./data")
git = GitHubPlatform(repo="yourorg/yourrepo", token="ghp_...")
ai = OpenAIProvider(api_key="sk-...")

# Create workflow engine
engine = WorkflowEngine(
    storage=storage,
    git_platform=git,
    ai_provider=ai
)

# Load workflow definition
workflow = engine.load_workflow("./workflows/feature_dev.yaml")

# Execute
result = await engine.run(
    workflow=workflow,
    inputs={"issue_url": "https://github.com/yourorg/repo/issues/42"}
)
```

### Define a Workflow (YAML)

```yaml
name: "Feature Development"
version: "1.0"

steps:
  - name: triage
    agent: ProjectLead
    prompt: "Analyze this feature request and determine complexity"
    timeout: 300
    retry: 3
    
  - name: design
    agent: Architect
    prompt: "Create technical design for this feature"
    condition: "triage.complexity == 'high'"
    timeout: 600
    retry: 2
    
  - name: implement
    agent: Developer
    prompt: "Implement the feature according to spec"
    timeout: 1800
    retry: 3
```

---

## Features

### 🔄 Workflow State Machine

Track multi-step processes with automatic state persistence:

```python
# Workflows can be paused, resumed, or stopped
await engine.pause_workflow(workflow_id)
await engine.resume_workflow(workflow_id)

# Full audit trail
history = await engine.get_audit_log(workflow_id)
```

### 🤖 AI Provider Orchestration

Intelligent routing with automatic fallback:

```python
from nexus.adapters.ai import CopilotCLI, OpenAIProvider, GeminiCLI

orchestrator = AIOrchestrator(
    providers=[
        CopilotCLI(preference="code_generation"),
        OpenAIProvider(preference="reasoning", model="gpt-4"),
        GeminiCLI(preference="fast_analysis"),
    ],
    fallback_enabled=True
)

# Automatically picks best provider & falls back if rate-limited
result = await orchestrator.execute(agent="Architect", context=ctx)
```

### 🔌 Pluggable Adapters

Swap any component via configuration:

**Storage Backends**:
- `FileStorage` - JSON files (great for dev)
- `PostgreSQLStorage` - Production database
- `RedisStorage` - Fast, ephemeral workflows
- `S3Storage` - Serverless deployments

**Git Platforms**:
- `GitHubPlatform` - Issues, PRs, comments
- `GitLabPlatform` - Issues, MRs, notes
- `BitbucketPlatform` - Issues, PRs

**Notification Channels**:
- `TelegramNotifier` - Interactive bot messages
- `SlackNotifier` - Thread-based updates
- `EmailNotifier` - Digest & alerts
- `WebhookNotifier` - Custom integrations

### 🛡️ Production-Ready Error Handling

- Exponential backoff with jitter
- Configurable retry limits per step
- Timeout detection & auto-kill
- User-friendly error messages
- Dead letter queue for failed workflows

### 📊 Monitoring & Metrics

Built-in observability:

```python
from nexus.monitoring import PrometheusExporter

# Export metrics
exporter = PrometheusExporter(port=9090)
engine.add_observer(exporter)

# Track:
# - Workflow success/failure rates
# - Step execution time
# - AI provider latency & costs
# - Retry & timeout counts
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  Input Adapters                         │
│  (Telegram, Slack, Webhook, CLI, GitHub Issues)         │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────┐
│                Workflow Engine                       │
│  ┌────────────────────────────────────────────────┐  │
│  │  Step Manager → State Machine → Audit Logger  │  │
│  └────────────────────────────────────────────────┘  │
└────────────┬──────────────────────┬──────────────────┘
             │                      │
    ┌────────▼────────┐    ┌───────▼───────┐
    │ AI Orchestrator │    │ Storage Backend│
    │  - Provider     │    │  - State       │
    │    Selection    │    │  - Audit Log   │
    │  - Retry Logic  │    │  - Metrics     │
    │  - Fallback     │    └────────────────┘
    └────────┬────────┘
             │
    ┌────────▼─────────────────────┐
    │     AI Providers             │
    │  (Copilot, GPT-4, Claude,    │
    │   Gemini, Local Models)      │
    └──────────────────────────────┘
             │
    ┌────────▼────────┐
    │ Output Adapters │
    │  (Git Platform, │
    │   Notifications)│
    └─────────────────┘
```

---

## Use Cases

### Feature Development Workflow
Multi-agent workflow that creates traceable artifacts at each step:
```yaml
workflow: feature_development
trigger: issue_created (label: feature)
steps:
  - ProjectLead: Triage & scope
    → Adds comment with complexity analysis
  - Architect: Technical design
    → Creates design doc, updates issue
  - Developer: Implementation
    → Creates PR, links to issue
  - QAGuard: Review & test
    → Comments on PR with test results
  - OpsCommander: Deploy
    → Adds deployment status to PR
```
**All agent decisions preserved in GitHub for future reference.**

### Code Review Automation
AI-powered review with full traceability:
```yaml
workflow: automated_review
trigger: pull_request_opened
steps:
  - SecurityAgent: Scan for vulnerabilities
    → Posts review comments on specific lines
  - PerformanceAgent: Check efficiency
    → Suggests optimizations in PR thread
  - StyleAgent: Enforce standards
    → Requests changes with explanations
```
**Every suggestion is a PR comment, not lost in logs.**

### Bug Fix Pipeline
End-to-end bug resolution with audit trail:
```yaml
workflow: bug_fix
trigger: issue_created (label: bug)
steps:
  - TriageAgent: Assess severity
    → Labels issue, sets priority
  - DiagnosticAgent: Root cause analysis
    → Comments with findings
  - FixAgent: Implement solution
    → Creates PR with fix
  - VerificationAgent: Test fix
    → Validates and approves PR
```
**Complete history from bug report to fix, all in GitHub.**

---

## Examples

See [examples/](./examples) directory:

- **basic_workflow.py** - Simple 3-step workflow
- **github_ci.py** - Automated code review on PRs
- **support_router.py** - Support ticket classification & routing
- **doc_generator.py** - Auto-generate docs from code

---

## Configuration

### YAML Config (`nexus.yaml`)

```yaml
version: "1.0"

adapters:
  storage:
    type: postgres
    connection_string: ${DATABASE_URL}
  
  git:
    type: github
    repo: yourorg/yourrepo
    token: ${GITHUB_TOKEN}
  
  ai_providers:
    - type: openai
      api_key: ${OPENAI_API_KEY}
      models: [gpt-4, gpt-3.5-turbo]
      preference: reasoning
    
    - type: copilot_cli
      path: /usr/local/bin/copilot
      preference: code_generation

workflows:
  - name: feature_dev
    file: ./workflows/feature_dev.yaml
  - name: bug_fix
    file: ./workflows/bug_fix.yaml
```

### Environment Variables

```bash
# Required
DATABASE_URL=postgresql://user:pass@localhost/nexus
GITHUB_TOKEN=ghp_your_token
OPENAI_API_KEY=sk-your_key

# Optional
NEXUS_LOG_LEVEL=INFO
NEXUS_HEALTH_CHECK_PORT=8080
NEXUS_METRICS_PORT=9090
```

---

## Development

### Setup

```bash
git clone https://github.com/Ghabs95/nexus-core
cd nexus-core
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

### Run Tests

```bash
pytest                    # Run all tests
pytest --cov             # With coverage
pytest -k test_workflow  # Specific test
```

### Code Quality

```bash
black .         # Format
ruff check .    # Lint
mypy nexus/     # Type check
```

---

## Roadmap

### v0.1 (Current)
- [x] Core workflow engine
- [x] File, Postgres, Redis storage
- [x] GitHub, GitLab git platforms
- [x] Copilot CLI, Gemini CLI, OpenAI providers
- [x] Telegram, Slack notifiers

### v0.2 (Next)
- [ ] GraphQL API for workflow management
- [ ] Web dashboard for monitoring
- [ ] Workflow versioning & rollback
- [ ] Distributed execution (Celery/RQ)
- [ ] Workflow marketplace

### v1.0
- [ ] SLA guarantees
- [ ] Multi-tenancy
- [ ] RBAC & audit compliance
- [ ] Cloud-hosted offering

---

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

**Good first issues**:
- Implement new adapters (Notion, Jira, Discord)
- Add example workflows
- Improve documentation
- Write integration tests

---

## License

Apache License 2.0 - see [LICENSE](LICENSE) for details.

---

## Support

- **Documentation**: https://nexus-core.readthedocs.io
- **Comparison Guide**: [vs Google ADK, LangChain, CrewAI](docs/COMPARISON.md)
- **Issues**: https://github.com/Ghabs95/nexus-core/issues
- **Discord**: https://discord.gg/nexus-core
- **Email**: support@nexus-core.dev

---

## Acknowledgments

Built with inspiration from:
- **Temporal** - Workflow orchestration patterns
- **Langchain** - AI tool composition
- **Prefect** - Developer experience
- **Original Nexus** - Real-world production validation

---

**Star ⭐ this repo if you find it useful!**
