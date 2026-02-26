from __future__ import annotations

import os
import re
from typing import Any, Callable

from config import NEXUS_CORE_STORAGE_DIR
from integrations.workflow_state_factory import get_workflow_state
from runtime.agent_launcher import clear_launch_guard
from utils.log_utils import log_unauthorized_access


async def handle_reprocess(ctx: Any, deps: Any, *, build_issue_url: Callable[..., str], resolve_repo: Callable[..., str]) -> None:
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
            await ctx.reply_text(f"❌ No agents config for project '{project_name or 'unknown'}'.")
            return
        with open(task_file, encoding="utf-8") as handle:
            content = handle.read()
        type_match = re.search(r"\*\*Type:\*\*\s*(.+)", content)
        task_type = type_match.group(1).strip().lower() if type_match else "feature"
    else:
        fallback_config = deps.project_config.get(project_key)
        if not isinstance(fallback_config, dict):
            await ctx.reply_text(f"❌ Task file not found for issue #{issue_num} and no project config fallback.")
            return
        if not fallback_config.get("agents_dir") or not fallback_config.get("workspace"):
            await ctx.reply_text(f"❌ Project config for '{project_key}' is missing agents_dir/workspace.")
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
        content = f"# {title}\n\n{body}" if title and body else (f"# {title}" if title else (body or f"Issue #{issue_num}"))
        labels = details.get("labels")
        if isinstance(labels, list):
            for label in labels:
                name = str((label.get("name") if isinstance(label, dict) else label) or "").strip().lower()
                if name.startswith("type:"):
                    candidate = name.split(":", 1)[1].strip()
                    if candidate:
                        task_type = candidate
                        break

    repo = resolve_repo(config, deps.default_repo)
    if not details:
        details = deps.get_issue_details(issue_num, repo=repo)
        if not details:
            await ctx.reply_text(f"❌ Could not load issue #{issue_num}.")
            return
    if details.get("state") == "closed":
        await ctx.reply_text(f"⚠️ Issue #{issue_num} is closed. Reprocess only applies to open issues.")
        return

    tracker_tier = deps.get_last_tier_for_issue(issue_num)
    label_tier = deps.get_sop_tier_from_issue(issue_num, project_name or project_key)
    tier_name = label_tier or tracker_tier
    if not tier_name:
        await ctx.reply_text(
            f"⚠️ Cannot determine workflow tier for issue #{issue_num}.\nAdd a `workflow:` label (e.g. `workflow:full`) to the issue and retry."
        )
        return
    issue_url = build_issue_url(repo, issue_num, config)
    msg_id = await ctx.reply_text(f"🔁 Reprocessing issue #{issue_num}...")
    pid, tool_used = deps.invoke_copilot_agent(
        agents_dir=os.path.join(deps.base_dir, config["agents_dir"]),
        workspace_dir=os.path.join(deps.base_dir, config["workspace"]),
        issue_url=issue_url,
        tier_name=tier_name,
        task_content=content,
        log_subdir=project_name or project_key,
        project_name=project_name or project_key,
    )
    if pid:
        await ctx.edit_message_text(message_id=msg_id, text=f"✅ Reprocess started for issue #{issue_num}. Agent PID: {pid} (Tool: {tool_used})\n\n🔗 {issue_url}")
    else:
        await ctx.edit_message_text(message_id=msg_id, text=f"❌ Failed to launch reprocess for issue #{issue_num}.")


async def handle_continue(ctx: Any, deps: Any, *, finalize_workflow: Callable[..., Any]) -> None:
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
        issue_num=issue_num, project_key=project_key, rest_tokens=rest or [], base_dir=deps.base_dir,
        project_config=deps.project_config, default_repo=deps.default_repo, find_task_file_by_issue=deps.find_task_file_by_issue,
        get_issue_details=deps.get_issue_details, resolve_project_config_from_task=deps.resolve_project_config_from_task,
        get_runtime_ops_plugin=deps.get_runtime_ops_plugin, scan_for_completions=deps.scan_for_completions,
        normalize_agent_reference=deps.normalize_agent_reference,
        get_expected_running_agent_from_workflow=deps.get_expected_running_agent_from_workflow,
        get_sop_tier_from_issue=deps.get_sop_tier_from_issue, get_sop_tier=deps.get_sop_tier,
    )
    if continue_ctx["status"] in {"error", "already_running", "mismatch", "workflow_done_closed"}:
        await ctx.reply_text(continue_ctx["message"]); return
    if continue_ctx["status"] == "workflow_done_open":
        msg_id = await ctx.reply_text(
            f"✅ Workflow complete for issue #{issue_num} (last agent: `{continue_ctx['resumed_from']}`)\nIssue is still open — running finalization now..."
        )
        try:
            finalize_workflow(issue_num, continue_ctx["repo"], continue_ctx["resumed_from"], continue_ctx["project_name"])
            await ctx.edit_message_text(message_id=msg_id, text=f"✅ Workflow complete for issue #{issue_num}\nLast agent: `{continue_ctx['resumed_from']}`\nIssue finalized (closed + PR if applicable).")
        except Exception as exc:
            deps.logger.error(f"Finalization failed for issue #{issue_num}: {exc}", exc_info=True)
            await ctx.edit_message_text(message_id=msg_id, text=f"⚠️ Finalization error for issue #{issue_num}: {exc}")
        return
    if continue_ctx["status"] != "ready":
        await ctx.reply_text(f"⚠️ Unexpected continue state: {continue_ctx['status']}"); return

    if continue_ctx.get("forced_agent_override"):
        workflow_plugin = deps.get_workflow_state_plugin(**deps.workflow_state_plugin_kwargs, cache_key="workflow:state-engine")
        reset_ok = False
        if workflow_plugin:
            try:
                reset_ok = await workflow_plugin.reset_to_agent_for_issue(issue_num, continue_ctx["agent_type"])
            except Exception as exc:
                deps.logger.error("Failed to reset workflow state for issue #%s to %s: %s", issue_num, continue_ctx["agent_type"], exc, exc_info=True)
        if not reset_ok:
            await ctx.reply_text(f"❌ Could not reset workflow to `{continue_ctx['agent_type']}` for issue #{issue_num}.")
            return

    resume_info = f" (after {continue_ctx['resumed_from']})" if continue_ctx["resumed_from"] else ""
    msg_id = await ctx.reply_text(f"⏩ Continuing issue #{issue_num} with `{continue_ctx['agent_type']}`{resume_info}...")
    pid, tool_used = deps.invoke_copilot_agent(
        agents_dir=continue_ctx["agents_abs"], workspace_dir=continue_ctx["workspace_abs"], issue_url=continue_ctx["issue_url"],
        tier_name=continue_ctx["tier_name"], task_content=continue_ctx["content"], continuation=True,
        continuation_prompt=continue_ctx["continuation_prompt"], log_subdir=continue_ctx["log_subdir"],
        agent_type=continue_ctx["agent_type"], project_name=continue_ctx["log_subdir"],
    )
    if pid:
        await ctx.edit_message_text(
            message_id=msg_id,
            text=(f"✅ Agent continued for issue #{issue_num}. PID: {pid} (Tool: {tool_used})\n\n"
                  f"Prompt: {continue_ctx['continuation_prompt']}\n\n"
                  "ℹ️ **Note:** The agent will first check if the workflow has already progressed.\n"
                  "If another agent is already handling the next step, this agent will exit gracefully.\n"
                  "Use `/continue` only when an agent is truly stuck mid-step.\n\n"
                  f"🔗 {continue_ctx['issue_url']}"),
        )
    else:
        await ctx.edit_message_text(message_id=msg_id, text=f"❌ Failed to continue agent for issue #{issue_num}.")
