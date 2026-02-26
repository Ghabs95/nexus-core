import os
import subprocess
import time
from typing import Any, Callable


def _prepare_log_path(
    *,
    prefix: str,
    workspace_dir: str,
    issue_num: str | None,
    log_subdir: str | None,
    get_tasks_logs_dir: Callable[[str, str | None], str],
) -> str:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_dir = get_tasks_logs_dir(workspace_dir, log_subdir)
    os.makedirs(log_dir, exist_ok=True)
    log_suffix = f"{issue_num}_{timestamp}" if issue_num else timestamp
    return os.path.join(log_dir, f"{prefix}_{log_suffix}.log")


def _launch_process_with_log(
    *,
    cmd: list[str],
    workspace_dir: str,
    env: dict[str, str] | None,
    log_path: str,
    logger: Any,
    launched_message: str,
) -> subprocess.Popen[Any]:
    log_file = None
    try:
        log_file = open(log_path, "w", encoding="utf-8")
        merged_env = {**os.environ}
        if env:
            merged_env.update(env)
        process = subprocess.Popen(
            cmd,
            cwd=workspace_dir,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            env=merged_env,
        )
        log_file.close()
        logger.info(launched_message, process.pid)
        return process
    except Exception:
        try:
            if log_file:
                log_file.close()
        except Exception:
            pass
        raise


def invoke_copilot_agent_cli(
    *,
    check_tool_available: Callable[[Any], bool],
    copilot_provider: Any,
    copilot_cli_path: str,
    get_tasks_logs_dir: Callable[[str, str | None], str],
    tool_unavailable_error: type[Exception],
    logger: Any,
    agent_prompt: str,
    workspace_dir: str,
    agents_dir: str,
    base_dir: str,
    issue_num: str | None = None,
    log_subdir: str | None = None,
    env: dict[str, str] | None = None,
) -> int | None:
    if not check_tool_available(copilot_provider):
        raise tool_unavailable_error("Copilot not available")

    cmd = [
        copilot_cli_path,
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
    log_path = _prepare_log_path(
        prefix="copilot",
        workspace_dir=workspace_dir,
        issue_num=issue_num,
        log_subdir=log_subdir,
        get_tasks_logs_dir=get_tasks_logs_dir,
    )

    logger.info("🤖 Launching Copilot CLI agent")
    logger.info("   Workspace: %s", workspace_dir)
    logger.info("   Log: %s", log_path)

    try:
        process = _launch_process_with_log(
            cmd=cmd,
            workspace_dir=workspace_dir,
            env=env,
            log_path=log_path,
            logger=logger,
            launched_message="🚀 Copilot launched (PID: %s)",
        )
        return process.pid
    except Exception as exc:
        logger.error("❌ Copilot launch failed: %s", exc)
        raise


def invoke_gemini_agent_cli(
    *,
    check_tool_available: Callable[[Any], bool],
    gemini_provider: Any,
    gemini_cli_path: str,
    gemini_model: str,
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
    if not check_tool_available(gemini_provider):
        raise tool_unavailable_error("Gemini CLI not available")

    cmd = [
        gemini_cli_path,
        "--prompt",
        agent_prompt,
        "--include-directories",
        agents_dir,
        "--yolo",
    ]
    if gemini_model:
        cmd.extend(["--model", gemini_model])

    log_path = _prepare_log_path(
        prefix="gemini",
        workspace_dir=workspace_dir,
        issue_num=issue_num,
        log_subdir=log_subdir,
        get_tasks_logs_dir=get_tasks_logs_dir,
    )

    logger.info("🤖 Launching Gemini CLI agent")
    logger.info("   Workspace: %s", workspace_dir)
    logger.info("   Log: %s", log_path)

    def _read_log_excerpt(max_chars: int = 2000) -> str:
        try:
            with open(log_path, encoding="utf-8", errors="replace") as handle:
                data = handle.read()
            if len(data) <= max_chars:
                return data
            return data[-max_chars:]
        except Exception:
            return ""

    try:
        process = _launch_process_with_log(
            cmd=cmd,
            workspace_dir=workspace_dir,
            env=env,
            log_path=log_path,
            logger=logger,
            launched_message="🚀 Gemini launched (PID: %s)",
        )

        # Detect immediate startup failure so orchestrator can fallback.
        time.sleep(1.5)
        exit_code = process.poll()
        if exit_code is not None:
            log_excerpt = _read_log_excerpt().lower()
            if (
                "ratelimitexceeded" in log_excerpt
                or "status 429" in log_excerpt
                or "no capacity available" in log_excerpt
            ):
                raise rate_limited_error(
                    f"Gemini exited immediately with rate limit (exit={exit_code})"
                )
            raise tool_unavailable_error(f"Gemini exited immediately (exit={exit_code})")
        return process.pid
    except Exception as exc:
        logger.error("❌ Gemini launch failed: %s", exc)
        raise
