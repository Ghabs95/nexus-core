"""GeminiCLI AI provider implementation."""
import asyncio
import logging
import shutil
import time
from pathlib import Path

from nexus.adapters.ai.base import AIProvider, ExecutionContext
from nexus.core.models import AgentResult, RateLimitStatus

logger = logging.getLogger(__name__)

# Task types where Gemini CLI excels
_GEMINI_PREFERRED_TASKS = {"reasoning", "analysis", "content_creation"}


class GeminiCLIProvider(AIProvider):
    """AI provider that delegates to the Gemini CLI (`gemini` binary)."""

    def __init__(self, timeout: int = 600, model: str = "gemini-2.0-flash"):
        self._timeout = timeout
        self._model = model
        self._availability_cache: dict = {}
        self._availability_ttl: int = 300  # seconds

    @property
    def name(self) -> str:
        return "gemini"

    async def check_availability(self) -> bool:
        """Return True if the `gemini` CLI binary is installed."""
        now = time.time()
        cached = self._availability_cache.get("gemini")
        if cached and now - cached["at"] < self._availability_ttl:
            return cached["available"]

        available = bool(shutil.which("gemini"))
        self._availability_cache["gemini"] = {"available": available, "at": now}
        return available

    async def get_rate_limit_status(self) -> RateLimitStatus:
        """Gemini CLI has no programmatic rate-limit endpoint; report unlimited."""
        return RateLimitStatus(
            provider=self.name,
            is_limited=False,
        )

    def get_preference_score(self, task_type: str) -> float:
        """Return 0.9 for reasoning/analysis tasks, 0.5 otherwise."""
        return 0.9 if task_type in _GEMINI_PREFERRED_TASKS else 0.5

    async def execute_agent(self, context: ExecutionContext) -> AgentResult:
        """Run the agent prompt through the `gemini` CLI."""
        start = time.time()
        workspace = Path(context.workspace)
        workspace.mkdir(parents=True, exist_ok=True)

        cmd = ["gemini", "--model", self._model, "--prompt", context.prompt]
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace),
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=context.timeout or self._timeout,
            )
            elapsed = time.time() - start
            if process.returncode == 0:
                return AgentResult(
                    success=True,
                    output=stdout.decode(errors="replace"),
                    execution_time=elapsed,
                    provider_used=self.name,
                )
            return AgentResult(
                success=False,
                output=stdout.decode(errors="replace"),
                error=stderr.decode(errors="replace"),
                execution_time=elapsed,
                provider_used=self.name,
            )
        except TimeoutError:
            return AgentResult(
                success=False,
                output="",
                error=f"Timeout after {context.timeout or self._timeout}s",
                execution_time=time.time() - start,
                provider_used=self.name,
            )
        except Exception as exc:
            return AgentResult(
                success=False,
                output="",
                error=str(exc),
                execution_time=time.time() - start,
                provider_used=self.name,
            )
