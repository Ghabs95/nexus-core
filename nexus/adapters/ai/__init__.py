"""AI provider adapters."""
from nexus.adapters.ai.base import AIProvider, ExecutionContext
from nexus.adapters.ai.copilot_provider import CopilotCLIProvider
from nexus.adapters.ai.gemini_provider import GeminiCLIProvider
from nexus.adapters.ai.registry import AgentRegistry

__all__ = [
    "AIProvider",
    "ExecutionContext",
    "CopilotCLIProvider",
    "GeminiCLIProvider",
    "AgentRegistry",
]
