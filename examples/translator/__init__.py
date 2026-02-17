"""
Agent Translator Utilities

Convert agent YAML definitions to various formats:
- Markdown documentation
- Python class scaffolding  
- VS Code Copilot instructions

These are example tools that developers can customize for their own needs.
"""

from .to_markdown import translate_agent_to_markdown
from .to_python import translate_agent_to_python
from .to_copilot import translate_agent_to_copilot

__all__ = [
    "translate_agent_to_markdown",
    "translate_agent_to_python", 
    "translate_agent_to_copilot",
]
