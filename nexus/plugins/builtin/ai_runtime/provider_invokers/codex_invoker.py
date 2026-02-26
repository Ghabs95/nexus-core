import os
import subprocess
import time
from typing import Any, Callable


def invoke_codex_cli(
    *,
    check_tool_available: Callable[[Any], bool],
    codex_provider: Any,
    codex_cli_path: str,
    codex_model: str,
    get_tasks_logs_dir: Callable[[str, str | None], str],
    tool_unavailable_error: type[Exception],
    logger: Any,
    agent_prompt: str,
    workspace_dir: str,
    issue_num: str | None = None,
    log_subdir: str | None = None,
    env: dict[str, str] | None = None,
) -> int | None:
    if not check_tool_available(codex_provider):
        raise tool_unavailable_error("Codex CLI not available")

    cmd = [codex_cli_path, "exec"]
    if codex_model:
        cmd.extend(["--model", codex_model])
    cmd.append(agent_prompt)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_dir = get_tasks_logs_dir(workspace_dir, log_subdir)
    os.makedirs(log_dir, exist_ok=True)
    log_suffix = f"{issue_num}_{timestamp}" if issue_num else timestamp
    log_path = os.path.join(log_dir, f"codex_{log_suffix}.log")

    logger.info("🤖 Launching Codex CLI agent")
    logger.info("   Workspace: %s", workspace_dir)
    logger.info("   Log: %s", log_path)

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
        logger.info("🚀 Codex launched (PID: %s)", process.pid)
        return process.pid
    except Exception as exc:
        try:
            if log_file:
                log_file.close()
        except Exception:
            pass
        logger.error("❌ Codex launch failed: %s", exc)
        raise

