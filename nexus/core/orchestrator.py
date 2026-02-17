"""
AI Orchestrator - intelligently routes work to best AI provider with fallback.

Migrated and simplified from original Nexus ai_orchestrator.py
"""
import logging
from typing import List, Optional

from nexus.adapters.ai.base import AIProvider, ExecutionContext
from nexus.core.models import AgentResult

logger = logging.getLogger(__name__)


class AIOrchestrator:
    """
    Orchestrates multiple AI providers with intelligent routing and fallback.
    
    Features:
    - Automatic provider selection based on task type and availability
    - Fallback to alternate providers on failure or rate limiting
    - Provider preference scoring
    """

    def __init__(self, providers: List[AIProvider], fallback_enabled: bool = True):
        """
        Initialize orchestrator.
        
        Args:
            providers: List of available AI providers
            fallback_enabled: Whether to try fallback providers on failure
        """
        self.providers = providers
        self.fallback_enabled = fallback_enabled

    async def execute(
        self,
        agent_name: str,
        prompt: str,
        workspace: str,
        task_type: str = "code_generation",
        **kwargs
    ) -> AgentResult:
        """
        Execute agent with best available provider.
        
        Args:
            agent_name: Name of the agent to execute
            prompt: Prompt/instructions for the agent
            workspace: Workspace path
            task_type: Type of task ("code_generation", "reasoning", "analysis")
            **kwargs: Additional context
            
        Returns:
            AgentResult from execution
        """
        from pathlib import Path
        
        context = ExecutionContext(
            agent_name=agent_name,
            prompt=prompt,
            workspace=Path(workspace),
            metadata=kwargs
        )
        
        # Select providers by preference score for this task type
        ranked_providers = await self._rank_providers(task_type)
        
        if not ranked_providers:
            return AgentResult(
                success=False,
                output="",
                error="No AI providers available"
            )
        
        # Try each provider in order
        last_error = None
        for provider in ranked_providers:
            # Check availability
            if not await provider.check_availability():
                logger.info(f"Provider {provider.name} unavailable, trying next")
                continue
            
            try:
                logger.info(f"Executing {agent_name} with provider {provider.name}")
                result = await provider.execute_agent(context)
                
                if result.success:
                    result.provider_used = provider.name
                    return result
                else:
                    last_error = result.error
                    logger.warning(f"Provider {provider.name} failed: {result.error}")
                    if not self.fallback_enabled:
                        return result
            
            except Exception as e:
                last_error = str(e)
                logger.error(f"Provider {provider.name} raised exception: {e}")
                if not self.fallback_enabled:
                    return AgentResult(
                        success=False,
                        output="",
                        error=str(e),
                        provider_used=provider.name
                    )
        
        # All providers failed
        return AgentResult(
            success=False,
            output="",
            error=f"All providers failed. Last error: {last_error}"
        )

    async def _rank_providers(self, task_type: str) -> List[AIProvider]:
        """Rank providers by preference score for task type."""
        scored = [
            (provider, provider.get_preference_score(task_type))
            for provider in self.providers
        ]
        
        # Sort by score descending
        scored.sort(key=lambda x: x[1], reverse=True)
        
        return [provider for provider, score in scored]

    def add_provider(self, provider: AIProvider) -> None:
        """Add a provider to the orchestrator."""
        self.providers.append(provider)
        logger.info(f"Added provider: {provider.name}")

    def remove_provider(self, provider_name: str) -> bool:
        """Remove a provider by name."""
        for i, provider in enumerate(self.providers):
            if provider.name == provider_name:
                self.providers.pop(i)
                logger.info(f"Removed provider: {provider_name}")
                return True
        return False
