# Agent YAML definitions for Nexus Core

These are example agent definitions in the Nexus Core agent format.
Use the translator tools to convert to markdown, Python, or Copilot instructions.

## Available Agents

- **triage-agent.yaml** - Analyzes issues, classifies type (bug/feature/doc) and priority (P0-P3)
- **design-agent.yaml** - Creates technical design proposals for feature requests
- **debug-agent.yaml** - Analyzes bug reports and suggests root causes
- **code-reviewer-agent.yaml** - Reviews pull requests for code quality and best practices
- **docs-agent.yaml** - Updates and maintains project documentation
- **summarizer-agent.yaml** - Provides final summaries and closes workflow loops

## Usage

These agents are referenced by `agent_type` in workflow YAML files (see `../workflows/development_workflow.yaml`).

Configure which AI tool (copilot/gemini) each agent uses in `project_config.yaml`:

```yaml
ai_tool_preferences:
  triage: copilot          # Issue classification
  design: copilot          # Design proposals
  debug: copilot           # Root cause analysis
  code_reviewer: copilot   # PR reviews
  docs: gemini             # Documentation (faster)
  summarizer: gemini       # Summaries (faster)
```

## Translators

To generate Python implementation template:
```bash
python ../translator/to_python.py triage-agent.yaml > triage_agent.py
```

To generate markdown documentation:
```bash
python ../translator/to_markdown.py triage-agent.yaml > TRIAGE-AGENT.md
```

To generate Copilot instructions:
```bash
python ../translator/to_copilot.py triage-agent.yaml > triage-agent.copilot
```

