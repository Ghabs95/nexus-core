"""Built-in plugin: AI runtime orchestration for Copilot CLI and Gemini CLI."""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class AIProvider(Enum):
    """AI provider enumeration."""

    COPILOT = "copilot"
    GEMINI = "gemini"


class ToolUnavailableError(Exception):
    """Raised when a tool is unavailable or fails."""


class RateLimitedError(Exception):
    """Raised when a tool hits rate limits."""


def _default_tasks_logs_dir(workspace: str, project: Optional[str] = None) -> str:
    """Resolve default logs dir when host app does not provide a resolver."""
    logs_dir = os.path.join(workspace, ".nexus", "tasks", "logs")
    if project:
        logs_dir = os.path.join(logs_dir, project)
    return logs_dir


class AIOrchestrator:
    """Manages AI tool orchestration with fallback support."""

    _rate_limits: Dict[str, Dict[str, Any]] = {}
    _tool_available: Dict[str, Any] = {}

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.gemini_cli_path = self.config.get("gemini_cli_path", "gemini")
        self.gemini_model = str(self.config.get("gemini_model", "")).strip()
        self.copilot_cli_path = self.config.get("copilot_cli_path", "copilot")
        self.tool_preferences = self.config.get("tool_preferences", {})
        self.fallback_enabled = self.config.get("fallback_enabled", True)
        self.rate_limit_ttl = self.config.get("rate_limit_ttl", 3600)
        self.max_retries = self.config.get("max_retries", 2)
        self.analysis_timeout = self.config.get("analysis_timeout", 30)
        self.refine_description_timeout = self.config.get("refine_description_timeout", 90)
        self.gemini_transcription_timeout = self.config.get("gemini_transcription_timeout", 60)
        self.copilot_transcription_timeout = self.config.get("copilot_transcription_timeout", 120)
        primary = str(self.config.get("transcription_primary", "gemini")).strip().lower()
        self.transcription_primary = AIProvider.GEMINI if primary == "gemini" else AIProvider.COPILOT
        self.get_tasks_logs_dir: Callable[[str, Optional[str]], str] = self.config.get(
            "tasks_logs_dir_resolver",
            _default_tasks_logs_dir,
        )

    def check_tool_available(self, tool: AIProvider) -> bool:
        if tool.value in self._tool_available:
            cached_at = self._tool_available.get(f"{tool.value}_cached_at", 0)
            if time.time() - cached_at < 300:
                return self._tool_available[tool.value]

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
            path = self.gemini_cli_path if tool == AIProvider.GEMINI else self.copilot_cli_path
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

        self._tool_available[tool.value] = available
        self._tool_available[f"{tool.value}_cached_at"] = time.time()
        return available

    def get_primary_tool(self, agent_name: Optional[str] = None) -> AIProvider:
        if agent_name and agent_name in self.tool_preferences:
            pref = self.tool_preferences[agent_name]
            return AIProvider.COPILOT if pref == "copilot" else AIProvider.GEMINI

        return AIProvider.COPILOT

    def get_fallback_tool(self, primary: AIProvider) -> Optional[AIProvider]:
        if not self.fallback_enabled:
            return None

        fallback = AIProvider.GEMINI if primary == AIProvider.COPILOT else AIProvider.COPILOT
        if self.check_tool_available(fallback):
            logger.info("🔄 Fallback ready from %s → %s (will use only if primary fails)", primary.value, fallback.value)
            return fallback

        logger.error("❌ Fallback %s unavailable", fallback.value)
        return None

    def _get_tool_order(self, agent_name: Optional[str] = None, use_gemini: bool = False) -> list:
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
        issue_num: Optional[str] = None,
        log_subdir: Optional[str] = None,
    ) -> Optional[int]:
        """Dispatch to the correct CLI for *tool*. Extend here when adding new providers."""
        if tool == AIProvider.COPILOT:
            return self._invoke_copilot(
                agent_prompt, workspace_dir, agents_dir, base_dir,
                issue_num=issue_num, log_subdir=log_subdir,
            )
        if tool == AIProvider.GEMINI:
            return self._invoke_gemini_agent(
                agent_prompt, workspace_dir, agents_dir, base_dir,
                issue_num=issue_num, log_subdir=log_subdir,
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
        issue_url: Optional[str] = None,
        agent_name: Optional[str] = None,
        use_gemini: bool = False,
        exclude_tools: Optional[list] = None,
        log_subdir: Optional[str] = None,
    ) -> Tuple[Optional[int], AIProvider]:
        """Try each available tool in priority order, skipping any in *exclude_tools*.

        *exclude_tools* is a list of tool value strings (e.g. ["gemini"]) that should
        be skipped entirely — used by the retry path so a crashed tool is not retried.
        Adding a new provider only requires extending AIProvider + _invoke_tool.
        """
        issue_num = None
        if issue_url:
            match = re.search(r"/issues/(\d+)", issue_url)
            issue_num = match.group(1) if match else None

        excluded = set(exclude_tools or [])
        ordered = self._get_tool_order(agent_name, use_gemini)
        candidates = [t for t in ordered if t.value not in excluded]

        if not candidates:
            raise ToolUnavailableError(
                f"All tools excluded. Order: {[t.value for t in ordered]}, "
                f"Excluded: {list(excluded)}"
            )

        tried: list = []
        for tool in candidates:
            if not self.check_tool_available(tool):
                logger.warning("⏭️  Skipping unavailable tool: %s", tool.value)
                tried.append(f"{tool.value}(unavailable)")
                continue
            try:
                if tried:
                    logger.info("🔄 Trying next tool %s (previously tried: %s)", tool.value, tried)
                pid = self._invoke_tool(
                    tool, agent_prompt, workspace_dir, agents_dir, base_dir,
                    issue_num=issue_num, log_subdir=log_subdir,
                )
                if pid:
                    if tried:
                        logger.info("✅ %s succeeded after: %s", tool.value, tried)
                    return pid, tool
                tried.append(f"{tool.value}(no-pid)")
            except RateLimitedError as exc:
                self._record_rate_limit_with_context(tool, exc, context="invoke_agent")
                tried.append(f"{tool.value}(rate-limited)")
            except Exception as exc:
                logger.error("❌ %s invocation failed: %s", tool.value, exc)
                self.record_failure(tool)
                tried.append(f"{tool.value}(error)")

        raise ToolUnavailableError(
            f"All AI tools exhausted. Tried: {tried}, Excluded: {list(excluded)}"
        )

    def _invoke_copilot(
        self,
        agent_prompt: str,
        workspace_dir: str,
        agents_dir: str,
        base_dir: str,
        issue_num: Optional[str] = None,
        log_subdir: Optional[str] = None,
    ) -> Optional[int]:
        if not self.check_tool_available(AIProvider.COPILOT):
            raise ToolUnavailableError("Copilot not available")

        cmd = [
            self.copilot_cli_path,
            "-p",
            agent_prompt,
            "--add-dir",
            base_dir,
            "--add-dir",
            workspace_dir,
            "--add-dir",
            agents_dir,
            "--allow-all-tools",
        ]

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        log_dir = self.get_tasks_logs_dir(workspace_dir, log_subdir)
        os.makedirs(log_dir, exist_ok=True)
        log_suffix = f"{issue_num}_{timestamp}" if issue_num else timestamp
        log_path = os.path.join(log_dir, f"copilot_{log_suffix}.log")

        logger.info("🤖 Launching Copilot CLI agent")
        logger.info("   Workspace: %s", workspace_dir)
        logger.info("   Log: %s", log_path)

        try:
            log_file = open(log_path, "w", encoding="utf-8")
            process = subprocess.Popen(
                cmd,
                cwd=workspace_dir,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            logger.info("🚀 Copilot launched (PID: %s)", process.pid)
            return process.pid
        except Exception as exc:
            logger.error("❌ Copilot launch failed: %s", exc)
            raise

    def _invoke_gemini_agent(
        self,
        agent_prompt: str,
        workspace_dir: str,
        agents_dir: str,
        base_dir: str,
        issue_num: Optional[str] = None,
        log_subdir: Optional[str] = None,
    ) -> Optional[int]:
        if not self.check_tool_available(AIProvider.GEMINI):
            raise ToolUnavailableError("Gemini CLI not available")

        cmd = [
            self.gemini_cli_path,
            "--prompt",
            agent_prompt,
            "--include-directories",
            agents_dir,
            "--yolo",
        ]
        if self.gemini_model:
            cmd.extend(["--model", self.gemini_model])

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        log_dir = self.get_tasks_logs_dir(workspace_dir, log_subdir)
        os.makedirs(log_dir, exist_ok=True)
        log_suffix = f"{issue_num}_{timestamp}" if issue_num else timestamp
        log_path = os.path.join(log_dir, f"gemini_{log_suffix}.log")

        logger.info("🤖 Launching Gemini CLI agent")
        logger.info("   Workspace: %s", workspace_dir)
        logger.info("   Log: %s", log_path)

        def _read_log_excerpt(max_chars: int = 2000) -> str:
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
                    data = handle.read()
                if len(data) <= max_chars:
                    return data
                return data[-max_chars:]
            except Exception:
                return ""

        try:
            log_file = open(log_path, "w", encoding="utf-8")
            process = subprocess.Popen(
                cmd,
                cwd=workspace_dir,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            logger.info("🚀 Gemini launched (PID: %s)", process.pid)

            # Detect immediate startup failure so invoke_agent can fallback to the next tool.
            time.sleep(1.5)
            exit_code = process.poll()
            if exit_code is not None:
                log_excerpt = _read_log_excerpt().lower()
                if (
                    "ratelimitexceeded" in log_excerpt
                    or "status 429" in log_excerpt
                    or "no capacity available" in log_excerpt
                ):
                    raise RateLimitedError(
                        f"Gemini exited immediately with rate limit (exit={exit_code})"
                    )
                raise ToolUnavailableError(
                    f"Gemini exited immediately (exit={exit_code})"
                )

            return process.pid
        except Exception as exc:
            logger.error("❌ Gemini launch failed: %s", exc)
            raise

    def transcribe_audio_cli(self, audio_file_path: str) -> Optional[str]:
        primary = self.transcription_primary
        fallback = self.get_fallback_tool(primary)

        try:
            text = (
                self._transcribe_with_gemini_cli(audio_file_path)
                if primary == AIProvider.GEMINI
                else self._transcribe_with_copilot_cli(audio_file_path)
            )
            if text:
                logger.info("✅ Transcription successful with %s", primary.value)
                return text
        except RateLimitedError as exc:
            self._record_rate_limit_with_context(primary, exc, context="transcribe")
        except Exception as exc:
            logger.warning("⚠️  %s transcription failed: %s", primary.value, exc)

        if fallback:
            try:
                text = (
                    self._transcribe_with_gemini_cli(audio_file_path)
                    if fallback == AIProvider.GEMINI
                    else self._transcribe_with_copilot_cli(audio_file_path)
                )
                if text:
                    logger.info("✅ Fallback transcription succeeded with %s", fallback.value)
                    return text
            except Exception as exc:
                logger.error("❌ Fallback transcription also failed: %s", exc)

        logger.error("❌ All transcription tools failed")
        return None

    @staticmethod
    def _is_transcription_refusal(text: str) -> bool:
        normalized = (text or "").lower().strip()
        if not normalized:
            return True

        refusal_markers = [
            "cannot directly transcribe audio",
            "can't directly transcribe audio",
            "cannot transcribe audio",
            "can't transcribe audio",
            "unable to transcribe audio",
            "capabilities are limited to text-based",
            "i do not have the ability to listen",
            "as a text-based ai",
            "i can't access audio",
            "i cannot access audio",
        ]
        return any(marker in normalized for marker in refusal_markers)

    @staticmethod
    def _is_non_transcription_artifact(text: str, audio_file_path: str) -> bool:
        normalized = (text or "").strip()
        if not normalized:
            return True

        if AIOrchestrator._is_transcription_refusal(normalized):
            return True

        audio_basename = os.path.basename(audio_file_path).lower()
        lowered = normalized.lower()

        if lowered == audio_basename:
            return True

        if lowered == f"file: {audio_basename}":
            return True

        if re.fullmatch(r"file:\s*[^\n\r]+\.(ogg|mp3|wav|m4a)\s*", lowered):
            return True

        if "permission denied and could not request permission from user" in lowered:
            return True

        if "i'm unable to transcribe the audio file" in lowered:
            return True

        if re.search(r"(?m)^\$\s", normalized):
            return True

        if re.search(r"(?m)^✗\s", normalized):
            return True

        debug_markers = [
            "check for transcription tools",
            "check whisper availability",
            "transcribe with whisper",
            "install whisper",
            "pip install openai-whisper",
            "which whisper",
            "which ffmpeg",
        ]
        if any(marker in lowered for marker in debug_markers):
            return True

        return False

    def _transcribe_with_gemini_cli(self, audio_file_path: str) -> Optional[str]:
        if not self.check_tool_available(AIProvider.GEMINI):
            raise ToolUnavailableError("Gemini CLI not available")

        if not os.path.exists(audio_file_path):
            raise ValueError(f"Audio file not found: {audio_file_path}")

        logger.info("🎧 Transcribing with Gemini: %s", audio_file_path)

        prompt = (
            "You are a speech-to-text (STT) transcriber. "
            "Transcribe only the spoken words from the provided audio file.\n"
            "Output rules:\n"
            "- Return ONLY the transcript text\n"
            "- Do NOT summarize, explain, or describe the file\n"
            "- Do NOT include labels like 'File:' or any metadata\n"
            "- Do NOT include apologies or capability statements\n"
            f"Audio file path: {audio_file_path}"
        )
        try:
            result = subprocess.run(
                [self.gemini_cli_path, "-p", prompt],
                capture_output=True,
                text=True,
                timeout=self.gemini_transcription_timeout,
            )

            if result.returncode != 0:
                if "rate limit" in result.stderr.lower() or "quota" in result.stderr.lower():
                    raise RateLimitedError(f"Gemini rate-limited: {result.stderr}")
                raise Exception(f"Gemini error: {result.stderr}")

            text = self._strip_cli_tool_output(result.stdout).strip()
            if text:
                if self._is_non_transcription_artifact(text, audio_file_path):
                    raise Exception("Gemini returned non-transcription content")
                return text
            raise Exception("Gemini returned empty transcription")

        except subprocess.TimeoutExpired as exc:
            raise Exception(f"Gemini transcription timed out (>{self.gemini_transcription_timeout}s)") from exc

    def _transcribe_with_copilot_cli(self, audio_file_path: str) -> Optional[str]:
        if not self.check_tool_available(AIProvider.COPILOT):
            raise ToolUnavailableError("Copilot CLI not available")

        if not os.path.exists(audio_file_path):
            raise ValueError(f"Audio file not found: {audio_file_path}")

        logger.info("🎧 Transcribing with Copilot (fallback): %s", audio_file_path)

        try:
            with tempfile.TemporaryDirectory(prefix="nexus_audio_") as temp_dir:
                audio_basename = os.path.basename(audio_file_path)
                staged_audio_path = os.path.join(temp_dir, audio_basename)
                shutil.copy2(audio_file_path, staged_audio_path)

                prompt = (
                    "You are a speech-to-text (STT) transcriber. "
                    "Transcribe only the spoken words from the attached audio file.\n"
                    "Output rules:\n"
                    "- Return ONLY the transcript text\n"
                    "- Do NOT summarize, explain, or describe the file\n"
                    "- Do NOT include labels like 'File:' or any metadata\n"
                    "- Do NOT include apologies or capability statements\n"
                    f"Audio file name: {audio_basename}"
                )

                result = subprocess.run(
                    [
                        self.copilot_cli_path,
                        "-p",
                        prompt,
                        "--add-dir",
                        temp_dir,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=self.copilot_transcription_timeout,
                )

            if result.returncode != 0:
                raise Exception(f"Copilot error: {result.stderr}")

            text = self._strip_cli_tool_output(result.stdout).strip()
            if text:
                if self._is_non_transcription_artifact(text, audio_file_path):
                    raise Exception("Copilot returned non-transcription content")
                return text
            raise Exception("Copilot returned empty transcription")

        except subprocess.TimeoutExpired as exc:
            raise Exception(f"Copilot transcription timed out (>{self.copilot_transcription_timeout}s)") from exc

    def run_text_to_speech_analysis(self, text: str, task: str = "classify", **kwargs) -> Dict[str, Any]:
        primary = AIProvider.GEMINI
        fallback = self.get_fallback_tool(primary)

        result = None

        try:
            result = self._run_gemini_cli_analysis(text, task, **kwargs)
            if result:
                return result
        except RateLimitedError as exc:
            self._record_rate_limit_with_context(primary, exc, context=f"analysis:{task}")
        except Exception as exc:
            logger.warning("⚠️  %s analysis failed: %s", primary.value, exc)

        if fallback:
            try:
                result = self._run_copilot_analysis(text, task, **kwargs)
                if result:
                    logger.info("✅ Fallback analysis succeeded with %s", fallback.value)
                    return result
            except Exception as exc:
                logger.error("❌ Fallback analysis also failed: %s", exc)

        logger.warning("⚠️  All tools failed for %s, returning default", task)
        return self._get_default_analysis_result(task, text=text, **kwargs)

    def _run_gemini_cli_analysis(self, text: str, task: str, **kwargs) -> Dict[str, Any]:
        if not self.check_tool_available(AIProvider.GEMINI):
            raise ToolUnavailableError("Gemini CLI not available")

        prompt = self._build_analysis_prompt(text, task, **kwargs)

        try:
            result = subprocess.run(
                [self.gemini_cli_path, "-p", prompt],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                if "rate limit" in result.stderr.lower() or "quota" in result.stderr.lower():
                    raise RateLimitedError(f"Gemini rate-limited: {result.stderr}")
                raise Exception(f"Gemini error: {result.stderr}")

            return self._parse_analysis_result(result.stdout, task)
        except subprocess.TimeoutExpired as exc:
            raise Exception(f"Gemini analysis timed out (>30s)") from exc

    def _run_copilot_analysis(self, text: str, task: str, **kwargs) -> Dict[str, Any]:
        if not self.check_tool_available(AIProvider.COPILOT):
            raise ToolUnavailableError("Copilot CLI not available")

        prompt = self._build_analysis_prompt(text, task, **kwargs)
        timeout = self.refine_description_timeout if task == "refine_description" else self.analysis_timeout

        try:
            result = subprocess.run(
                [self.copilot_cli_path, "-p", prompt],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode != 0:
                raise Exception(f"Copilot error: {result.stderr}")

            return self._parse_analysis_result(result.stdout, task)
        except subprocess.TimeoutExpired as exc:
            raise Exception(f"Copilot analysis timed out (>{timeout}s)") from exc

    def _build_analysis_prompt(self, text: str, task: str, **kwargs) -> str:
        if task == "classify":
            projects = kwargs.get("projects", [])
            types = kwargs.get("types", [])
            return f"""Classify this task:
Text: {text[:500]}

1. Map to project (one of: {", ".join(projects)}). Use key format.
2. Classify type (one of: {", ".join(types)}).
3. Generate concise task name (3-6 words, kebab-case).
4. Return JSON: {{"project": "key", "type": "type_key", "task_name": "name"}}

Return ONLY valid JSON."""

        if task == "route":
            return f"""Route this task to the best agent:
{text[:500]}

1. Identify primary work type (coding, design, testing, ops, content).
2. Suggest best agent.
3. Rate confidence 0-100.
4. Return JSON: {{"agent": "name", "type": "work_type", "confidence": 85}}

Return ONLY valid JSON."""

        if task == "generate_name":
            project = kwargs.get("project_name", "")
            return f"""Generate a concise task name (3-6 words, kebab-case):
{text[:300]}
Project: {project}

Return ONLY the name, no quotes."""

        if task == "refine_description":
            return f"""Rewrite this task description to be clear, concise, and structured.
Preserve all concrete requirements, constraints, and details. Do not invent facts.

Return in plain text (no Markdown headers), using short paragraphs and bullet points if helpful.

Input:
{text.strip()}
"""

        return text

    @staticmethod
    def _strip_cli_tool_output(text: str) -> str:
        """Remove Copilot/Gemini CLI tool-use artifacts from analysis output.

        CLI tools emit lines like::

            ● No-op
              $ true
              └ 1 line...

        These are tool-use progress indicators and must not leak into
        user-facing content.
        """
        lines = text.splitlines()
        cleaned: list[str] = []
        skip_until_blank = False
        for line in lines:
            stripped = line.lstrip()
            # Tool-use block header (● List directory, ● No-op, ● Read file, etc.)
            if stripped.startswith("●"):
                skip_until_blank = True
                continue
            # Tool-use command ($ true, $ cd ...)
            if skip_until_blank and stripped.startswith("$"):
                continue
            # Tool-use result (└ 1 line...)
            if skip_until_blank and stripped.startswith("└"):
                continue
            # Blank line ends a tool-use block
            if not stripped:
                skip_until_blank = False
                # Keep blank line only if we have preceding content
                if cleaned:
                    cleaned.append(line)
                continue
            skip_until_blank = False
            cleaned.append(line)
        # Strip leading/trailing blank lines
        result = "\n".join(cleaned).strip()
        return result

    def _parse_analysis_result(self, output: str, task: str) -> Dict[str, Any]:
        output = self._strip_cli_tool_output(output)

        def _parse_json_candidates(text: str) -> Optional[Dict[str, Any]]:
            candidates: list[str] = [text.strip()]

            fenced_blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", text, flags=re.IGNORECASE)
            candidates.extend(block.strip() for block in fenced_blocks if block.strip())

            first_brace = text.find("{")
            last_brace = text.rfind("}")
            if first_brace != -1 and last_brace > first_brace:
                candidates.append(text[first_brace:last_brace + 1].strip())

            seen: set[str] = set()
            for candidate in candidates:
                if not candidate or candidate in seen:
                    continue
                seen.add(candidate)
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    continue
            return None

        parsed_json = _parse_json_candidates(output)
        if parsed_json is not None:
            return parsed_json

        looks_like_json = "{" in output and "}" in output
        if looks_like_json or "```" in output:
            logger.warning("Failed to parse %s result as JSON: %s", task, output[:100])
            return {"text": output, "parse_error": True}

        return {"text": output}

    def _get_default_analysis_result(self, task: str, **kwargs) -> Dict[str, Any]:
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
        return {}


def register_plugins(registry) -> None:
    """Register built-in AI runtime orchestrator plugin."""
    from nexus.plugins import PluginKind

    registry.register_factory(
        kind=PluginKind.AI_PROVIDER,
        name="ai-runtime-orchestrator",
        version="0.1.0",
        factory=lambda config: AIOrchestrator(config),
        description="Copilot/Gemini orchestration with fallback and cooldown handling",
    )
