import os
import subprocess
import time
from typing import Any, Callable


def _cleanup_empty_rollout_files(*, logger: Any, codex_home: str | None = None) -> int:
    """Remove zero-byte Codex rollout session files that can crash reconcile startup."""
    home = str(codex_home or os.getenv("CODEX_HOME") or os.path.expanduser("~/.codex")).strip()
    if not home:
        return 0

    sessions_dir = os.path.join(home, "sessions")
    if not os.path.isdir(sessions_dir):
        return 0

    removed = 0
    now = time.time()
    for root, _dirs, files in os.walk(sessions_dir):
        for name in files:
            if not (name.startswith("rollout-") and name.endswith(".jsonl")):
                continue
            path = os.path.join(root, name)
            try:
                if os.path.getsize(path) != 0:
                    continue
                # Avoid racing with files currently being created by a live process.
                if (now - os.path.getmtime(path)) < 120:
                    continue
                os.remove(path)
                removed += 1
            except Exception:
                # Best-effort hygiene only.
                continue

    if removed:
        logger.warning(
            "Removed %s empty Codex rollout session file(s) from %s",
            removed,
            sessions_dir,
        )
    return removed


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

    _cleanup_empty_rollout_files(logger=logger)

    # Force writable workspace + network permission so Codex can post issue comments
    # and write completion summaries during workflow handoff.
    cmd = [
        codex_cli_path,
        "exec",
        "--sandbox",
        "workspace-write",
        "-c",
        'sandbox_permissions=["network-access"]',
    ]
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
        # Ensure inherited host sandbox flags don't force-disable network for
        # Codex child commands (e.g., gh issue comment/view).
        merged_env.pop("CODEX_SANDBOX_NETWORK_DISABLED", None)
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
