# Workflow Definitions

Example Nexus Core workflows that orchestrate agent actions.

## development_workflow.yaml

Multi-agent workflow for managing Nexus Core's own development:

1. **Triage** - Analyze new issues (type, priority, labels)
2. **Route** - Conditional routing based on issue type
3. **Design** - Create design proposal for features
4. **Debug** - Root cause analysis for bugs
5. **Developer** - Implement code changes
6. **Reviewer** - Review the implementation PR
7. **Docs** - Update documentation
8. **Summarizer** - Final summary and close loop

This demonstrates:
- Multi-step orchestration
- Conditional routing via `agent_type: "router"`
- Agent hand-offs via `on_success`
- Git-native audit trail (all decisions in GitHub comments)
- Human approval gates

## ghabs_org_workflow.yaml

Real-world **multi-tier** workflow for the Ghabs organization with three tiers:

| Tier | Key | Use Case | Steps |
|------|-----|----------|-------|
| **Full** | `full` | New features | 12 steps: vision → feasibility → architecture → UX → implementation → QA → compliance → deployment → documentation |
| **Shortened** | `shortened` | Bug fixes | 7 steps: triage → root cause → fix → verify → deploy → document |
| **Fast-track** | `fast-track` | Hotfixes | 5 steps: emergency triage → quick impl → quick verify → emergency deploy |

This demonstrates:
- **Multi-tier workflows** — select a tier at runtime via `workflow_type`
- Named agents per role (`Ghabs`, `Atlas`, `Tier2Lead`, `QAGuard`, etc.)
- Feedback loops (e.g. QA rejects → back to implementation)
- Human approval gates for deployments
- Error handling and timeout configuration

### Loading a specific tier

```python
from nexus.core.workflow import WorkflowDefinition

# Load the shortened (bug fix) tier
wf = WorkflowDefinition.from_yaml("ghabs_org_workflow.yaml", workflow_type="shortened")

# Get prompt context for a specific agent in the full tier
context = WorkflowDefinition.to_prompt_context(
    "ghabs_org_workflow.yaml",
    current_agent_type="Tier2Lead",
    workflow_type="full",
)
```

## Creating Your Own Workflow

See `examples/README.md` for instructions on defining agents and workflows.
