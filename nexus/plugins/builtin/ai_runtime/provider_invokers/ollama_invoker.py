import subprocess
import time
from typing import Any, Callable

from nexus.plugins.builtin.ai_runtime.provider_invokers.agent_invokers import (
    _prepare_log_path,
    _launch_process_with_log,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.subprocess_utils import (
    run_cli_prompt,
    wrap_timeout_error,
)


def invoke_ollama_agent_cli(
    *,
    check_tool_available: Callable[[Any], bool],
    ollama_provider: Any,
    ollama_cli_path: str,
    ollama_model: str,
    get_tasks_logs_dir: Callable[[str, str | None], str],
    tool_unavailable_error: type[Exception],
    rate_limited_error: type[Exception],
    logger: Any,
    agent_prompt: str,
    workspace_dir: str,
    agents_dir: str,
    issue_num: str | None = None,
    log_subdir: str | None = None,
    env: dict[str, str] | None = None,
) -> int | None:
    if not check_tool_available(ollama_provider):
        raise tool_unavailable_error("Ollama CLI not available")

    cmd = [ollama_cli_path, "run"]
    if ollama_model:
        cmd.append(ollama_model)
    else:
        cmd.append("llama3")  # default fallback if not specified
    cmd.append(agent_prompt)

    log_path = _prepare_log_path(
        prefix="ollama",
        workspace_dir=workspace_dir,
        issue_num=issue_num,
        log_subdir=log_subdir,
        get_tasks_logs_dir=get_tasks_logs_dir,
    )

    logger.info("🤖 Launching Ollama CLI agent (model: %s)", ollama_model or "default")
    logger.info("   Workspace: %s", workspace_dir)
    logger.info("   Log: %s", log_path)

    try:
        process = _launch_process_with_log(
            cmd=cmd,
            workspace_dir=workspace_dir,
            env=env,
            log_path=log_path,
            logger=logger,
            launched_message="🚀 Ollama launched (PID: %s)",
        )

        # Detect near-immediate startup failure so orchestrator can fallback
        exit_code = None
        deadline = time.time() + 5.0
        while time.time() < deadline:
            exit_code = process.poll()
            if exit_code is not None:
                break
            time.sleep(0.3)

        if exit_code is not None and exit_code != 0:
            raise tool_unavailable_error(f"Ollama exited immediately (exit={exit_code})")

        return process.pid
    except Exception as exc:
        logger.error("❌ Ollama launch failed: %s", exc)
        raise


def run_ollama_analysis_cli(
    *,
    check_tool_available: Callable[[Any], bool],
    ollama_provider: Any,
    ollama_cli_path: str,
    ollama_model: str,
    build_analysis_prompt: Callable[..., str],
    parse_analysis_result: Callable[[str, str], dict[str, Any]],
    tool_unavailable_error: type[Exception],
    rate_limited_error: type[Exception],
    text: str,
    task: str,
    timeout: int,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    if not check_tool_available(ollama_provider):
        raise tool_unavailable_error("Ollama CLI not available")

    prompt = build_analysis_prompt(text, task, **kwargs)
    try:
        cmd = [ollama_cli_path, "run"]
        if ollama_model:
            cmd.append(ollama_model)
        else:
            cmd.append("llama3")
        cmd.append(prompt)

        result = run_cli_prompt(cmd, timeout=timeout)
        if result.returncode != 0:
            stderr = result.stderr or ""
            raise Exception(f"Ollama error: {stderr}")

        return parse_analysis_result(result.stdout or "", task)
    except subprocess.TimeoutExpired as exc:
        raise wrap_timeout_error(exc, provider_name="Ollama", timeout=timeout) from exc


def run_ollama_transcription_cli(
    *,
    check_tool_available: Callable[[Any], bool],
    ollama_provider: Any,
    ollama_cli_path: str,
    ollama_model: str,
    strip_cli_tool_output: Callable[[str], str],
    is_non_transcription_artifact: Callable[[str, str], bool],
    tool_unavailable_error: type[Exception],
    logger: Any,
    audio_file_path: str,
    timeout: int,
) -> str | None:
    """Run transcription using Ollama (expects a whisper or multimodal model)."""
    if not check_tool_available(ollama_provider):
        raise tool_unavailable_error("Ollama CLI not available")

    # Best-effort prompt for multimodal/STT models in Ollama
    prompt = (
        "You are a speech-to-text (STT) transcriber. "
        "Transcribe only the spoken words from the provided audio file.\n"
        "Output rules:\n"
        "- Return ONLY the transcript text\n"
        "- Do NOT summarize, explain, or describe the file\n"
        f"Audio file path: {audio_file_path}"
    )

    logger.info(
        "🎧 Transcribing with Ollama (model: %s): %s", ollama_model or "default", audio_file_path
    )
    try:
        cmd = [ollama_cli_path, "run"]
        if ollama_model:
            cmd.append(ollama_model)
        else:
            cmd.append("llama3")  # Fallback
        cmd.append(prompt)

        result = run_cli_prompt(cmd, timeout=timeout)
        if result.returncode != 0:
            stderr = result.stderr or ""
            raise Exception(f"Ollama transcription error: {stderr}")

        text = strip_cli_tool_output(result.stdout or "").strip()
        if text:
            if is_non_transcription_artifact(text, audio_file_path):
                raise Exception("Ollama returned non-transcription content")
            return text
        raise Exception("Ollama returned empty transcription")
    except subprocess.TimeoutExpired as exc:
        raise wrap_timeout_error(exc, provider_name="Ollama", timeout=timeout) from exc
