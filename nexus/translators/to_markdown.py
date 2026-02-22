"""
Agent YAML → Markdown Translator

Converts agent.yaml definitions into formatted markdown documentation.
Users can customize this to fit their documentation style.

Usage:
    python -m nexus.cli translate to-markdown triage-agent.yaml > Triage-Agent.md
"""

import yaml
import sys
from pathlib import Path


def translate_agent_to_markdown(yaml_path: str) -> str:
    """Load YAML agent definition and render as markdown."""
    
    with open(yaml_path) as f:
        agent = yaml.safe_load(f)
    
    metadata = agent.get("metadata", {})
    spec = agent.get("spec", {})
    
    md = f"""# {metadata.get('name', 'Agent')}

{metadata.get('description', '')}

**Version:** {metadata.get('version', '0.1.0')} | 
**Author:** {metadata.get('author', 'Unknown')}

---

## Overview

{spec.get('purpose', '')}

---

## Tools Required

The agent needs access to these tools:

```
{chr(10).join(f'- {tool}' for tool in spec.get('requires_tools', []))}
```

---

## Input Schema

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
"""
    
    # Add input parameters
    for param_name, param_def in spec.get("inputs", {}).items():
        required = "✓" if param_def.get("required", False) else "✗"
        md += f"| `{param_name}` | {param_def.get('type', 'string')} | {required} | {param_def.get('description', '')} |\n"
    
    md += "\n## Output Schema\n\n| Field | Type | Description |\n|-------|------|-------------|\n"
    
    # Add output parameters
    for output_name, output_def in spec.get("outputs", {}).items():
        md += f"| `{output_name}` | {output_def.get('type', 'string')} | {output_def.get('description', '')} |\n"
    
    md += f"\n## AI Instructions\n\n```\n{spec.get('ai_instructions', '')}\n```\n"
    
    # Add example
    if "example" in spec:
        example = spec["example"]
        md += "\n## Example\n\n### Input\n```json\n"
        md += yaml.dump(example.get("input", {}), default_flow_style=False)
        md += "```\n\n### Expected Output\n```json\n"
        md += yaml.dump(example.get("expected_output", {}), default_flow_style=False)
        md += "```\n"
    
    # Add routing
    if "next_steps" in spec:
        md += "\n## Routing\n\nAfter this agent completes:\n"
        next_steps = spec.get("next_steps", [])
        if isinstance(next_steps, list):
            for step in next_steps:
                if isinstance(step, dict):
                    if "default" in step:
                        md += f"- Default: {step['default']}\n"
                    elif "condition" in step and "then" in step:
                        md += f"- If {step['condition']} → {step['then']}\n"
        elif isinstance(next_steps, dict):
            for condition, action in next_steps.items():
                md += f"- {condition} → {action}\n"
    
    return md
