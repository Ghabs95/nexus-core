"""
AI Orchestrator - intelligently routes work to best AI provider with fallback.

Migrated and simplified from original Nexus ai_orchestrator.py
"""
import json
import logging
import re
from typing import Any

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

    def __init__(self, providers: list[AIProvider], fallback_enabled: bool = True):
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
        approval_required: bool = False,
        tool_restrictions: list[str] | None = None,
        **kwargs
    ) -> AgentResult:
        """
        Execute agent with best available provider.

        Args:
            agent_name: Name of the agent to execute
            prompt: Prompt/instructions for the agent
            workspace: Workspace path
            task_type: Type of task ("code_generation", "reasoning", "analysis")
            approval_required: If True, inject tool restrictions for approval gates
            tool_restrictions: List of tools/commands to restrict (e.g., ["gh pr merge"])
            **kwargs: Additional context

        Returns:
            AgentResult from execution
        """
        from pathlib import Path

        # Generate prompt with restrictions if approval required
        final_prompt = prompt
        if approval_required:
            final_prompt = self._inject_approval_constraints(prompt, tool_restrictions or [])

        context = ExecutionContext(
            agent_name=agent_name,
            prompt=final_prompt,
            workspace=Path(workspace),
            tool_restrictions=tool_restrictions,
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

    def _inject_approval_constraints(self, prompt: str, tool_restrictions: list[str]) -> str:
        """
        Inject approval gate constraints into agent prompt.

        Args:
            prompt: Original agent prompt
            tool_restrictions: List of restricted tools/commands

        Returns:
            Prompt with approval constraints injected
        """
        if not tool_restrictions:
            return prompt

        restrictions_text = "\n".join(f"  - {tool}" for tool in tool_restrictions)

        approval_constraint = (
            "\n\n"
            "🚨 **APPROVAL GATE ENFORCEMENT (CRITICAL):**\n"
            "This step requires approval before certain operations can be executed.\n\n"
            "❌ **YOU CANNOT USE THESE TOOLS:**\n"
            f"{restrictions_text}\n\n"
            "✅ **YOU CAN USE:**\n"
            "  - File operations (read, create, modify, delete)\n"
            "  - Git operations (branch creation, commits, push)\n"
            "  - Code generation and analysis\n"
            "  - GitHub API reads (issues, PRs, repos)\n\n"
            "⚠️  Attempting to use restricted tools will cause workflow failure.\n"
            "Please wait for human approval before proceeding with these operations.\n"
        )

        return prompt + approval_constraint

    async def _rank_providers(self, task_type: str) -> list[AIProvider]:
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

    async def execute_with_delegation(
        self,
        agent_name: str,
        prompt: str,
        workspace: str,
        delegation_request: Any | None = None,
        handoff_manager: Any | None = None,
        **kwargs,
    ) -> AgentResult:
        """Execute agent with optional delegation tracking.

        Wraps :meth:`execute` with delegation lifecycle management:

        1. If *delegation_request* is provided, it is registered with
           *handoff_manager* before the agent is launched.
        2. After execution, the result output is scanned for a nested
           completion marker (a JSON block tagged ``__delegation_callback__``).
           When found, the marker is stripped from ``result.output`` and
           :meth:`handoff_manager.complete` is called to resolve the chain.
        3. ``result.metadata["delegation_id"]`` is set when a delegation is
           active, so callers can correlate results.

        Nested completion marker format emitted by a sub-agent::

            {"__delegation_callback__": {"delegation_id": "<uuid>",
                                         "result": {...},
                                         "success": true}}

        Args:
            agent_name: Name of the agent to execute.
            prompt: Prompt/instructions for the agent.
            workspace: Workspace path.
            delegation_request: Optional
                :class:`~nexus.core.models.DelegationRequest` to register.
            handoff_manager: Optional
                :class:`~nexus.plugins.plugin_runtime.HandoffManager` for
                lifecycle tracking.  Required when *delegation_request* is set.
            **kwargs: Forwarded to :meth:`execute`.

        Returns:
            :class:`~nexus.core.models.AgentResult` with
            ``metadata["delegation_id"]`` populated when active.
        """
        if delegation_request is not None and handoff_manager is not None:
            handoff_manager.register(delegation_request)
            kwargs.setdefault("metadata", {})["delegation_id"] = (
                delegation_request.delegation_id
            )

        result = await self.execute(agent_name, prompt, workspace, **kwargs)

        if result.metadata is None:
            result.metadata = {}

        if delegation_request is not None:
            result.metadata["delegation_id"] = delegation_request.delegation_id

        if handoff_manager is not None and result.output:
            result = self._resolve_delegation_callback(result, handoff_manager)

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    _CALLBACK_RE = re.compile(
        r'\{[^{}]*"__delegation_callback__"[^{}]*\{[^{}]*\}[^{}]*\}',
        re.DOTALL,
    )

    def _resolve_delegation_callback(
        self,
        result: AgentResult,
        handoff_manager: Any,
    ) -> AgentResult:
        """Scan *result.output* for delegation callback markers and resolve them.

        Strips the JSON marker from the output so downstream consumers see
        clean text.  Calls :meth:`handoff_manager.complete` for each valid
        marker found.

        Args:
            result: The :class:`~nexus.core.models.AgentResult` to inspect.
            handoff_manager: Active
                :class:`~nexus.plugins.plugin_runtime.HandoffManager`.

        Returns:
            The (possibly mutated) *result* with markers removed.
        """
        from nexus.core.models import DelegationCallback

        cleaned_output = result.output
        for match in self._CALLBACK_RE.finditer(result.output):
            raw = match.group(0)
            try:
                outer: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                continue

            payload = outer.get("__delegation_callback__")
            if not isinstance(payload, dict):
                continue

            delegation_id = payload.get("delegation_id")
            if not delegation_id:
                continue

            original = handoff_manager.get(delegation_id)
            if original is None:
                logger.warning(
                    "_resolve_delegation_callback: unknown delegation_id %s",
                    delegation_id,
                )
                continue

            callback = DelegationCallback(
                delegation_id=delegation_id,
                sub_agent=original.sub_agent,
                lead_agent=original.lead_agent,
                issue_number=original.issue_number,
                workflow_id=original.workflow_id,
                result=payload.get("result", {}),
                success=bool(payload.get("success", False)),
                error=payload.get("error"),
            )
            handoff_manager.complete(callback)
            logger.info(
                "Delegation callback resolved: %s (success=%s)",
                delegation_id,
                callback.success,
            )
            cleaned_output = cleaned_output.replace(raw, "", 1)

        result.output = cleaned_output.strip()
        return result
