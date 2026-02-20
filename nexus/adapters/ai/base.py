"""Base interface for AI providers."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from nexus.core.models import AgentResult, RateLimitStatus


@dataclass
class ExecutionContext:
    """Context for agent execution."""

    agent_name: str
    prompt: str
    workspace: Path
    issue_url: Optional[str] = None
    metadata: Dict[str, Any] = None
    timeout: int = 600
    tool_restrictions: Optional[list] = None  # Commands/tools to block (e.g., ["gh pr merge"])


class AIProvider(ABC):
    """Abstract interface for AI providers (Copilot, Gemini, etc.)."""

    @abstractmethod
    async def execute_agent(self, context: ExecutionContext) -> AgentResult:
        """
        Execute an AI agent with given context.
        
        Args:
            context: Execution context with prompt, workspace, metadata
            
        Returns:
            AgentResult with success status, output, and metadata
        """
        pass

    @abstractmethod
    async def check_availability(self) -> bool:
        """Check if this provider is currently available."""
        pass

    @abstractmethod
    async def get_rate_limit_status(self) -> RateLimitStatus:
        """Get current rate limit status."""
        pass

    @abstractmethod
    def get_preference_score(self, task_type: str) -> float:
        """
        Return preference score (0.0-1.0) for this provider for given task type.
        
        Task types: "code_generation", "reasoning", "analysis", "content_creation"
        Higher score = better fit
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name (e.g., 'openai', 'copilot', 'gemini')."""
        pass
