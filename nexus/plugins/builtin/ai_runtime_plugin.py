"""Built-in plugin: AI runtime orchestration for Copilot/Gemini/Codex CLIs."""

import logging
import os
import subprocess
import time
from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any

from nexus.plugins.builtin.ai_runtime.agent_invoke_service import (
    invoke_agent_with_fallback as invoke_agent_with_fallback_impl,
)
from nexus.plugins.builtin.ai_runtime.analysis_service import (
    build_analysis_prompt as build_analysis_prompt_impl,
    parse_analysis_result as parse_analysis_result_impl,
    run_analysis_attempts as run_analysis_attempts_impl,
    run_analysis_with_provider as run_analysis_with_provider_impl,
    strip_cli_tool_output as strip_cli_tool_output_impl,
)
from nexus.plugins.builtin.ai_runtime.fallback_policy import (
    fallback_order_from_preferences as fallback_order_from_preferences_impl,
    resolve_analysis_tool_order as resolve_analysis_tool_order_impl,
)
from nexus.plugins.builtin.ai_runtime.operation_agent_policy import (
    resolve_issue_override_agent as resolve_issue_override_agent_impl,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.agent_invokers import (
    invoke_copilot_agent_cli as invoke_copilot_agent_cli_impl,
    invoke_gemini_agent_cli as invoke_gemini_agent_cli_impl,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.analysis_invokers import (
    run_copilot_analysis_cli as run_copilot_analysis_cli_impl,
    run_codex_analysis_cli as run_codex_analysis_cli_impl,
    run_gemini_analysis_cli as run_gemini_analysis_cli_impl,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.codex_invoker import (
    invoke_codex_cli as invoke_codex_cli_impl,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.transcription_invokers import (
    transcribe_with_copilot_cli as transcribe_with_copilot_cli_impl,
    transcribe_with_gemini_cli as transcribe_with_gemini_cli_impl,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.whisper_invoker import (
    transcribe_with_local_whisper as transcribe_with_local_whisper_impl,
)
from nexus.plugins.builtin.ai_runtime.provider_registry import (
    parse_provider as parse_provider_impl,
    supports_analysis as supports_analysis_impl,
    unique_tools as unique_tools_impl,
)
from nexus.plugins.builtin.ai_runtime.transcription_service import (
    is_non_transcription_artifact as is_non_transcription_artifact_impl,
    is_transcription_refusal as is_transcription_refusal_impl,
    normalize_local_whisper_model_name as normalize_local_whisper_model_name_impl,
    run_transcription_attempts as run_transcription_attempts_impl,
    resolve_transcription_attempts as resolve_transcription_attempts_impl,
)

logger = logging.getLogger(__name__)


class AIProvider(Enum):
    """AI provider enumeration."""

    COPILOT = "copilot"
    GEMINI = "gemini"
    CODEX = "codex"


class ToolUnavailableError(Exception):
    """Raised when a tool is unavailable or fails."""


class RateLimitedError(Exception):
    """Raised when a tool hits rate limits."""


def _default_tasks_logs_dir(workspace: str, project: str | None = None) -> str:
    """Resolve default logs dir when host app does not provide a resolver."""
    logs_dir = os.path.join(workspace, ".nexus", "tasks", "logs")
    if project:
        logs_dir = os.path.join(logs_dir, project)
    return logs_dir


class AIOrchestrator:
    """Manages AI tool orchestration with fallback support."""

    _rate_limits: dict[str, dict[str, Any]] = {}
    _tool_available: dict[str, bool | float] = {}

    def __init__(self, config: dict[str, Any] | None = None):
        self._tool_available: dict[str, bool | float] = {}
        self._rate_limits: dict[str, dict[str, Any]] = {}
        self.config = config or {}
        self.copilot_cli_path = self.config.get("copilot_cli_path", "copilot")
        self.gemini_cli_path = self.config.get("gemini_cli_path", "gemini")
        self.gemini_model = str(self.config.get("gemini_model", "")).strip()
        self.codex_cli_path = self.config.get("codex_cli_path", "codex")
        self.codex_model = str(self.config.get("codex_model", "")).strip()
        self.tool_preferences = self.config.get("tool_preferences", {})
        self.tool_preferences_resolver: Callable[[str], dict[str, Any] | None] | None = (
            self.config.get(
                "tool_preferences_resolver",
            )
        )
        self.operation_agents = self.config.get("operation_agents", {})
        self.operation_agents_resolver: Callable[[str], dict[str, Any] | None] | None = (
            self.config.get(
                "operation_agents_resolver",
            )
        )
        self.chat_agent_types_resolver: Callable[[str], list[str] | None] | None = self.config.get(
            "chat_agent_types_resolver",
        )
        self.fallback_enabled = self.config.get("fallback_enabled", True)
        self.rate_limit_ttl = self.config.get("rate_limit_ttl", 3600)
        self.max_retries = self.config.get("max_retries", 3)
        self.analysis_timeout = self.config.get("analysis_timeout", 120)
        self.refine_description_timeout = self.config.get("refine_description_timeout", 90)
        self.gemini_transcription_timeout = self.config.get("gemini_transcription_timeout", 60)
        self.copilot_transcription_timeout = self.config.get("copilot_transcription_timeout", 120)
        primary = str(self.config.get("transcription_primary", "gemini")).strip().lower()
        self.transcription_primary = (
            primary if primary in {"gemini", "copilot", "whisper"} else "gemini"
        )
        self.whisper_model = (
            str(self.config.get("whisper_model", "whisper-1")).strip() or "whisper-1"
        )
        self.whisper_language = str(self.config.get("whisper_language", "")).strip().lower() or None
        raw_whisper_languages = str(self.config.get("whisper_languages", "")).strip().lower()
        self.whisper_languages = [
            value.strip() for value in raw_whisper_languages.split(",") if value.strip()
        ]
        self._whisper_model_instance = None
        self._whisper_model_name = ""
        self.get_tasks_logs_dir: Callable[[str, str | None], str] = self.config.get(
            "tasks_logs_dir_resolver",
            _default_tasks_logs_dir,
        )

    @staticmethod
    def _parse_provider(candidate: Any) -> AIProvider | None:
        parsed = parse_provider_impl(candidate, AIProvider)
        return parsed if isinstance(parsed, AIProvider) else None

    @staticmethod
    def _supports_analysis(tool: AIProvider) -> bool:
        return supports_analysis_impl(
            tool,
            gemini_provider=AIProvider.GEMINI,
            copilot_provider=AIProvider.COPILOT,
            codex_provider=AIProvider.CODEX,
        )

    @staticmethod
    def _looks_like_bug_issue(text: str) -> bool:
        candidate = str(text or "").strip().lower()
        if not candidate:
            return False
        bug_markers = (
            "bug",
            "issue",
            "error",
            "exception",
            "traceback",
            "stack trace",
            "fails",
            "failing",
            "failed",
            "crash",
            "broken",
            "regression",
            "hotfix",
            "not working",
            "doesn't work",
            "doesnt work",
        )
        return any(marker in candidate for marker in bug_markers)

    @staticmethod
    def _unique_tools(order: list[AIProvider]) -> list[AIProvider]:
        return unique_tools_impl(order)

    def _resolved_tool_preferences(self, project_name: str | None = None) -> dict[str, Any]:
        if project_name and callable(self.tool_preferences_resolver):
            try:
                resolved = self.tool_preferences_resolver(str(project_name))
                if isinstance(resolved, dict):
                    return resolved
            except Exception as exc:
                logger.warning(
                    "Could not resolve tool preferences for project %s: %s",
                    project_name,
                    exc,
                )
        return self.tool_preferences if isinstance(self.tool_preferences, dict) else {}

    def _resolved_operation_agents(self, project_name: str | None = None) -> dict[str, Any]:
        if project_name and callable(self.operation_agents_resolver):
            try:
                resolved = self.operation_agents_resolver(str(project_name))
                if isinstance(resolved, dict):
                    return resolved
            except Exception as exc:
                logger.warning(
                    "Could not resolve operation agents for project %s: %s",
                    project_name,
                    exc,
                )
        return self.operation_agents if isinstance(self.operation_agents, dict) else {}

    def _fallback_order_from_preferences(self, project_name: str | None = None) -> list[AIProvider]:
        tool_preferences = self._resolved_tool_preferences(project_name)
        return fallback_order_from_preferences_impl(
            resolved_tool_preferences=tool_preferences,
            parse_provider=self._parse_provider,
        )

    def _default_chat_agent_type(self, project_name: str | None = None) -> str:
        if not project_name or not callable(self.chat_agent_types_resolver):
            return ""
        try:
            values = self.chat_agent_types_resolver(str(project_name))
        except Exception as exc:
            logger.warning(
                "Could not resolve chat agent types for project %s: %s", project_name, exc
            )
            return ""
        if not isinstance(values, list):
            return ""
        for item in values:
            normalized = str(item or "").strip().lower()
            if normalized:
                return normalized
        return ""

    def _resolve_issue_override_agent(
        self,
        *,
        task_key: str,
        mapped_agent: str,
        text: str,
        operation_agents: Mapping[str, Any] | None,
    ) -> str:
        return resolve_issue_override_agent_impl(
            task_key=task_key,
            mapped_agent=mapped_agent,
            text=text,
            operation_agents=operation_agents,
            looks_like_bug_issue=self._looks_like_bug_issue,
        )

    def check_tool_available(self, tool: AIProvider) -> bool:
        if tool.value in self._tool_available:
            cached_at = float(self._tool_available.get(f"{tool.value}_cached_at", 0.0))
            if time.time() - cached_at < 300:
                return bool(self._tool_available[tool.value])

        if tool.value in self._rate_limits:
            rate_info = self._rate_limits[tool.value]
            if time.time() < rate_info["until"]:
                logger.warning(
                    "⏸️  %s is rate-limited until %s (retries: %s/%s)",
                    tool.value.upper(),
                    rate_info["until"],
                    rate_info["retries"],
                    self.max_retries,
                )
                return False
            del self._rate_limits[tool.value]

        try:
            if tool == AIProvider.GEMINI:
                path = self.gemini_cli_path
            elif tool == AIProvider.CODEX:
                path = self.codex_cli_path
            else:
                path = self.copilot_cli_path
            result = subprocess.run(
                [path, "--version"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            available = result.returncode == 0
            if available:
                logger.info("✅ %s available", tool.value.upper())
            else:
                logger.warning(
                    "⚠️  %s unavailable: version check failed (exit=%s)",
                    tool.value.upper(),
                    result.returncode,
                )
        except Exception as exc:
            available = False
            logger.warning("⚠️  %s unavailable: %s", tool.value.upper(), exc)

        tool_key = str(tool.value)
        self._tool_available[tool_key] = available
        self._tool_available[f"{tool_key}_cached_at"] = time.time()
        return available

    def get_primary_tool(
        self, agent_name: str | None = None, project_name: str | None = None
    ) -> AIProvider:
        tool_preferences = self._resolved_tool_preferences(project_name)
        if agent_name and agent_name in tool_preferences:
            pref = self._parse_provider(tool_preferences.get(agent_name))
            if pref:
                return pref

        return AIProvider.COPILOT

    def get_fallback_tool(self, primary: AIProvider) -> AIProvider | None:
        if not self.fallback_enabled:
            return None

        fallbacks = [tool for tool in AIProvider if tool != primary]
        for fallback in fallbacks:
            if self.check_tool_available(fallback):
                logger.info(
                    "🔄 Fallback ready from %s → %s (will use only if primary fails)",
                    primary.value,
                    fallback.value,
                )
                return fallback

        logger.error("❌ No fallback providers available for %s", primary.value)
        return None

    def _resolve_analysis_tool_order(
        self,
        task: str,
        project_name: str | None = None,
        text: str = "",
    ) -> list[AIProvider]:
        operation_agents = self._resolved_operation_agents(project_name)
        if not isinstance(operation_agents, Mapping):
            operation_agents = {}
        return resolve_analysis_tool_order_impl(
            task=task,
            text=text,
            project_name=project_name,
            fallback_enabled=bool(self.fallback_enabled),
            operation_agents=operation_agents,
            default_chat_agent_type=self._default_chat_agent_type(project_name),
            resolve_issue_override_agent=self._resolve_issue_override_agent,
            get_primary_tool=lambda agent, project: self.get_primary_tool(
                agent,
                project_name=project,
            ),
            fallback_order_from_preferences_fn=self._fallback_order_from_preferences,
            unique_tools=self._unique_tools,
            supports_analysis=self._supports_analysis,
            gemini_provider=AIProvider.GEMINI,
            copilot_provider=AIProvider.COPILOT,
        )

    def _resolve_transcription_attempts(self, project_name: str | None = None) -> list[str]:
        operation_agents = self._resolved_operation_agents(project_name)
        if not isinstance(operation_agents, Mapping):
            operation_agents = {}
        return resolve_transcription_attempts_impl(
            project_name=project_name,
            operation_agents=operation_agents,
            fallback_enabled=bool(self.fallback_enabled),
            transcription_primary=self.transcription_primary,
            get_primary_tool=lambda agent, project: self.get_primary_tool(
                agent, project_name=project
            ),
            fallback_order_from_preferences_fn=self._fallback_order_from_preferences,
            unique_tools=self._unique_tools,
            gemini_provider=AIProvider.GEMINI,
            copilot_provider=AIProvider.COPILOT,
            warn_unsupported_mapped_provider=lambda provider, agent: logger.warning(
                "Ignoring unsupported transcription provider '%s' for mapped agent '%s'; "
                "falling back to transcription_primary",
                provider,
                agent,
            ),
        )

    def _run_analysis_with_provider(
        self, tool: AIProvider, text: str, task: str, **kwargs
    ) -> dict[str, Any]:
        return run_analysis_with_provider_impl(
            tool=tool,
            gemini_provider=AIProvider.GEMINI,
            copilot_provider=AIProvider.COPILOT,
            codex_provider=AIProvider.CODEX,
            run_gemini_cli_analysis=self._run_gemini_cli_analysis,
            run_copilot_analysis=self._run_copilot_analysis,
            run_codex_analysis=self._run_codex_analysis,
            text=text,
            task=task,
            kwargs=kwargs,
            tool_unavailable_error=ToolUnavailableError,
        )

    def _get_tool_order(self, agent_name: str | None = None, use_gemini: bool = False) -> list:
        """Return all known tools in priority order for this agent.

        The preferred tool goes first; the rest follow in enum declaration order.
        Adding a new provider (Claude, Codex, …) only requires extending AIProvider
        and implementing _invoke_tool dispatch — no other changes needed.
        """
        preferred = AIProvider.GEMINI if use_gemini else self.get_primary_tool(agent_name)
        all_tools = list(AIProvider)
        return [preferred] + [t for t in all_tools if t != preferred]

    def _invoke_tool(
        self,
        tool: AIProvider,
        agent_prompt: str,
        workspace_dir: str,
        agents_dir: str,
        base_dir: str,
        issue_num: str | None = None,
        log_subdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int | None:
        """Dispatch to the correct CLI for *tool*. Extend here when adding new providers."""
        if tool == AIProvider.COPILOT:
            return self._invoke_copilot(
                agent_prompt,
                workspace_dir,
                agents_dir,
                base_dir,
                issue_num=issue_num,
                log_subdir=log_subdir,
                env=env,
            )
        if tool == AIProvider.GEMINI:
            return self._invoke_gemini_agent(
                agent_prompt,
                workspace_dir,
                agents_dir,
                base_dir,
                issue_num=issue_num,
                log_subdir=log_subdir,
                env=env,
            )
        if tool == AIProvider.CODEX:
            return self._invoke_codex(
                agent_prompt,
                workspace_dir,
                agents_dir,
                base_dir,
                issue_num=issue_num,
                log_subdir=log_subdir,
                env=env,
            )
        raise ToolUnavailableError(f"No invoker implemented for tool: {tool.value}")

    def record_rate_limit(self, tool: AIProvider, retry_count: int = 1):
        self._rate_limits[tool.value] = {
            "until": time.time() + self.rate_limit_ttl,
            "retries": retry_count,
        }
        logger.warning("⏸️  %s rate-limited for %ss", tool.value.upper(), self.rate_limit_ttl)

    def _record_rate_limit_with_context(
        self,
        tool: AIProvider,
        error: Exception,
        retry_count: int = 1,
        context: str = "",
    ) -> None:
        """Record rate-limit cooldown and include root-cause context in logs."""
        self.record_rate_limit(tool, retry_count=retry_count)
        message = str(error).strip() or repr(error)
        if len(message) > 600:
            message = f"{message[:600]}..."
        context_prefix = f" ({context})" if context else ""
        logger.warning(
            "⚠️  %s rate-limit detail%s: %s",
            tool.value.upper(),
            context_prefix,
            message,
        )

    def record_failure(self, tool: AIProvider):
        if tool.value not in self._rate_limits:
            self._tool_available[tool.value] = False
            logger.error("❌ %s marked unavailable", tool.value.upper())
            return

        current = self._rate_limits[tool.value]
        current["retries"] += 1
        if current["retries"] >= self.max_retries:
            logger.error("❌ %s exceeded max retries, marking unavailable", tool.value.upper())
            self._tool_available[tool.value] = False

    def invoke_agent(
        self,
        agent_prompt: str,
        workspace_dir: str,
        agents_dir: str,
        base_dir: str,
        issue_url: str | None = None,
        agent_name: str | None = None,
        use_gemini: bool = False,
        exclude_tools: list | None = None,
        log_subdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int | None, AIProvider]:
        """Try each available tool in priority order, skipping any in *exclude_tools*.

        *exclude_tools* is a list of tool value strings (e.g. ["gemini"]) that should
        be skipped entirely — used by the retry path so a crashed tool is not retried.
        Adding a new provider only requires extending AIProvider + _invoke_tool.
        """
        return invoke_agent_with_fallback_impl(
            issue_url=issue_url,
            exclude_tools=exclude_tools,
            get_tool_order=lambda: self._get_tool_order(agent_name, use_gemini),
            check_tool_available=self.check_tool_available,
            invoke_tool=lambda tool, issue_num: self._invoke_tool(
                tool,
                agent_prompt,
                workspace_dir,
                agents_dir,
                base_dir,
                issue_num=issue_num,
                log_subdir=log_subdir,
                env=env,
            ),
            record_rate_limit_with_context=lambda tool, exc, context: self._record_rate_limit_with_context(
                tool,
                exc,
                context=context,
            ),
            record_failure=self.record_failure,
            rate_limited_error_type=RateLimitedError,
            tool_unavailable_error_type=ToolUnavailableError,
            logger=logger,
        )

    def _invoke_copilot(
        self,
        agent_prompt: str,
        workspace_dir: str,
        agents_dir: str,
        base_dir: str,
        issue_num: str | None = None,
        log_subdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int | None:
        return invoke_copilot_agent_cli_impl(
            check_tool_available=self.check_tool_available,
            copilot_provider=AIProvider.COPILOT,
            copilot_cli_path=self.copilot_cli_path,
            get_tasks_logs_dir=self.get_tasks_logs_dir,
            tool_unavailable_error=ToolUnavailableError,
            rate_limited_error=RateLimitedError,
            logger=logger,
            agent_prompt=agent_prompt,
            workspace_dir=workspace_dir,
            agents_dir=agents_dir,
            base_dir=base_dir,
            issue_num=issue_num,
            log_subdir=log_subdir,
            env=env,
        )

    def _invoke_gemini_agent(
        self,
        agent_prompt: str,
        workspace_dir: str,
        agents_dir: str,
        base_dir: str,
        issue_num: str | None = None,
        log_subdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int | None:
        return invoke_gemini_agent_cli_impl(
            check_tool_available=self.check_tool_available,
            gemini_provider=AIProvider.GEMINI,
            gemini_cli_path=self.gemini_cli_path,
            gemini_model=self.gemini_model,
            get_tasks_logs_dir=self.get_tasks_logs_dir,
            tool_unavailable_error=ToolUnavailableError,
            rate_limited_error=RateLimitedError,
            logger=logger,
            agent_prompt=agent_prompt,
            workspace_dir=workspace_dir,
            agents_dir=agents_dir,
            issue_num=issue_num,
            log_subdir=log_subdir,
            env=env,
        )

    def _invoke_codex(
        self,
        agent_prompt: str,
        workspace_dir: str,
        agents_dir: str,
        base_dir: str,
        issue_num: str | None = None,
        log_subdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> int | None:
        return invoke_codex_cli_impl(
            check_tool_available=self.check_tool_available,
            codex_provider=AIProvider.CODEX,
            codex_cli_path=self.codex_cli_path,
            codex_model=self.codex_model,
            get_tasks_logs_dir=self.get_tasks_logs_dir,
            tool_unavailable_error=ToolUnavailableError,
            logger=logger,
            agent_prompt=agent_prompt,
            workspace_dir=workspace_dir,
            issue_num=issue_num,
            log_subdir=log_subdir,
            env=env,
        )

    def transcribe_audio(self, audio_file_path: str, project_name: str | None = None) -> str | None:
        attempts = self._resolve_transcription_attempts(project_name=project_name)
        return run_transcription_attempts_impl(
            attempts=attempts,
            audio_file_path=audio_file_path,
            transcribe_with_whisper=self._transcribe_with_whisper_api,
            transcribe_with_gemini=self._transcribe_with_gemini_cli,
            transcribe_with_copilot=self._transcribe_with_copilot_cli,
            rate_limited_error_type=RateLimitedError,
            record_rate_limit_with_context=lambda tool, exc, context: self._record_rate_limit_with_context(
                tool,
                exc,
                context=context,
            ),
            gemini_provider=AIProvider.GEMINI,
            copilot_provider=AIProvider.COPILOT,
            logger=logger,
        )

    def _transcribe_with_whisper_api(self, audio_file_path: str) -> str | None:
        result = transcribe_with_local_whisper_impl(
            audio_file_path=audio_file_path,
            current_model_instance=self._whisper_model_instance,
            current_model_name=self._whisper_model_name,
            configured_model=self.whisper_model,
            whisper_language=self.whisper_language,
            whisper_languages=self.whisper_languages,
            normalize_local_whisper_model_name=self._normalize_local_whisper_model_name,
            tool_unavailable_error=ToolUnavailableError,
            logger=logger,
        )
        self._whisper_model_instance = result["model_instance"]
        self._whisper_model_name = str(result["model_name"])
        return str(result["text"])

    @staticmethod
    def _normalize_local_whisper_model_name(configured_model: str) -> str:
        return normalize_local_whisper_model_name_impl(configured_model)

    @staticmethod
    def _is_transcription_refusal(text: str) -> bool:
        return is_transcription_refusal_impl(text)

    @staticmethod
    def _is_non_transcription_artifact(text: str, audio_file_path: str) -> bool:
        return is_non_transcription_artifact_impl(text, audio_file_path)

    def _transcribe_with_gemini_cli(self, audio_file_path: str) -> str | None:
        return transcribe_with_gemini_cli_impl(
            check_tool_available=self.check_tool_available,
            gemini_provider=AIProvider.GEMINI,
            gemini_cli_path=self.gemini_cli_path,
            strip_cli_tool_output=self._strip_cli_tool_output,
            is_non_transcription_artifact=self._is_non_transcription_artifact,
            tool_unavailable_error=ToolUnavailableError,
            rate_limited_error=RateLimitedError,
            logger=logger,
            audio_file_path=audio_file_path,
            timeout=self.gemini_transcription_timeout,
        )

    def _transcribe_with_copilot_cli(self, audio_file_path: str) -> str | None:
        return transcribe_with_copilot_cli_impl(
            check_tool_available=self.check_tool_available,
            copilot_provider=AIProvider.COPILOT,
            copilot_cli_path=self.copilot_cli_path,
            strip_cli_tool_output=self._strip_cli_tool_output,
            is_non_transcription_artifact=self._is_non_transcription_artifact,
            tool_unavailable_error=ToolUnavailableError,
            logger=logger,
            audio_file_path=audio_file_path,
            timeout=self.copilot_transcription_timeout,
        )

    def run_text_to_speech_analysis(
        self, text: str, task: str = "classify", **kwargs
    ) -> dict[str, Any]:
        project_name = kwargs.get("project_name")
        tool_order = self._resolve_analysis_tool_order(task, project_name=project_name, text=text)
        mapped_agent = self._resolve_analysis_mapped_agent(
            task=task,
            text=text,
            project_name=project_name,
        )
        if tool_order:
            preferred = tool_order[0]
            preferred_name = str(getattr(preferred, "value", preferred))
            fallback_names = [str(getattr(tool, "value", tool)) for tool in tool_order[1:]]
            logger.info(
                "🧭 Analysis provider order: task=%s project=%s mapped_agent=%s order=%s",
                task,
                project_name or "default",
                mapped_agent or "unknown",
                " -> ".join([preferred_name] + fallback_names),
            )
            if not self.check_tool_available(preferred):
                fallback_display = ", ".join(fallback_names) if fallback_names else "none"
                logger.warning(
                    "⚠️ Preferred provider '%s' unavailable for task '%s' (project=%s); "
                    "mapped_agent=%s; falling back to: %s",
                    preferred_name,
                    task,
                    project_name or "default",
                    mapped_agent or "unknown",
                    fallback_display,
                )
        return run_analysis_attempts_impl(
            tool_order=tool_order,
            text=text,
            task=task,
            kwargs=kwargs,
            invoke_provider=lambda tool, text_arg, task_arg, kw: self._run_analysis_with_provider(
                tool, text_arg, task_arg, **kw
            ),
            rate_limited_error_type=RateLimitedError,
            record_rate_limit_with_context=lambda tool, exc, retry_count, context: self._record_rate_limit_with_context(
                tool,
                exc,
                retry_count=retry_count,
                context=context,
            ),
            get_default_analysis_result=self._get_default_analysis_result,
            logger=logger,
        )

    def _resolve_analysis_mapped_agent(
        self,
        *,
        task: str,
        text: str,
        project_name: str | None,
    ) -> str:
        def _coerce_chat_agent_type(chat_config: Any) -> str:
            if isinstance(chat_config, str):
                return str(chat_config).strip()
            if isinstance(chat_config, list):
                for item in chat_config:
                    if isinstance(item, str) and item.strip():
                        return item.strip()
                    if isinstance(item, Mapping):
                        explicit = str(item.get("agent_type") or "").strip()
                        if explicit:
                            return explicit
                        for key in item:
                            normalized = str(key).strip()
                            if normalized:
                                return normalized
            if isinstance(chat_config, Mapping):
                explicit = str(chat_config.get("agent_type") or "").strip()
                if explicit:
                    return explicit
                default_item = chat_config.get("default")
                if isinstance(default_item, str) and default_item.strip():
                    return default_item.strip()
                if isinstance(default_item, Mapping):
                    nested = str(default_item.get("agent_type") or "").strip()
                    if nested:
                        return nested
                for key in chat_config:
                    normalized = str(key).strip()
                    if normalized and normalized not in {"default", "agent_type"}:
                        return normalized
            return ""

        operation_agents = self._resolved_operation_agents(project_name)
        if not isinstance(operation_agents, Mapping):
            operation_agents = {}

        task_key = str(task or "").strip().lower()
        if task_key == "chat":
            mapped_agent = _coerce_chat_agent_type(operation_agents.get(task_key))
            if not mapped_agent:
                mapped_agent = self._default_chat_agent_type(project_name)
            if not mapped_agent:
                mapped_agent = str(operation_agents.get("default") or "").strip()
        else:
            mapped_agent = str(
                operation_agents.get(task_key) or operation_agents.get("default") or ""
            ).strip()

        mapped_agent = self._resolve_issue_override_agent(
            task_key=task_key,
            mapped_agent=mapped_agent,
            text=text,
            operation_agents=operation_agents,
        )
        return mapped_agent

    def _run_gemini_cli_analysis(self, text: str, task: str, **kwargs) -> dict[str, Any]:
        return run_gemini_analysis_cli_impl(
            check_tool_available=self.check_tool_available,
            gemini_provider=AIProvider.GEMINI,
            gemini_cli_path=self.gemini_cli_path,
            build_analysis_prompt=self._build_analysis_prompt,
            parse_analysis_result=self._parse_analysis_result,
            tool_unavailable_error=ToolUnavailableError,
            rate_limited_error=RateLimitedError,
            text=text,
            task=task,
            timeout=self.analysis_timeout,
            kwargs=kwargs,
        )

    def _run_copilot_analysis(self, text: str, task: str, **kwargs) -> dict[str, Any]:
        timeout = (
            self.refine_description_timeout
            if task == "refine_description"
            else self.analysis_timeout
        )
        return run_copilot_analysis_cli_impl(
            check_tool_available=self.check_tool_available,
            copilot_provider=AIProvider.COPILOT,
            copilot_cli_path=self.copilot_cli_path,
            build_analysis_prompt=self._build_analysis_prompt,
            parse_analysis_result=self._parse_analysis_result,
            tool_unavailable_error=ToolUnavailableError,
            text=text,
            task=task,
            timeout=timeout,
            kwargs=kwargs,
        )

    def _run_codex_analysis(self, text: str, task: str, **kwargs) -> dict[str, Any]:
        timeout = (
            self.refine_description_timeout
            if task == "refine_description"
            else self.analysis_timeout
        )
        return run_codex_analysis_cli_impl(
            check_tool_available=self.check_tool_available,
            codex_provider=AIProvider.CODEX,
            codex_cli_path=self.codex_cli_path,
            codex_model=self.codex_model,
            build_analysis_prompt=self._build_analysis_prompt,
            parse_analysis_result=self._parse_analysis_result,
            tool_unavailable_error=ToolUnavailableError,
            rate_limited_error=RateLimitedError,
            text=text,
            task=task,
            timeout=timeout,
            kwargs=kwargs,
        )

    def _build_analysis_prompt(self, text: str, task: str, **kwargs) -> str:
        return build_analysis_prompt_impl(text, task, **kwargs)

    @staticmethod
    def _strip_cli_tool_output(text: str) -> str:
        return strip_cli_tool_output_impl(text)

    def _parse_analysis_result(self, output: str, task: str) -> dict[str, Any]:
        return parse_analysis_result_impl(output, task, logger=logger)

    def _get_default_analysis_result(self, task: str, **kwargs) -> dict[str, Any]:
        if task == "classify":
            return {
                "project": kwargs.get("projects", ["case-italia"])[0],
                "type": kwargs.get("types", ["feature"])[0],
                "task_name": "generic-task",
            }
        if task == "route":
            return {"agent": "ProjectLead", "type": "routing", "confidence": 0}
        if task == "generate_name":
            text = kwargs.get("text", "")
            words = text.split()[:3]
            if not words:
                return {"text": "generic-task"}
            return {"text": "-".join(words).lower()}
        if task == "refine_description":
            return {"text": kwargs.get("text", "")}
        if task == "detect_intent":
            return {"intent": "task"}
        if task == "detect_feature_ideation":
            return {
                "feature_ideation": False,
                "confidence": 0.0,
                "reason": "default-no-match",
            }
        if task == "chat":
            return {"text": "I'm offline right now, how can I help you later?"}
        return {}


def register_plugins(registry) -> None:
    """Register built-in AI runtime orchestrator plugin."""
    from nexus.plugins import PluginKind

    registry.register_factory(
        kind=PluginKind.AI_PROVIDER,
        name="ai-runtime-orchestrator",
        version="0.1.0",
        factory=lambda config: AIOrchestrator(config),
        description="Copilot/Gemini/Codex orchestration with fallback and cooldown handling",
    )
