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
    copilot_model: str,
    copilot_supports_model: bool,
    get_tasks_logs_dir: Callable[[str, str | None], str],
    tool_unavailable_error: type[Exception],
    rate_limited_error: type[Exception],
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
    if copilot_model and copilot_supports_model:
        cmd.extend(["--model", copilot_model])
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

        def _read_log_excerpt(max_chars: int = 2000) -> str:
            try:
                with open(log_path, encoding="utf-8", errors="replace") as handle:
                    data = handle.read()
                if len(data) <= max_chars:
                    return data
                return data[-max_chars:]
            except Exception:
                return ""

        # Detect near-immediate startup failure so orchestrator can fallback.
        # Copilot quota failures often exit after a few seconds, not instantly.
        exit_code = None
        deadline = time.time() + 5.0
        while time.time() < deadline:
            exit_code = process.poll()
            if exit_code is not None:
                break
            time.sleep(0.5)
        if exit_code is not None:
            excerpt = _read_log_excerpt().lower()
            if (
                "402" in excerpt
                or "quota" in excerpt
                or "rate limit" in excerpt
                or "ratelimit" in excerpt
                or "429" in excerpt
                or "too many requests" in excerpt
            ):
                raise rate_limited_error(
                    f"Copilot exited immediately with rate limit/quota (exit={exit_code})"
                )
            raise tool_unavailable_error(f"Copilot exited immediately (exit={exit_code})")
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

    def _looks_rate_limited(log_excerpt: str) -> bool:
        text = str(log_excerpt or "").lower()
        if not text:
            return False
        hard_markers = (
            "ratelimitexceeded",
            "status 429",
            "status: 429",
            "too many requests",
            "statustext: 'too many requests'",
            "no capacity available",
            "exhausted your capacity",
            "quota will reset",
        )
        return any(marker in text for marker in hard_markers)

    def _gemini_retry_loop_count(log_excerpt: str) -> int:
        text = str(log_excerpt or "").lower()
        if not text:
            return 0
        markers = (
            "attempt 1 failed:",
            "retryablequotaerror",
            "quota will reset",
        )
        return sum(text.count(marker) for marker in markers)

    try:
        process = _launch_process_with_log(
            cmd=cmd,
            workspace_dir=workspace_dir,
            env=env,
            log_path=log_path,
            logger=logger,
            launched_message="🚀 Gemini launched (PID: %s)",
        )

        # Startup probe: detect both immediate exits and live quota/rate-limit loops.
        # Gemini can stay alive while repeatedly logging retries when quota is exhausted.
        exit_code = None
        # Keep startup probe short; if Gemini survives initial checks we let
        # post-launch watchdogs monitor quota loops and trigger fallback quickly.
        deadline = time.time() + 5.0
        retry_loop_cap = 3
        while time.time() < deadline:
            log_excerpt = _read_log_excerpt().lower()
            retry_loop_count = _gemini_retry_loop_count(log_excerpt)
            if retry_loop_count >= retry_loop_cap:
                try:
                    process.kill()
                except Exception:
                    pass
                raise rate_limited_error(
                    f"Gemini quota rate limit retry loop detected at startup (markers={retry_loop_count}, cap={retry_loop_cap})"
                )
            if _looks_rate_limited(log_excerpt):
                try:
                    process.kill()
                except Exception:
                    pass
                raise rate_limited_error("Gemini quota/rate limit detected at startup")

            exit_code = process.poll()
            if exit_code is not None:
                break
            time.sleep(0.3)

        if exit_code is not None:
            log_excerpt = _read_log_excerpt().lower()
            if _looks_rate_limited(log_excerpt):
                raise rate_limited_error(
                    f"Gemini exited immediately with rate limit (exit={exit_code})"
                )
            raise tool_unavailable_error(f"Gemini exited immediately (exit={exit_code})")
        return process.pid
    except Exception as exc:
        logger.error("❌ Gemini launch failed: %s", exc)
        raise
