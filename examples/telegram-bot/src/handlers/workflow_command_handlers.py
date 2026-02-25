"""Workflow command handlers extracted from telegram_bot."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from config import NEXUS_CORE_STORAGE_DIR
from interactive_context import InteractiveContext
from runtime.agent_launcher import clear_launch_guard
from integrations.workflow_state_factory import get_workflow_state
from state_manager import HostStateManager
from utils.log_utils import log_unauthorized_access


@dataclass
class WorkflowHandlerDeps:
    logger: Any
    allowed_user_ids: list[int]
    base_dir: str
    default_repo: str
    project_config: dict[str, dict[str, Any]]
    workflow_state_plugin_kwargs: dict[str, Any]
    prompt_project_selection: Callable[[InteractiveContext, str], Awaitable[None]]
    ensure_project_issue: Callable[
        [InteractiveContext, str], Awaitable[tuple[str | None, str | None, list[str]]]
    ]
    find_task_file_by_issue: Callable[[str], str | None]
    project_repo: Callable[[str], str]
    get_issue_details: Callable[[str, str | None], dict[str, Any] | None]
    resolve_project_config_from_task: Callable[[str], tuple[str | None, dict[str, Any] | None]]
    invoke_copilot_agent: Callable[..., tuple[int | None, str | None]]
    get_sop_tier_from_issue: Callable[[str, str | None], str | None]
    get_sop_tier: Callable[[str], tuple[str, Any, Any]]
    get_last_tier_for_issue: Callable[[str], str | None]
    prepare_continue_context: Callable[..., dict[str, Any]]
    kill_issue_agent: Callable[..., dict[str, Any]]
    get_runtime_ops_plugin: Callable[..., Any]
    get_workflow_state_plugin: Callable[..., Any]
    scan_for_completions: Callable[[str], list[Any]]
    normalize_agent_reference: Callable[[str | None], str | None]
    get_expected_running_agent_from_workflow: Callable[[str], str | None]
    reconcile_issue_from_signals: Callable[..., Awaitable[dict[str, Any]]]
    get_direct_issue_plugin: Callable[[str], Any]
    extract_structured_completion_signals: Callable[[list[dict]], list[dict[str, str]]]
    write_local_completion_from_signal: Callable[[str, str, dict[str, str]], str]
    build_workflow_snapshot: Callable[..., dict[str, Any]]
    read_latest_local_completion: Callable[[str], dict[str, Any] | None]
    workflow_pause_handler: Callable[[InteractiveContext], Awaitable[None]]
    workflow_resume_handler: Callable[[InteractiveContext], Awaitable[None]]
    workflow_stop_handler: Callable[[InteractiveContext], Awaitable[None]]


async def reprocess_handler(
    ctx: InteractiveContext,
    deps: WorkflowHandlerDeps,
) -> None:
    deps.logger.info(f"Reprocess requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "reprocess")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "reprocess")
    if not project_key:
        return

    task_file = deps.find_task_file_by_issue(issue_num)
    details = None
    repo = None
    if not task_file:
        repo = deps.project_repo(project_key)
        details = deps.get_issue_details(issue_num, repo=repo)
        if not details:
            await ctx.reply_text(f"❌ Could not load issue #{issue_num}.")
            return
        body = details.get("body", "")
        match = re.search(r"Task File:\s*`([^`]+)`", body)
        task_file = match.group(1) if match else None

    project_name = None
    config: dict[str, Any] | None = None
    content = ""
    task_type = "feature"

    if task_file and os.path.exists(task_file):
        project_name, config = deps.resolve_project_config_from_task(task_file)
        if not config or not config.get("agents_dir"):
            fallback_config = deps.project_config.get(project_key)
            if isinstance(fallback_config, dict) and fallback_config.get("agents_dir"):
                config = fallback_config
                project_name = project_key

        if not config or not config.get("agents_dir"):
            name = project_name or "unknown"
            await ctx.reply_text(f"❌ No agents config for project '{name}'.")
            return

        with open(task_file, encoding="utf-8") as handle:
            content = handle.read()

        type_match = re.search(r"\*\*Type:\*\*\s*(.+)", content)
        task_type = type_match.group(1).strip().lower() if type_match else "feature"
    else:
        fallback_config = deps.project_config.get(project_key)
        if not isinstance(fallback_config, dict):
            await ctx.reply_text(
                f"❌ Task file not found for issue #{issue_num} and no project config fallback."
            )
            return
        if not fallback_config.get("agents_dir") or not fallback_config.get("workspace"):
            await ctx.reply_text(
                f"❌ Project config for '{project_key}' is missing agents_dir/workspace."
            )
            return

        config = fallback_config
        project_name = project_key

        if not details:
            repo = deps.project_repo(project_key)
            details = deps.get_issue_details(issue_num, repo=repo)
        if not details:
            await ctx.reply_text(f"❌ Could not load issue #{issue_num}.")
            return

        title = str(details.get("title") or "").strip()
        body = str(details.get("body") or "").strip()
        if title and body:
            content = f"# {title}\n\n{body}"
        elif title:
            content = f"# {title}"
        elif body:
            content = body
        else:
            content = f"Issue #{issue_num}"

        labels = details.get("labels")
        if isinstance(labels, list):
            for label in labels:
                if isinstance(label, dict):
                    name = str(label.get("name") or "").strip().lower()
                else:
                    name = str(label or "").strip().lower()
                if name.startswith("type:"):
                    candidate = name.split(":", 1)[1].strip()
                    if candidate:
                        task_type = candidate
                        break

    from nexus.adapters.git.utils import build_issue_url, resolve_repo

    repo = resolve_repo(config, deps.default_repo)
    if not details:
        details = deps.get_issue_details(issue_num, repo=repo)
        if not details:
            await ctx.reply_text(f"❌ Could not load issue #{issue_num}.")
            return

    if details.get("state") == "closed":
        await ctx.reply_text(
            f"⚠️ Issue #{issue_num} is closed. Reprocess only applies to open issues."
        )
        return

    tracker_tier = deps.get_last_tier_for_issue(issue_num)
    label_tier = deps.get_sop_tier_from_issue(issue_num, project_name or project_key)
    tier_name = label_tier or tracker_tier
    if not tier_name:
        await ctx.reply_text(
            f"⚠️ Cannot determine workflow tier for issue #{issue_num}.\n"
            f"Add a `workflow:` label (e.g. `workflow:full`) to the issue and retry."
        )
        return

    issue_url = build_issue_url(repo, issue_num, config)

    msg_id = await ctx.reply_text(f"🔁 Reprocessing issue #{issue_num}...")

    agents_abs = os.path.join(deps.base_dir, config["agents_dir"])
    workspace_abs = os.path.join(deps.base_dir, config["workspace"])

    log_subdir = project_name or project_key
    pid, tool_used = deps.invoke_copilot_agent(
        agents_dir=agents_abs,
        workspace_dir=workspace_abs,
        issue_url=issue_url,
        tier_name=tier_name,
        task_content=content,
        log_subdir=log_subdir,
        project_name=log_subdir,
    )

    if pid:
        await ctx.edit_message_text(
            message_id=msg_id,
            text=(
                f"✅ Reprocess started for issue #{issue_num}. Agent PID: {pid} (Tool: {tool_used})\n\n"
                f"🔗 {issue_url}"
            ),
        )
    else:
        await ctx.edit_message_text(
            message_id=msg_id,
            text=f"❌ Failed to launch reprocess for issue #{issue_num}.",
        )


async def continue_handler(
    ctx: InteractiveContext,
    deps: WorkflowHandlerDeps,
) -> None:
    deps.logger.info(f"Continue requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "continue")
        return

    project_key, issue_num, rest = await deps.ensure_project_issue(ctx, "continue")
    if not project_key:
        return

    continue_ctx = deps.prepare_continue_context(
        issue_num=issue_num,
        project_key=project_key,
        rest_tokens=rest or [],
        base_dir=deps.base_dir,
        project_config=deps.project_config,
        default_repo=deps.default_repo,
        find_task_file_by_issue=deps.find_task_file_by_issue,
        get_issue_details=deps.get_issue_details,
        resolve_project_config_from_task=deps.resolve_project_config_from_task,
        get_runtime_ops_plugin=deps.get_runtime_ops_plugin,
        scan_for_completions=deps.scan_for_completions,
        normalize_agent_reference=deps.normalize_agent_reference,
        get_expected_running_agent_from_workflow=deps.get_expected_running_agent_from_workflow,
        get_sop_tier_from_issue=deps.get_sop_tier_from_issue,
        get_sop_tier=deps.get_sop_tier,
    )

    if continue_ctx["status"] in {"error", "already_running", "mismatch", "workflow_done_closed"}:
        await ctx.reply_text(continue_ctx["message"])
        return

    if continue_ctx["status"] == "workflow_done_open":
        msg_id = await ctx.reply_text(
            f"✅ Workflow complete for issue #{issue_num} (last agent: `{continue_ctx['resumed_from']}`)\n"
            f"Issue is still open — running finalization now..."
        )
        try:
            from inbox_processor import _finalize_workflow

            _finalize_workflow(
                issue_num,
                continue_ctx["repo"],
                continue_ctx["resumed_from"],
                continue_ctx["project_name"],
            )
            await ctx.edit_message_text(
                message_id=msg_id,
                text=(
                    f"✅ Workflow complete for issue #{issue_num}\n"
                    f"Last agent: `{continue_ctx['resumed_from']}`\n"
                    "Issue finalized (closed + PR if applicable)."
                ),
            )
        except Exception as exc:
            deps.logger.error(f"Finalization failed for issue #{issue_num}: {exc}", exc_info=True)
            await ctx.edit_message_text(
                message_id=msg_id,
                text=f"⚠️ Finalization error for issue #{issue_num}: {exc}",
            )
        return

    if continue_ctx["status"] != "ready":
        await ctx.reply_text(
            f"⚠️ Unexpected continue state: {continue_ctx['status']}"
        )
        return

    if continue_ctx.get("forced_agent_override"):
        workflow_plugin = deps.get_workflow_state_plugin(
            **deps.workflow_state_plugin_kwargs,
            cache_key="workflow:state-engine",
        )
        reset_ok = False
        if workflow_plugin:
            try:
                reset_ok = await workflow_plugin.reset_to_agent_for_issue(
                    issue_num,
                    continue_ctx["agent_type"],
                )
            except Exception as exc:
                deps.logger.error(
                    "Failed to reset workflow state for issue #%s to %s: %s",
                    issue_num,
                    continue_ctx["agent_type"],
                    exc,
                    exc_info=True,
                )
        if not reset_ok:
            await ctx.reply_text(
                f"❌ Could not reset workflow to `{continue_ctx['agent_type']}` for issue #{issue_num}."
            )
            return

    resume_info = f" (after {continue_ctx['resumed_from']})" if continue_ctx["resumed_from"] else ""
    msg_id = await ctx.reply_text(
        f"⏩ Continuing issue #{issue_num} with `{continue_ctx['agent_type']}`{resume_info}..."
    )

    pid, tool_used = deps.invoke_copilot_agent(
        agents_dir=continue_ctx["agents_abs"],
        workspace_dir=continue_ctx["workspace_abs"],
        issue_url=continue_ctx["issue_url"],
        tier_name=continue_ctx["tier_name"],
        task_content=continue_ctx["content"],
        continuation=True,
        continuation_prompt=continue_ctx["continuation_prompt"],
        log_subdir=continue_ctx["log_subdir"],
        agent_type=continue_ctx["agent_type"],
        project_name=continue_ctx["log_subdir"],
    )

    if pid:
        await ctx.edit_message_text(
            message_id=msg_id,
            text=(
                f"✅ Agent continued for issue #{issue_num}. PID: {pid} (Tool: {tool_used})\n\n"
                f"Prompt: {continue_ctx['continuation_prompt']}\n\n"
                "ℹ️ **Note:** The agent will first check if the workflow has already progressed.\n"
                "If another agent is already handling the next step, this agent will exit gracefully.\n"
                "Use `/continue` only when an agent is truly stuck mid-step.\n\n"
                f"🔗 {continue_ctx['issue_url']}"
            ),
        )
    else:
        await ctx.edit_message_text(
            message_id=msg_id,
            text=f"❌ Failed to continue agent for issue #{issue_num}.",
        )

async def kill_handler(
    ctx: InteractiveContext,
    deps: WorkflowHandlerDeps,
) -> None:
    deps.logger.info(f"Kill requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "kill")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "kill")
    if not project_key:
        return

    kill_result = deps.kill_issue_agent(issue_num=issue_num, get_runtime_ops_plugin=deps.get_runtime_ops_plugin)
    if kill_result["status"] == "not_running":
        await ctx.reply_text(kill_result["message"])
        return

    msg_id = await ctx.reply_text(
        f"🔪 Killing agent for issue #{issue_num} (PID: {kill_result.get('pid', 'n/a')})..."
    )

    if kill_result["status"] == "killed":
        text = f"✅ Agent killed (PID: {kill_result['pid']}).\n\nUse /reprocess {issue_num} to restart."
    elif kill_result["status"] == "stopped":
        text = f"✅ Agent stopped (PID: {kill_result['pid']}).\n\nUse /reprocess {issue_num} to restart."
    else:
        text = f"❌ Error: {kill_result.get('message', 'Unknown kill error')}"

    await ctx.edit_message_text(
        message_id=msg_id,
        text=text,
    )

async def reconcile_handler(
    ctx: InteractiveContext,
    deps: WorkflowHandlerDeps,
) -> None:
    deps.logger.info(f"Reconcile requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "reconcile")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "reconcile")
    if not project_key:
        return

    repo = deps.project_repo(project_key)

    msg_id = await ctx.reply_text(
        f"🔄 Reconciling issue #{issue_num} from structured GitHub comments..."
    )

    result = await deps.reconcile_issue_from_signals(
        issue_num=issue_num,
        project_key=project_key,
        repo=repo,
        get_issue_plugin=deps.get_direct_issue_plugin,
        extract_structured_completion_signals=deps.extract_structured_completion_signals,
        workflow_state_plugin_kwargs=deps.workflow_state_plugin_kwargs,
        write_local_completion_from_signal=deps.write_local_completion_from_signal,
    )

    if not result.get("ok"):
        await ctx.edit_message_text(
            message_id=msg_id,
            text=f"⚠️ {result.get('error', 'Reconcile failed.')}",
        )
        return

    await ctx.edit_message_text(
        message_id=msg_id,
        text=(
            f"✅ Reconcile completed for issue #{issue_num}\n\n"
            f"Signals scanned: {result['signals_scanned']}\n"
            f"Signals applied to workflow: {result['signals_applied']}\n"
            f"Local completion updated: `{result['completion_file']}`\n"
            f"Current workflow: `{result['workflow_state']}` | "
            f"Step {result['workflow_step']} | Agent `{result['workflow_agent']}`"
        ),
    )


async def wfstate_handler(
    ctx: InteractiveContext,
    deps: WorkflowHandlerDeps,
) -> None:
    deps.logger.info(f"Wfstate requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "wfstate")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "wfstate")
    if not project_key:
        return

    repo = deps.project_repo(project_key)

    msg_id = await ctx.reply_text(
        f"📊 Fetching workflow state for issue #{issue_num}..."
    )

    state = await deps.get_workflow_state(
        issue_num=issue_num,
        project_key=project_key,
        repo=repo,
        get_issue_plugin=deps.get_direct_issue_plugin,
        extract_structured_completion_signals=deps.extract_structured_completion_signals,
        workflow_state_plugin_kwargs=deps.workflow_state_plugin_kwargs,
        write_local_completion_from_signal=deps.write_local_completion_from_signal,
        build_workflow_snapshot=deps.build_workflow_snapshot,
        read_latest_local_completion=deps.read_latest_local_completion,
    )

    if not state.get("ok"):
        await ctx.edit_message_text(
            message_id=msg_id,
            text=f"⚠️ {state.get('error', 'Failed to fetch workflow state.')}",
        )
        return

    snapshot = state["snapshot"]
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

    text = f"📊 Workflow Snapshot — Issue #{issue_num}\n\n"
    summary = {
        "Repo": snapshot.get("repo", "N/A"),
        "Workflow ID": snapshot.get("workflow_id", "N/A"),
        "Workflow State": snapshot.get("workflow_state", "N/A"),
        "Current Step": f"{snapshot.get('current_step', 'N/A')} ({snapshot.get('current_step_name', 'N/A')})",
        "Current Agent": snapshot.get("current_agent", "N/A"),
        "Expected RUNNING Agent": snapshot.get("expected_running_agent", "N/A"),
        "Process": "running" if snapshot.get("running") else "stopped",
        "PID": snapshot.get("pid", "N/A"),
        "Task File": snapshot.get("task_file", "N/A"),
        "Workflow File": snapshot.get("workflow_file", "N/A"),
        "Local Completion (from)": snapshot.get("local_from", "N/A"),
        "Local Completion (next)": snapshot.get("local_next", "N/A"),
        "Local Completion (status)": (snapshot.get("local", {})).get("status", "N/A"),
        "Local Completion (updated)": (snapshot.get("local", {})).get("mtime", "N/A"),
        "Local Completion (file)": (snapshot.get("local", {})).get("path", "N/A"),
        "Latest Structured Comment (from)": snapshot.get("comment_from", "N/A"),
        "Latest Structured Comment (next)": snapshot.get("comment_next", "N/A"),
        "Latest Structured Comment (comment_id)": (snapshot.get("latest_signal", {})).get("comment_id", "N/A"),
        "Latest Structured Comment (created)": (snapshot.get("latest_signal", {})).get("created", "N/A"),
        "Latest Processor Signal (type)": processor_type,
        "Latest Processor Signal (severity)": processor_severity,
        "Latest Processor Signal (at)": processor_at,
        "Latest Processor Signal (detail)": processor_line,
        "Recovery Hint": recovery_hint,
        "Drift Flags": ', '.join(snapshot['drift_flags']) if snapshot.get('drift_flags') else 'none',
    }
        
    for k, v in summary.items():
        text += f"- **{k}**: {v}\n"

    if snapshot.get("workflow_pointer_mismatch"):
        text += "\n⚠️ **Workflow Pointer Mismatch**:\n"
        text += f"- **indexed step**: {snapshot.get('indexed_step', 'N/A')} ({snapshot.get('indexed_step_name', 'N/A')}) / {snapshot.get('indexed_agent', 'N/A')}\n"
        text += f"- **running step**: {snapshot.get('running_step', 'N/A')} ({snapshot.get('running_step_name', 'N/A')}) / {snapshot.get('running_agent', 'N/A')}\n"

    await ctx.edit_message_text(
        message_id=msg_id,
        text=text,
    )


async def pause_handler(
    ctx: InteractiveContext,
    deps: WorkflowHandlerDeps,
) -> None:
    deps.logger.info(f"Pause requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "pause")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "pause")
    if not project_key:
        return

    ctx.args = [project_key, issue_num]
    await deps.workflow_pause_handler(ctx)


async def resume_handler(
    ctx: InteractiveContext,
    deps: WorkflowHandlerDeps,
) -> None:
    deps.logger.info(f"Resume requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "resume")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "resume")
    if not project_key:
        return

    ctx.args = [project_key, issue_num]
    await deps.workflow_resume_handler(ctx)


async def stop_handler(
    ctx: InteractiveContext,
    deps: WorkflowHandlerDeps,
) -> None:
    deps.logger.info(f"Stop requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "stop")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "stop")
    if not project_key:
        return

    ctx.args = [project_key, issue_num]
    await deps.workflow_stop_handler(ctx)


async def forget_handler(
    ctx: InteractiveContext,
    deps: WorkflowHandlerDeps,
) -> None:
    deps.logger.info(f"Forget requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "forget")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "forget")
    if not project_key:
        return

    if project_key not in deps.project_config:
        await ctx.reply_text("❌ Invalid project.")
        return

    workflow_id = get_workflow_state().get_workflow_id(str(issue_num))
    workflow_file_deleted = False
    if workflow_id:
        workflow_file = os.path.join(NEXUS_CORE_STORAGE_DIR, "workflows", f"{workflow_id}.json")
        if os.path.exists(workflow_file):
            try:
                os.remove(workflow_file)
                workflow_file_deleted = True
            except OSError as exc:
                deps.logger.warning(
                    "Failed to delete workflow file for issue #%s: %s",
                    issue_num,
                    exc,
                )

    runtime_ops = deps.get_runtime_ops_plugin(cache_key="runtime-ops:workflow")
    pid = runtime_ops.find_agent_pid_for_issue(issue_num) if runtime_ops else None
    killed = False
    if pid and runtime_ops:
        killed = bool(runtime_ops.kill_process(pid, force=True))

    launched = HostStateManager.load_launched_agents(recent_only=False)
    launched_removed = launched.pop(str(issue_num), None) is not None
    HostStateManager.save_launched_agents(launched)

    tracked = HostStateManager.load_tracked_issues()
    tracked_removed = tracked.pop(str(issue_num), None) is not None
    HostStateManager.save_tracked_issues(tracked)

    get_workflow_state().remove_mapping(str(issue_num))
    get_workflow_state().clear_pending_approval(str(issue_num))

    cleared_guards = clear_launch_guard(str(issue_num))

    try:
        completion_path = os.path.join(os.path.dirname(NEXUS_CORE_STORAGE_DIR), "completion_comments.json")
        with open(completion_path, encoding="utf-8") as handle:
            completion_data = json.load(handle) or {}
        if isinstance(completion_data, dict):
            to_delete = [key for key in completion_data if key.startswith(f"{issue_num}:")]
            for key in to_delete:
                completion_data.pop(key, None)
            if to_delete:
                with open(completion_path, "w", encoding="utf-8") as handle:
                    json.dump(completion_data, handle)
    except Exception as exc:
        deps.logger.debug("completion_comments cleanup skipped for issue #%s: %s", issue_num, exc)

    await update.effective_message.reply_text(
        "🧹 **Issue state forgotten**\n\n"
        f"Issue: #{issue_num}\n"
        f"Project: {project_key}\n"
        f"Workflow mapping: cleared{' (file deleted)' if workflow_file_deleted else ''}\n"
        f"Tracker state: {'removed' if launched_removed else 'not present'}\n"
        f"Tracked issue: {'removed' if tracked_removed else 'not present'}\n"
        f"Running PID: {'killed' if killed else ('found but kill failed' if pid else 'none')}\n"
        f"Launch guards cleared: {cleared_guards}\n\n"
        "This issue will no longer auto-retry or emit orphan notifications unless relaunched manually."
    )
