# Workflow Definitions

Example Nexus Core workflows that orchestrate agent actions.

## nexus_core_development.yaml

Multi-agent workflow for managing Nexus Core's own development:

1. **Triage** - Analyze new issues (type, priority, labels)
2. **Route** - Conditional routing based on issue type
3. **Design** - Create design proposal for features
4. **Debug** - Analyze bug reports
5. **Code Review** - Review pull requests
6. **Docs** - Update documentation
7. **Close Loop** - Summarize and link back to original issue

This demonstrates:
- Multi-step orchestration
- Conditional routing
- Agent hand-offs
- Git-native audit trail (all decisions in GitHub comments)
- Human approval gates

## Creating Your Own Workflow

See `examples/README.md` for instructions on defining agents and workflows.
