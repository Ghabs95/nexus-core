"""
Agent YAML → VS Code Copilot Instructions Translator

Converts agent.yaml to .copilot instruction format for Copilot Chat.
This allows developers to ask Copilot to implement the agent.

Usage:
    python to_copilot.py triage-agent.yaml > triage-agent.copilot
"""

import yaml
import sys
from pathlib import Path


def translate_agent_to_copilot(yaml_path: str) -> str:
    """Load YAML agent definition and generate .copilot instructions."""
    
    with open(yaml_path) as f:
        agent = yaml.safe_load(f)
    
    metadata = agent.get("metadata", {})
    spec = agent.get("spec", {})
    
    instructions = f"""# Copilot Instructions: {metadata.get('name')}

You are implementing the `{metadata.get('name')}` agent for Nexus Core.

## Agent Definition

**Name:** {metadata.get('name')}
**Description:** {metadata.get('description')}
**Version:** {metadata.get('version')}

## Purpose

{spec.get('purpose', '')}

## Requirements

### Required Tools (must be called/used)
```
{chr(10).join(f'- {tool}' for tool in spec.get('requires_tools', []))}
```

### Input Schema
You will receive:
"""
    
    for param_name, param_def in spec.get("inputs", {}).items():
        required = "required" if param_def.get("required", False) else "optional"
        instructions += f"- `{param_name}` ({param_def.get('type', 'string')}, {required}): {param_def.get('description', '')}\n"
        if "example" in param_def:
            instructions += f"  Example: `{param_def['example']}`\n"
    
    instructions += "\n### Output Schema\nYou must return an object with:\n"
    
    for output_name, output_def in spec.get("outputs", {}).items():
        instructions += f"- `{output_name}` ({output_def.get('type', 'string')}): {output_def.get('description', '')}\n"
    
    instructions += f"\n## AI Instructions\n\nWhen calling the LLM, use this prompt:\n\n```\n{spec.get('ai_instructions', '')}\n```\n"
    
    if "example" in spec:
        example = spec["example"]
        instructions += "\n## Reference Example\n\n### Input\n"
        instructions += yaml.dump(example.get("input", {}), default_flow_style=False)
        instructions += "### Expected Output\n"
        instructions += yaml.dump(example.get("expected_output", {}), default_flow_style=False)
    
    instructions += "\n## Implementation Notes\n\n"
    instructions += "1. Follow the Nexus Core async/await patterns\n"
    instructions += "2. Add proper error handling and retries\n"
    instructions += "3. Include type hints for all parameters\n"
    instructions += "4. Write docstrings for public methods\n"
    instructions += "5. Add logging at key decision points\n"
    
    if "next_steps" in spec:
        instructions += "\n## Routing After Execution\n\n"
        instructions += "After successful execution:\n"
        next_steps = spec.get("next_steps", [])
        if isinstance(next_steps, list):
            for step in next_steps:
                if isinstance(step, dict):
                    if "default" in step:
                        instructions += f"- Default: {step['default']}\n"
                    elif "condition" in step and "then" in step:
                        instructions += f"- If {step['condition']}, then {step['then']}\n"
        elif isinstance(next_steps, dict):
            for condition, action in next_steps.items():
                instructions += f"- If {condition}, then {action}\n"
    
    instructions += """\n## Testing\n\nAfter implementation:
1. Write at least 2 unit tests covering different input scenarios
2. Test error conditions (missing inputs, API failures, etc.)
3. Verify output matches the schema
4. Test timeout/retry behavior

## Resources

- Parent workflow: Check `examples/workflows/` for how this agent is used
- Similar agents: See `examples/agents/` for reference implementations
- Framework docs: Check `docs/USAGE.md` for Nexus Core patterns
"""
    
    return instructions


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python to_copilot.py <agent.yaml>")
        sys.exit(1)
    
    yaml_file = sys.argv[1]
    copilot_instructions = translate_agent_to_copilot(yaml_file)
    print(copilot_instructions)
