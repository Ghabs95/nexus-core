"""Base interface for AI providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nexus.core.models import AgentResult, RateLimitStatus


@dataclass
class ExecutionContext:
    """Context for agent execution."""

    agent_name: str
    prompt: str
    workspace: Path
    issue_url: str | None = None
    metadata: dict[str, Any] = None
    timeout: int = 600
    tool_restrictions: list | None = None  # Commands/tools to block (e.g., ["gh pr merge"])
    model_override: str | None = None  # Override the provider's default model (e.g., "gpt-4o-mini")
    max_tokens: int | None = None  # Cap output tokens for cost control


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
