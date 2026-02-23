# Nexus Core Architecture

> **About this document:** This shows the evolution from the original Nexus Telegram bot (coupled architecture) to the generic Nexus Core framework (pluggable architecture). The migration sections are specific to that project but demonstrate how to adopt the framework.

## Original Nexus (Coupled Architecture)

```
┌──────────────┐
│   Telegram   │ ← Single input method
│     Bot      │
└──────┬───────┘
       │
       ▼
┌──────────────────┐
│  Inbox Files     │ ← File-based only
│  (JSON on disk)  │
└──────┬───────────┘
       │
       ▼
┌────────────────────────┐
│  Inbox Processor       │ ← Hardcoded logic
│  - Hardcoded projects  │
│  - GitHub only         │
│  - Copilot/Gemini only │
└──────┬─────────────────┘
       │
       ▼
┌───────────────────┐
│  GitHub Issues    │ ← GitHub only
│  + Copilot CLI    │
└───────────────────┘
```

**Problems:**
- ❌ Can't use Slack, Discord, web interface
- ❌ Can't switch from GitHub to GitLab
- ❌ Can't use Claude API, Codex API
- ❌ Can't scale to PostgreSQL
- ❌ Hard to test (tightly coupled)
- ❌ Not reusable by other teams

---

## Nexus Core (Pluggable Architecture)

```
┌──────────────────────────────────────────────────┐
│              Input Adapters                      │
│  ┌──────────┬──────────┬──────────┬──────────┐  │
│  │Telegram  │  Slack   │ Webhook  │   CLI    │  │
│  └────┬─────┴────┬─────┴────┬─────┴────┬─────┘  │
└───────┼──────────┼──────────┼──────────┼────────┘
        │          │          │          │
        └──────────┴─────┬────┴──────────┘
                         │
                         ▼
        ┌────────────────────────────────┐
        │     Workflow Engine            │
        │  ┌──────────────────────────┐  │
        │  │  - State Machine         │  │
        │  │  - Step Execution        │  │
        │  │  - Pause/Resume/Cancel   │  │
        │  │  - Audit Logging         │  │
        │  └──────────────────────────┘  │
        └─────┬──────────────────┬───────┘
              │                  │
    ┌─────────▼─────┐   ┌────────▼─────────┐
    │ AI Orchestrator│   │ Storage Backend  │
    │  ┌──────────┐ │   │  ┌────────────┐  │
    │  │Provider  │ │   │  │File/JSON   │  │
    │  │Selection │ │   │  │PostgreSQL  │  │
    │  │Fallback  │ │   │  │Redis       │  │
    │  │Retry     │ │   │  │S3          │  │
    │  └──────────┘ │   │  └────────────┘  │
    └────┬──────────┘   └──────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│         AI Providers                    │
│  ┌─────────┬──────────┬──────────────┐  │
│  │Copilot  │  OpenAI  │  Anthropic   │  │
│  │  CLI    │   API    │    API       │  │
│  ├─────────┼──────────┼──────────────┤  │
│  │ Gemini  │  Local   │   Custom     │  │
│  │  CLI    │  Models  │   Provider   │  │
│  └─────────┴──────────┴──────────────┘  │
└─────────┬───────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────┐
│         Output Adapters                 │
│  ┌──────────────┬───────────────────┐   │
│  │Git Platform  │  Notifications    │   │
│  │┌──────────┐  │  ┌─────────────┐  │   │
│  ││ GitHub   │  │  │  Telegram   │  │   │
│  ││ GitLab   │  │  │  Slack      │  │   │
│  ││Bitbucket │  │  │  Email      │  │   │
│  │└──────────┘  │  │  Discord    │  │   │
│  │              │  └─────────────┘  │   │
│  └──────────────┴───────────────────┘   │
└─────────────────────────────────────────┘
```

**Benefits:**
- ✅ **Multi-channel input**: Telegram, Slack, Webhook, CLI
- ✅ **Storage flexibility**: File → Postgres → Redis as you grow
- ✅ **Git platform agnostic**: GitHub, GitLab, Bitbucket
- ✅ **AI provider choice**: Copilot, Gemini, soon Claude and Codex
- ✅ **Testable**: Mock any adapter
- ✅ **Reusable**: Ship as library, deploy anywhere
- ✅ **Horizontally scalable**: Distributed workflows

---

## Data Flow Example

### Scenario: User submits feature request via Telegram

```
1. INPUT
   ┌─────────────┐
   │  User sends │
   │ voice note  │
   │to Telegram  │
   └──────┬──────┘
          │
          ▼
   ┌──────────────────┐
   │ TelegramAdapter  │
   │  .receive_task() │
   └──────┬───────────┘
          │
          ▼ Creates Task object
          
2. WORKFLOW EXECUTION
   ┌──────────────────┐
   │ WorkflowEngine   │
   │ .start_workflow()│
   └──────┬───────────┘
          │
          ▼ Loads workflow definition
          
   ┌──────────────────────────┐
   │ Workflow: feature_dev    │
   │  Step 1: Triage (AI)     │
   │  Step 2: Design (AI)     │
   │  Step 3: Implement (AI)  │
   └──────┬───────────────────┘
          │
          ▼ For each step...

3. AI EXECUTION
   ┌──────────────────┐
   │ AIOrchestrator   │
   │ .execute()       │
   └──────┬───────────┘
          │
          ▼ Selects best provider
          
   ┌──────────────────┐
   │Try Copilot CLI   │
   │  ❌ Rate limited │
   └──────┬───────────┘
          │
          ▼ Fallback
          
   ┌──────────────────┐
   │Try OpenAI API    │
   │  ✅ Success!     │
   └──────┬───────────┘
          │
          ▼ Returns AgentResult

4. STATE PERSISTENCE
   ┌──────────────────┐
   │ FileStorage      │
   │ .save_workflow() │
   │ .append_audit()  │
   └──────┬───────────┘
          │
          ▼ Persists to disk
          
   workflows/wf-001.json
   audit/wf-001.jsonl

5. OUTPUT
   ┌──────────────────┐
   │ GitHubPlatform   │
   │ .create_issue()  │
   │ .add_comment()   │
   └──────┬───────────┘
          │
          ▼ Posts to GitHub
          
   ┌──────────────────┐
   │TelegramNotifier  │
   │ .send_message()  │
   └──────┬───────────┘
          │
          ▼ Sends update
          
   ✅ "Issue #42 created: Feature XYZ"
   ✅ "Step 1 complete: Triaged as P1"
```

---

## Key Abstractions

### 1. StorageBackend
**Why**: Decouple state persistence from storage technology

**Interface**:
```python
class StorageBackend(ABC):
    async def save_workflow(workflow: Workflow) -> None
    async def load_workflow(workflow_id: str) -> Workflow
    async def append_audit_event(event: AuditEvent) -> None
    async def get_audit_log(workflow_id: str) -> List[AuditEvent]
```

**Implementations**:
- `FileStorage` - JSON files (MVP)
- `PostgreSQLStorage` - Relational DB (production)
- `RedisStorage` - Fast cache (ephemeral workflows)
- `S3Storage` - Serverless (AWS Lambda)

### 2. GitPlatform
**Why**: Support GitHub, GitLab, Bitbucket interchangeably

**Interface**:
```python
class GitPlatform(ABC):
    async def create_issue(title, body, labels) -> Issue
    async def add_comment(issue_id, body) -> Comment
    async def close_issue(issue_id) -> None
    async def search_linked_prs(issue_id) -> List[PullRequest]
```

**Implementations**:
- `GitHubPlatform` - gh CLI
- `GitLabPlatform` - GitLab API
- `BitbucketPlatform` - Bitbucket API

### 3. AIProvider
**Why**: Choose best AI tool for each task, automatic fallback

**Interface**:
```python
class AIProvider(ABC):
    async def execute_agent(context: ExecutionContext) -> AgentResult
    async def check_availability() -> bool
    async def get_rate_limit_status() -> RateLimitStatus
    def get_preference_score(task_type: str) -> float
```

**Implementations**:
- `CopilotCLIProvider` - GitHub Copilot CLI
- `OpenAIProvider` - GPT-4 API (Coming soon)
- `AnthropicProvider` - Claude API (Coming soon)
- `GeminiCLIProvider` - Google Gemini CLI
- `LocalModelProvider` - Ollama, LM Studio

### 4. NotificationChannel
**Why**: Send updates via user's preferred platform

**Interface**:
```python
class NotificationChannel(ABC):
    async def send_message(user_id, message: Message) -> str
    async def update_message(message_id, new_text) -> None
    async def send_alert(message, severity: Severity) -> None
```

**Implementations**:
- `TelegramNotifier` - Telegram bot
- `SlackNotifier` - Slack webhooks
- `EmailNotifier` - SMTP
- `DiscordNotifier` - Discord webhooks

---

## Configuration Evolution

### Before (Hardcoded)

```python
# nexus/src/config.py
PROJECT_CONFIG = {
    "example_project": {
       "agents_dir": "examples/agents",
       "workspace": "examples",
       "git_repo": "Ghabs95/nexus-core",
    }
}

WORKFLOW_CHAIN = {
    "full": [
        ("ProjectLead", "Vision"),
        ("Architect", "Design"),
        # ... hardcoded steps
    ]
}
```

### After (Configurable)

```yaml
# nexus.yaml
adapters:
  storage:
    type: postgres # options: file, postgres
    storage_config:
      connection_string: ${DATABASE_URL}
      # storage_dir: ./data # required for type: file
  
  git:
    type: github
    repo: yourorg/yourrepo
    token: ${GITHUB_TOKEN}
```

---

## Migration Path: From Monolithic to Pluggable

This section shows a typical migration from a coupled system to nexus-core (based on a real migration from a Telegram bot).

**Phase 1**: Run in parallel
```
your-app/        (existing application, unchanged)
nexus-core/      (new framework, integrated gradually)
```

**Phase 2**: Gradual adoption
```python
# In your existing codebase
from nexus.adapters.storage import FileStorage
from nexus.core.workflow import WorkflowEngine

# Replace your custom workflow code with nexus-core
engine = WorkflowEngine(storage=FileStorage("./data"))
workflow = await engine.create_workflow(your_workflow_definition)
```

**Phase 3**: Full migration
- Migrate all workflows to nexus-core
- Use YAML workflow definitions
- Deploy new version

---

## Commercial Deployment Scenarios

### Scenario 1: SaaS Platform

```
┌─────────────────────────┐
│   Web Dashboard         │
│  (React + GraphQL)      │
└───────┬─────────────────┘
        │
        ▼
┌─────────────────────────┐
│   Nexus Core API        │
│  (FastAPI + GraphQL)    │
└───────┬─────────────────┘
        │
        ├─► PostgreSQL (workflows)
        ├─► Redis (cache)
        ├─► S3 (artifact storage)
        └─► Celery (distributed execution)
```

**Pricing**: $99-499/mo per team

### Scenario 2: Enterprise Self-Hosted

```
┌─────────────────────────┐
│  Customer's K8s Cluster │
│                         │
│  ┌───────────────────┐  │
│  │ Nexus Core        │  │
│  │ (Docker image)    │  │
│  └─────┬─────────────┘  │
│        │                │
│        ├─► Their DB     │
│        ├─► Their Git    │
│        └─► Their LLMs   │
└─────────────────────────┘
```

**Pricing**: $5K-20K/year (support + SLA)

### Scenario 3: Open Source + Consulting

```
┌─────────────────────────┐
│   GitHub (Public Repo)  │
│   nexus-core (MIT)      │
└───────┬─────────────────┘
        │
        ▼ Download
┌─────────────────────────┐
│   Companies self-host   │
└─────────┬───────────────┘
          │
          ▼ Need help?
┌─────────────────────────┐
│  Offer Consulting       │
│  - Implementation       │
│  - Custom adapters      │
│  - Training             │
└─────────────────────────┘
```

**Pricing**: $200-300/hour consulting

---

## What Makes This Architecture Special

### 1. **Battle-Tested**
Extracted from production Nexus with real users, real workflows.

### 2. **Reliability First**
- Auto-retry with exponential backoff
- Timeout detection and recovery
- Audit trail for debugging
- State persistence across crashes

### 3. **Developer Experience**
- Clean abstractions
- Type hints throughout
- Async/await
- Comprehensive docs

### 4. **Production Ready**
- Horizontal scaling (Celery, RQ)
- Multi-tenancy ready
- Observability built-in
- Security considerations

---

**This is how you turn a personal project into a commercial product.** 🚀
