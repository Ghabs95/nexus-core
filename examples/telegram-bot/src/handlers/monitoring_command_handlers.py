"""Monitoring/log command handlers extracted from telegram_bot."""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from nexus.adapters.notifications.base import Button

from interactive_context import InteractiveContext
from utils.log_utils import log_unauthorized_access


@dataclass
class MonitoringHandlersDeps:
    logger: Any
    allowed_user_ids: list[int]
    base_dir: str
    project_config: dict[str, dict[str, Any]]
    types_map: dict[str, str]
    # Removed prompt_monitor_project_selection and prompt_project_selection
    ensure_project: Callable[[InteractiveContext, str], Awaitable[str | None]]
    ensure_project_issue: Callable[
        [InteractiveContext, str], Awaitable[tuple[str | None, str | None, list[str]]]
    ]
    normalize_project_key: Callable[[str], str | None]
    iter_project_keys: Callable[[], list[str]]
    get_project_label: Callable[[str], str]
    get_project_root: Callable[[str], str | None]
    get_project_logs_dir: Callable[[str], str | None]
    project_repo: Callable[[str], str]
    get_issue_details: Callable[[str, str | None], dict[str, Any] | None]
    get_inbox_dir: Callable[[str, str], str]
    get_tasks_active_dir: Callable[[str, str], str]
    get_tasks_closed_dir: Callable[[str, str], str]
    extract_issue_number_from_file: Callable[[str], str | None]
    build_issue_url: Callable[[str, str, dict[str, Any] | None], str]
    find_task_file_by_issue: Callable[[str], str | None]
    find_issue_log_files: Callable[..., list[str]]
    read_latest_log_tail: Callable[..., list[str]]
    search_logs_for_issue: Callable[[str], list[str]]
    read_latest_log_full: Callable[[str | None], list[str]]
    read_log_matches: Callable[..., list[str]]
    active_tail_sessions: dict[tuple[int, int], str]
    active_tail_tasks: dict[tuple[int, int], asyncio.Task]
    get_retry_fuse_status: Callable[[str], dict[str, Any]]
    normalize_agent_reference: Callable[[str | None], str | None]
    get_expected_running_agent_from_workflow: Callable[[str], str | None]
    get_direct_issue_plugin: Callable[[str], Any]
    extract_structured_completion_signals: Callable[[list[dict]], list[dict[str, str]]]
    read_latest_local_completion: Callable[[str], dict[str, Any] | None]
    build_workflow_snapshot: Callable[..., dict[str, Any]]

async def status_handler(ctx: InteractiveContext, deps: MonitoringHandlersDeps) -> None:
    deps.logger.info(f"Status triggered by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    project_filter = None
    issue_num: str | None = None
    if ctx.args:
        raw = ctx.args[0].strip().lower()
        if raw != "all":
            project_filter = deps.normalize_project_key(raw)
            if project_filter not in deps.iter_project_keys():
                await ctx.reply_text(f"❌ Unknown project '{raw}'.")
                return
        if len(ctx.args) > 1:
            candidate = str(ctx.args[1]).strip()
            if candidate.isdigit():
                issue_num = candidate
    else:
        # Replaced prompt_monitor_project_selection
        buttons = [[Button(label=deps.get_project_label(pk), callback_data=f"pickmonitor:status:{pk}")] for pk in deps.iter_project_keys()]
        buttons.append([Button(label="All Projects", callback_data="pickmonitor:status:all")])
        await ctx.reply_text("Please select a project to view its status:", buttons=buttons)
        return

    selected_projects = [project_filter] if project_filter else deps.iter_project_keys()

    status_text = "📥 Inbox Status (Pending Tasks)\n\n"
    total_tasks = 0

    for project_key in selected_projects:
        project_name = deps.get_project_label(project_key)
        project_root = deps.get_project_root(project_key)
        if not project_root:
            continue
        inbox_dir = deps.get_inbox_dir(project_root, project_key)
        if os.path.exists(inbox_dir):
            files = [f for f in os.listdir(inbox_dir) if f.endswith(".md")]
            if files:
                repo = deps.project_repo(project_key)
                project_issue_cfg = deps.project_config.get(project_key)
                status_text += f"{project_name}: {len(files)} task(s)\n"
                total_tasks += len(files)
                for filename in files[:3]:
                    task_type = filename.split("_")[0]
                    emoji = deps.types_map.get(task_type, "📝")
                    file_path = os.path.join(inbox_dir, filename)
                    issue_number = deps.extract_issue_number_from_file(file_path)
                    if issue_number:
                        issue_link = deps.build_issue_url(
                            repo,
                            issue_number,
                            project_issue_cfg if isinstance(project_issue_cfg, dict) else None,
                        )
                        issue_suffix = f" [#{issue_number}]({issue_link})"
                    else:
                        issue_suffix = " (issue ?)"
                    status_text += f"  • {emoji} `{filename}`{issue_suffix}\n"
                if len(files) > 3:
                    status_text += f"  ... +{len(files) - 3} more\n"
                status_text += "\n"

    if total_tasks == 0:
        status_text += "✨ No pending tasks in inbox!\n"
    else:
        status_text += f"Total: {total_tasks} pending task(s)"

    with_issue_snapshot = bool(issue_num and project_filter)

    if with_issue_snapshot:
        repo = deps.project_repo(project_filter)
        expected_running = deps.normalize_agent_reference(
            deps.get_expected_running_agent_from_workflow(issue_num) or ""
        )
        snapshot = deps.build_workflow_snapshot(
            issue_num=issue_num,
            repo=repo,
            get_issue_plugin=deps.get_direct_issue_plugin,
            expected_running_agent=expected_running,
            find_task_file_by_issue=deps.find_task_file_by_issue,
            read_latest_local_completion=deps.read_latest_local_completion,
            extract_structured_completion_signals=deps.extract_structured_completion_signals,
        )

        processor_signal = snapshot.get("processor_signal") or {}
        processor_type = processor_signal.get("type", "n/a")
        processor_severity = processor_signal.get("severity", "n/a")
        processor_at = processor_signal.get("timestamp", "n/a")
        processor_line = processor_signal.get("line", "n/a")

        recovery_hint = "none"
        if processor_type == "completion_mismatch":
            recovery_hint = "stale completion signal. Run /reconcile then /continue"
        elif processor_type in {"signal_drift", "retry_fuse", "pause_failed"}:
            recovery_hint = "workflow drift. Run /wfstate, then /reconcile and /continue"

        status_text += (
            "\n\n"
            f"📊 Workflow Snapshot — Issue #{issue_num}\n"
            f"Workflow State: {snapshot['workflow_state']}\n"
            f"Current Step: {snapshot['current_step']} ({snapshot['current_step_name']})\n"
            f"Current Agent: {snapshot['current_agent']}\n"
            f"Expected RUNNING Agent: {snapshot['expected_running_agent'] or expected_running or 'n/a'}\n"
            f"Process: {'running' if snapshot['running'] else 'stopped'} (PID: {snapshot['pid'] or 'n/a'})\n"
            f"Local Completion: from={snapshot['local_from'] or 'n/a'}, next={snapshot['local_next'] or 'n/a'}\n"
            f"Latest Comment: from={snapshot['comment_from'] or 'n/a'}, next={snapshot['comment_next'] or 'n/a'}\n"
            f"Drift Flags: {', '.join(snapshot['drift_flags']) if snapshot['drift_flags'] else 'none'}\n"
            "Latest Processor Signal:\n"
            f"- type: {processor_type}\n"
            f"- severity: {processor_severity}\n"
            f"- at: {processor_at}\n"
            f"- detail: {processor_line}\n"
            f"Recovery Hint: {recovery_hint}"
        )

        if snapshot.get("workflow_pointer_mismatch"):
            status_text += (
                "\n"
                "Workflow Pointer Mismatch:\n"
                f"- indexed: {snapshot['indexed_step']} ({snapshot['indexed_step_name']}) / {snapshot['indexed_agent']}\n"
                f"- running: {snapshot['running_step']} ({snapshot['running_step_name']}) / {snapshot['running_agent']}"
            )

    await ctx.reply_text(
        status_text,
        parse_mode=None if with_issue_snapshot else "Markdown",
        disable_web_page_preview=True,
    )


async def active_handler(ctx: InteractiveContext, deps: MonitoringHandlersDeps) -> None:
    deps.logger.info(f"Active triggered by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    cleanup_mode = any(arg.lower() in {"cleanup", "--cleanup"} for arg in (ctx.args or []))
    project_tokens = [arg for arg in (ctx.args or []) if arg.lower() not in {"cleanup", "--cleanup"}]
    project_filter = None
    if project_tokens:
        raw = project_tokens[0].strip().lower()
        if raw != "all":
            project_filter = deps.normalize_project_key(raw)
            if project_filter not in deps.iter_project_keys():
                await ctx.reply_text(f"❌ Unknown project '{raw}'.")
                return
    elif not cleanup_mode:
        # Replaced prompt_monitor_project_selection
        buttons = [[Button(label=deps.get_project_label(pk), callback_data=f"pickmonitor:active:{pk}")] for pk in deps.iter_project_keys()]
        buttons.append([Button(label="All Projects", callback_data="pickmonitor:active:all")])
        if cleanup_mode:
            buttons.append([Button(label="All Projects (Cleanup)", callback_data="pickmonitor:active:all:cleanup")])
        await ctx.reply_text("Please select a project to view its active tasks:", buttons=buttons)
        return

    selected_projects = [project_filter] if project_filter else deps.iter_project_keys()

    active_text = "🚀 Active Tasks (In Progress)\n\n"
    if cleanup_mode:
        active_text += "🧹 Cleanup mode: archiving closed tasks to `tasks/closed`\n\n"
    total_active = 0
    total_skipped_closed = 0
    total_archived = 0

    issue_state_cache: dict[str, str] = {}

    for project_key in selected_projects:
        display_name = deps.get_project_label(project_key)
        project_root = deps.get_project_root(project_key)
        if not project_root:
            continue
        active_dir = deps.get_tasks_active_dir(project_root, project_key)
        if os.path.exists(active_dir):
            files = [f for f in os.listdir(active_dir) if f.endswith(".md")]
            if files:
                repo = deps.project_repo(project_key)
                project_issue_cfg = deps.project_config.get(project_key)
                open_files: list[tuple[str, str | None]] = []
                stale_count = 0

                for filename in files:
                    file_path = os.path.join(active_dir, filename)
                    issue_number = deps.extract_issue_number_from_file(file_path)
                    if not issue_number:
                        filename_match = re.search(r"_(\d+)\.md$", filename)
                        issue_number = filename_match.group(1) if filename_match else None

                    # Resolve issue state
                    issue_state = "unknown"
                    if issue_number:
                        cache_key = f"{repo}:{issue_number}"
                        if cache_key not in issue_state_cache:
                            details = deps.get_issue_details(issue_number, repo=repo)
                            if not details:
                                issue_state_cache[cache_key] = "orphan"
                            else:
                                issue_state_cache[cache_key] = details.get("state", "unknown").lower()
                        issue_state = issue_state_cache[cache_key]
                    else:
                        issue_state = "orphan"

                    # If it's a valid open task (or state unknown), keep it in the active list
                    if issue_state in {"open", "unknown"}:
                        open_files.append((filename, issue_number))
                        continue

                    # Otherwise it's archivable (closed or orphan)
                    stale_count += 1
                    if cleanup_mode:
                        try:
                            closed_dir = deps.get_tasks_closed_dir(project_root, project_key)
                            os.makedirs(closed_dir, exist_ok=True)
                            target_path = os.path.join(closed_dir, filename)
                            if os.path.exists(target_path):
                                base, ext = os.path.splitext(filename)
                                target_path = os.path.join(closed_dir, f"{base}_{int(time.time())}{ext}")
                            os.replace(file_path, target_path)
                            total_archived += 1
                            deps.logger.info(
                                f"Archived {issue_state} task file: {file_path} -> {target_path}"
                            )
                        except Exception as exc:
                            deps.logger.warning(f"Failed to archive {file_path}: {exc}")

                if not open_files:
                    total_skipped_closed += stale_count
                    continue

                active_text += f"{display_name}: {len(open_files)} task(s)\n"
                total_active += len(open_files)
                total_skipped_closed += stale_count

                for filename, issue_number in open_files[:3]:
                    task_type = filename.split("_")[0]
                    emoji = deps.types_map.get(task_type, "📝")
                    if issue_number:
                        issue_link = deps.build_issue_url(
                            repo,
                            issue_number,
                            project_issue_cfg if isinstance(project_issue_cfg, dict) else None,
                        )
                        issue_suffix = f" [#{issue_number}]({issue_link})"
                    else:
                        issue_suffix = " _(orphan — no issue reference)_"
                    active_text += f"  • {emoji} `{filename}`{issue_suffix}\n"
                if len(open_files) > 3:
                    active_text += f"  ... +{len(open_files) - 3} more\n"
                active_text += "\n"

    if total_active == 0:
        active_text += "💤 No active tasks at the moment.\n"
    else:
        active_text += f"Total: {total_active} active task(s)"

    if total_skipped_closed:
        active_text += f"\n\nℹ️ Skipped {total_skipped_closed} closed or orphan task file(s)."
    if cleanup_mode:
        active_text += f"\n📦 Archived {total_archived} closed task file(s) to `tasks/closed`."

    await ctx.reply_text(
        active_text,
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


async def logs_handler(ctx: InteractiveContext, deps: MonitoringHandlersDeps) -> None:
    deps.logger.info(f"Logs requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        # Replaced prompt_project_selection
        buttons = [[Button(label=deps.get_project_label(pk), callback_data=f"pickmonitor:logs:{pk}")] for pk in deps.iter_project_keys()]
        await ctx.reply_text("Please select a project to view logs:", buttons=buttons)
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "logs")
    if not project_key:
        return

    msg_id = await ctx.reply_text(f"📋 Fetching logs for issue #{issue_num}...")

    repo = deps.project_repo(project_key)
    details = deps.get_issue_details(issue_num, repo=repo)
    timeline = "Task Logs:\n"

    task_file = None
    if details and details.get("body"):
        match = re.search(r"Task File:\s*`([^`]+)`", details.get("body", ""))
        if match:
            task_file = match.group(1)
    if not task_file:
        task_file = deps.find_task_file_by_issue(issue_num)

    issue_logs = deps.find_issue_log_files(issue_num, task_file=task_file)
    if issue_logs:
        issue_logs.sort(key=lambda path: os.path.getmtime(path))
        timeline += "\n"
        for log_file in issue_logs:
            size = os.path.getsize(log_file)
            mtime = time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(log_file)))
            timeline += f"• `{os.path.basename(log_file)}` ({size}B, {mtime})\n"

        non_empty = [lf for lf in reversed(issue_logs) if os.path.getsize(lf) > 0]
        latest = non_empty[0] if non_empty else issue_logs[-1]
        deps.logger.info(f"Reading log file: {latest}")
        try:
            with open(latest, encoding="utf-8") as handle:
                lines = handle.readlines()[-200:]
            deps.logger.info(f"Read {len(lines)} lines from log file")
            timeline += f"\n**{os.path.basename(latest)}** (last 200 lines):\n"
            for line in lines:
                timeline += f"{line.rstrip()}\n"
        except Exception as exc:
            deps.logger.error(f"Error reading log file: {exc}", exc_info=True)
            timeline += f"\n❌ Failed to read {os.path.basename(latest)}: {exc}\n"
    else:
        latest_tail = deps.read_latest_log_tail(task_file, max_lines=200)
        if not latest_tail:
            issue_refs = deps.search_logs_for_issue(issue_num)
            if issue_refs:
                timeline += "\nReferences in service logs:\n"
                for line in issue_refs[-30:]:
                    timeline += f"{line}\n"
            else:
                timeline += "\n- No task logs found for this issue.\n"
            latest_tail = []

        if latest_tail:
            timeline += "\nLatest Task Logs:\n"
            for log_line in latest_tail:
                timeline += f"{log_line}\n"
        else:
            timeline += "\n- No task logs found.\n"

    max_len = 3500
    if len(timeline) <= max_len:
        await ctx.edit_message_text(
            message_id=msg_id,
            text=timeline,
            parse_mode=None,
        )
    else:
        chunks = [timeline[i : i + max_len] for i in range(0, len(timeline), max_len)]
        for idx, chunk in enumerate(chunks):
            if idx == 0:
                await ctx.edit_message_text(
                    message_id=msg_id,
                    text=chunk,
                    parse_mode=None,
                )
            else:
                await ctx.reply_text(text=chunk, parse_mode=None)


async def logsfull_handler(
    ctx: InteractiveContext,
    deps: MonitoringHandlersDeps,
) -> None:
    deps.logger.info(f"Logsfull requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        # Replaced prompt_project_selection
        buttons = [[Button(label=deps.get_project_label(pk), callback_data=f"pickmonitor:logsfull:{pk}")] for pk in deps.iter_project_keys()]
        await ctx.reply_text("Please select a project to view full logs:", buttons=buttons)
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "logsfull")
    if not project_key:
        return

    msg_id = await ctx.reply_text(f"📋 Fetching full logs for issue #{issue_num}...")
    repo = deps.project_repo(project_key)
    issue_cfg = deps.project_config.get(project_key)
    issue_url = deps.build_issue_url(repo, issue_num, issue_cfg if isinstance(issue_cfg, dict) else None)

    details = deps.get_issue_details(issue_num, repo=repo)
    timeline = "Git Platform Activity:\n"
    if details:
        timeline += f"- Title: {details.get('title', 'N/A')}\n"
        timeline += f"- State: {details.get('state', 'open')}\n"
        timeline += f"- Last Updated: {details.get('updatedAt', 'N/A')}\n"
        if details.get("labels"):
            timeline += f"- Labels: {', '.join([l['name'] for l in details.get('labels', [])])}\n"
    else:
        timeline += "- Could not fetch issue details\n"

    system_logs = deps.search_logs_for_issue(issue_num)
    if system_logs:
        timeline += "\nBot/Processor Logs:\n"
        for log_line in system_logs:
            timeline += f"- {log_line}\n"

    task_file = None
    if details and details.get("body"):
        match = re.search(r"Task File:\s*`([^`]+)`", details.get("body", ""))
        if match:
            task_file = match.group(1)

    latest_full = deps.read_latest_log_full(task_file)
    if latest_full:
        timeline += "\nLatest Task Log (full):\n"
        for log_line in latest_full:
            timeline += f"- {log_line}\n"

    processor_log = os.path.join(deps.base_dir, "ghabs", "nexus", "inbox_processor.log")
    processor_matches = deps.read_log_matches(processor_log, issue_num, issue_url, max_lines=20)
    if processor_matches:
        timeline += "\nProcessor Log:\n"
        for log_line in processor_matches:
            timeline += f"- {log_line}\n"

    max_len = 3500
    if len(timeline) <= max_len:
        await ctx.edit_message_text(
            message_id=msg_id,
            text=timeline,
            parse_mode=None,
        )
        return

    chunks = [timeline[i : i + max_len] for i in range(0, len(timeline), max_len)]
    await ctx.edit_message_text(
        message_id=msg_id,
        text=chunks[0],
        parse_mode=None,
    )
    for part in chunks[1:]:
        await ctx.reply_text(text=part, parse_mode=None)


async def tail_handler(ctx: InteractiveContext, deps: MonitoringHandlersDeps) -> None:
    deps.logger.info(f"Tail requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        # Replaced prompt_project_selection
        buttons = [[Button(label=deps.get_project_label(pk), callback_data=f"pickmonitor:tail:{pk}")] for pk in deps.iter_project_keys()]
        await ctx.reply_text("Please select a project to tail logs:", buttons=buttons)
        return

    project_key, issue_num, rest = await deps.ensure_project_issue(ctx, "tail")
    if not project_key:
        return

    max_lines = 50
    follow_seconds = 30
    refresh_seconds = 3
    if rest:
        try:
            max_lines = max(5, min(200, int(rest[0])))
        except ValueError:
            await ctx.reply_text("⚠️ Line count must be a number.")
            return
    if len(rest) > 1:
        try:
            follow_seconds = max(5, min(300, int(rest[1])))
        except ValueError:
            await ctx.reply_text(
                "⚠️ Follow duration must be a number of seconds."
            )
            return

    repo = deps.project_repo(project_key)
    details = deps.get_issue_details(issue_num, repo=repo)

    task_file = None
    if details and details.get("body"):
        match = re.search(r"Task File:\s*`([^`]+)`", details.get("body", ""))
        if match:
            task_file = match.group(1)
    if not task_file:
        task_file = deps.find_task_file_by_issue(issue_num)

    def _read_tail_lines() -> list[str]:
        lines_local = deps.read_latest_log_tail(task_file, max_lines=max_lines)
        if lines_local:
            return lines_local

        logs_dir = deps.get_project_logs_dir(project_key)
        if logs_dir:
            log_files = [
                os.path.join(logs_dir, filename)
                for filename in os.listdir(logs_dir)
                if filename.endswith(".log")
            ]
            if log_files:
                log_files.sort(key=os.path.getmtime, reverse=True)
                latest = log_files[0]
                try:
                    with open(latest, encoding="utf-8") as handle:
                        tail_lines = handle.readlines()[-max_lines:]
                    return [f"[{os.path.basename(latest)}] {line.rstrip()}" for line in tail_lines]
                except Exception as exc:
                    deps.logger.error(f"Error reading log file: {exc}", exc_info=True)
        return []

    chat_id = ctx.chat_id
    user_id = int(ctx.user_id)
    session_key = (chat_id, user_id)

    existing_task = deps.active_tail_tasks.get(session_key)
    if existing_task and not existing_task.done():
        existing_task.cancel()

    session_token = f"{issue_num}:{time.time()}"
    deps.active_tail_sessions[session_key] = session_token

    msg_id = await ctx.reply_text(
        f"📋 Following log tail for #{issue_num} ({max_lines} lines, {follow_seconds}s)...\n"
        "Use /tailstop to stop."
    )

    async def _run_tail_follow() -> None:
        deadline = time.time() + follow_seconds
        previous_text = ""
        max_len = 3500

        try:
            while True:
                if deps.active_tail_sessions.get(session_key) != session_token:
                    break

                lines = _read_tail_lines()
                if lines:
                    text = f"📋 Live log tail (#{issue_num}, {max_lines} lines):\n" + "\n".join(lines)
                else:
                    text = (
                        f"📋 Live log tail (#{issue_num}, {max_lines} lines):\n"
                        "⚠️ No task logs found yet. Waiting for new output..."
                    )

                if len(text) > max_len:
                    text = text[: max_len - 64] + "\n\n…(truncated, rerun with fewer lines)"

                if text != previous_text:
                    try:
                        await ctx.edit_message_text(
                            message_id=msg_id,
                            text=text,
                            parse_mode=None,
                        )
                        previous_text = text
                    except Exception as exc:
                        deps.logger.warning(f"Failed to update tail message for issue #{issue_num}: {exc}")

                if time.time() >= deadline:
                    break
                await asyncio.sleep(refresh_seconds)
        except asyncio.CancelledError:
            raise
        finally:
            if deps.active_tail_sessions.get(session_key) == session_token:
                deps.active_tail_sessions.pop(session_key, None)
            existing = deps.active_tail_tasks.get(session_key)
            if existing is asyncio.current_task():
                deps.active_tail_tasks.pop(session_key, None)

    deps.active_tail_tasks[session_key] = asyncio.create_task(_run_tail_follow())


async def tailstop_handler(
    ctx: InteractiveContext,
    deps: MonitoringHandlersDeps,
) -> None:
    deps.logger.info(f"Tailstop requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    session_key = (ctx.chat_id, int(ctx.user_id))
    active_task = deps.active_tail_tasks.get(session_key)
    if session_key in deps.active_tail_sessions or (active_task and not active_task.done()):
        deps.active_tail_sessions.pop(session_key, None)
        if active_task and not active_task.done():
            active_task.cancel()
        deps.active_tail_tasks.pop(session_key, None)
        await ctx.reply_text("⏹️ Stopped live tail session.")
    else:
        await ctx.reply_text("ℹ️ No active live tail session to stop.")


async def fuse_handler(ctx: InteractiveContext, deps: MonitoringHandlersDeps) -> None:
    deps.logger.info(f"Fuse status requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        # Replaced prompt_project_selection
        buttons = [[Button(label=deps.get_project_label(pk), callback_data=f"pickmonitor:fuse:{pk}")] for pk in deps.iter_project_keys()]
        await ctx.reply_text("Please select a project to view fuse status:", buttons=buttons)
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "fuse")
    if not project_key:
        return

    status = deps.get_retry_fuse_status(issue_num)
    if not status.get("exists"):
        await ctx.reply_text(
            f"🧯 Retry fuse: no state recorded for issue #{issue_num}."
        )
        return

    attempts = int(status.get("attempts") or 0)
    max_attempts = int(status.get("max_attempts") or 0)
    remaining = max(0, max_attempts - attempts)

    window_remaining = status.get("window_remaining_seconds")
    if isinstance(window_remaining, (int, float)):
        window_remaining_text = f"{int(window_remaining)}s"
    else:
        window_remaining_text = "n/a"

    trip_count = int(status.get("trip_count_in_hard_window") or 0)
    trip_threshold = int(status.get("hard_trip_threshold") or 0)
    hard_window_seconds = int(status.get("hard_window_seconds") or 0)
    hard_window_minutes = hard_window_seconds // 60

    state_flags = []
    if status.get("hard_tripped"):
        state_flags.append("HARD-TRIPPED")
    elif status.get("tripped"):
        state_flags.append("TRIPPED")
    else:
        state_flags.append("ACTIVE")
    if status.get("alerted"):
        state_flags.append("ALERTED")

    agent = status.get("agent") or "unknown"
    text = (
        f"🧯 **Retry Fuse Status**\n\n"
        f"Project: {project_key}\n"
        f"Issue: #{issue_num}\n"
        f"Agent: {agent}\n"
        f"State: {', '.join(state_flags)}\n"
        f"Attempts in window: {attempts}/{max_attempts}\n"
        f"Remaining before trip: {remaining}\n"
        f"Window remaining: {window_remaining_text}\n"
        f"Fuse trips in last {hard_window_minutes}m: {trip_count}/{trip_threshold}"
    )
    await ctx.reply_text(text)
