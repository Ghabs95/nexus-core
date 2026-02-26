# Workflow Definitions

Example Nexus Core workflows that orchestrate agent actions.

## development_workflow.yaml

Simple **single-tier** workflow for managing development issues:

1. **Triage** — Analyse issue and classify as bug/feature/doc
2. **Route** — Conditional routing based on issue type
3. **Design** — Create design proposal for features
4. **Debug** — Root cause analysis for bugs
5. **Developer** — Implement code changes
6. **Reviewer** — Review the implementation PR
7. **Docs** — Update documentation
8. **Summarizer** — Final summary and close loop

This demonstrates:

- Multi-step orchestration
- Conditional routing via `agent_type: "router"`
- Agent hand-offs via `on_success`
- Git-native audit trail (all decisions in GitHub comments)
- Human approval gates

## enterprise_workflow.yaml

**Multi-tier** workflow demonstrating full lifecycle orchestration with three
selectable tiers:

| Tier           | Key          | Use Case     | Steps                                                                         |
|----------------|--------------|--------------|-------------------------------------------------------------------------------|
| **Full**       | `full`       | New features | 10 steps: triage → design → develop → review → compliance → deploy → document |
| **Shortened**  | `shortened`  | Bug fixes    | 7 steps: triage → debug → develop → review → deploy → document                |
| **Fast-track** | `fast-track` | Hotfixes     | 4 steps: triage → develop → review → deploy                                   |

Additional patterns demonstrated:

- **Multi-tier workflows** — select a tier at runtime via `workflow_type`
- **Feedback loops** — reviewer rejects → back to develop
- **Compliance gate** — security/privacy review before deploy
- **Human approval gates** for deployments
- **Error handling** and per-step timeout configuration

### Loading a specific tier

```python
from nexus.core.workflow import WorkflowDefinition

# Load the shortened (bug fix) tier
wf = WorkflowDefinition.from_yaml("enterprise_workflow.yaml", workflow_type="shortened")

# Get prompt context for a specific agent in the full tier
context = WorkflowDefinition.to_prompt_context(
    "enterprise_workflow.yaml",
    current_agent_type="developer",
    workflow_type="full",
)
```

## Creating Your Own Workflow

See `examples/README.md` for instructions on defining agents and workflows.
