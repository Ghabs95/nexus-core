# Copilot Instructions - Nexus Core

## Agent System

**CRITICAL:** Nexus Core uses an **agent_type** system, NOT hardcoded agent names.

### Agent Type Reference

When routing tasks or mentioning agents, ALWAYS use agent_types from the `examples/agents/` folder:

- **`triage`** - Issue classification, priority analysis, complexity assessment
- **`design`** - Technical design proposals, architecture planning
- **`debug`** - Root cause analysis, bug investigation
- **`code_reviewer`** - Code review, PR feedback, quality checks
- **`docs`** - Documentation updates, README improvements
- **`summarizer`** - Workflow summaries, status reports

### Agent Definitions

Agent specifications are in `examples/agents/*.yaml`. Reference these files for:
- Agent capabilities and tools
- Input/output schemas
- Prompt templates
- Routing logic

**Example agent reference:**
```yaml
# Correct
agent_type: triage
# See: examples/agents/triage-agent.yaml

# WRONG - DO NOT USE
agent: ProjectLead
agent: @Atlas
```

### Workflow Routing

When completing a task and routing to next agent:

✅ **CORRECT:**
- "Routing to **triage** agent for complexity analysis"
- "Next step: **design** agent will create technical proposal"
- "Escalating to **debug** agent for root cause analysis"

❌ **INCORRECT:**
- "Routing to @ProjectLead"
- "Next: @Atlas for RCA"
- "Escalating to Tier2Lead"

### Documentation Context

When reading documentation:
- README.md and ARCHITECTURE.md may contain historical examples with old agent names
- These are for comparison purposes only
- ALWAYS use agent_types from `examples/agents/` folder for current system

## Coding Conventions

- PEP 8 style, 100-char lines, double quotes
- Type hints on all function signatures
- Small, readable functions - avoid clever abstractions
- Minimal comments - code should be self-documenting
- Async/await patterns throughout

## Testing

- Write tests for all new features
- Use pytest with async support
- Mock external dependencies (GitHub API, etc.)
- Tests live in `tests/` directory

## Agent Implementation

When implementing agents:
1. Check `examples/agents/` for the agent YAML definition
2. Follow the input/output schema exactly
3. Use the tools specified in `requires_tools`
4. Return structured output matching the schema
5. Add error handling and retries as specified

## Workflow Context

- Workflows defined in YAML (`examples/workflows/*.yaml`)
- Steps use `agent_type` field to route to correct agent
- Conditions support Python expressions for control flow
- All workflows are asynchronous and stateful
