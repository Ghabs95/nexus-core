"""Callback and picker handlers extracted from telegram_bot."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from interactive_context import InteractiveContext

from nexus.adapters.notifications.base import Button


@dataclass
class CallbackHandlerDeps:
    logger: logging.Logger
    github_repo: str
    prompt_issue_selection: Callable[..., Awaitable[None]]
    prompt_project_selection: Callable[..., Awaitable[None]]
    dispatch_command: Callable[..., Awaitable[None]]
    get_project_label: Callable[[str], str]
    status_handler: Callable[..., Awaitable[None]]
    active_handler: Callable[..., Awaitable[None]]
    get_direct_issue_plugin: Callable[[str], Any]
    get_workflow_state_plugin: Callable[..., Any]
    workflow_state_plugin_kwargs: dict[str, Any]
    action_handlers: dict[str, Callable[..., Awaitable[None]]]


async def core_callback_router(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    data = ctx.query.data
    if data.startswith("pickcmd:"):
        await project_picker_handler(ctx, deps)
    elif data.startswith("pickissue") or data.startswith("pickissue_manual:") or data.startswith("pickissue_state:"):
        await issue_picker_handler(ctx, deps)
    elif data.startswith("pickmonitor:"):
        await monitor_project_picker_handler(ctx, deps)
    elif data.startswith("flow:close"):
        await flow_close_handler(ctx, deps)
    elif data.startswith("menu:"):
        await menu_callback_handler(ctx, deps)
    elif data == "close":
        await close_flow_handler(ctx, deps)


async def project_picker_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    query_data = ctx.query.data

    if not query_data or not query_data.startswith("pickcmd:"):
        return

    _, command, project_key = query_data.split(":", 2)
    ctx.user_state["pending_command"] = command
    ctx.user_state["pending_project"] = project_key

    if command == "agents":
        ctx.user_state.pop("pending_command", None)
        ctx.user_state.pop("pending_project", None)
        await deps.dispatch_command(ctx, command, project_key, "")
        return

    pending_issue = ctx.user_state.get("pending_issue")
    if pending_issue and command != "respond":
        ctx.user_state.pop("pending_issue", None)
        await deps.dispatch_command(ctx, command, project_key, pending_issue)
        return

    if pending_issue and command == "respond":
        await ctx.edit_message_text(
            f"Selected {deps.get_project_label(project_key)}. Now send the response message."
        )
        return

    await deps.prompt_issue_selection(ctx, command, project_key, edit_message=True)


async def issue_picker_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    query_data = ctx.query.data

    if not query_data:
        return

    if query_data.startswith("pickissue_manual:"):
        _, command, project_key = query_data.split(":", 2)
        ctx.user_state["pending_command"] = command
        ctx.user_state["pending_project"] = project_key
        await ctx.edit_message_text(
            f"Selected {deps.get_project_label(project_key)}. Send the issue number."
        )
        return

    if query_data.startswith("pickissue_state:"):
        _, issue_state, command, project_key = query_data.split(":", 3)
        await deps.prompt_issue_selection(
            ctx,
            command,
            project_key,
            edit_message=True,
            issue_state=issue_state,
        )
        return

    if not query_data.startswith("pickissue:"):
        return

    _, command, project_key, issue_num = query_data.split(":", 3)
    await ctx.edit_message_text(ctx.text, buttons=[])
    await deps.dispatch_command(ctx, command, project_key, issue_num)


async def monitor_project_picker_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    query_data = ctx.query.data

    if not query_data or not query_data.startswith("pickmonitor:"):
        return

    _, command, project_key = query_data.split(":", 2)
    ctx.args = [project_key]

    if command == "status":
        await deps.status_handler(ctx)
        return
    if command == "active":
        await deps.active_handler(ctx)
        return

    await ctx.edit_message_text("Unsupported monitoring command.")


async def close_flow_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    await ctx.edit_message_text(ctx.text, buttons=[])


async def flow_close_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    await ctx.edit_message_text("❌ Cancelled.")


async def menu_callback_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    query_data = ctx.query.data

    if not query_data:
        return

    menu_key = query_data.split(":", 1)[1]

    if menu_key == "close":
        await ctx.edit_message_text(ctx.text, buttons=[])
        return

    if menu_key == "root":
        keyboard = [
            [Button("🗣️ Chat", callback_data="menu:chat")],
            [Button("✨ Task Creation", callback_data="menu:tasks")],
            [Button("📊 Monitoring", callback_data="menu:monitor")],
            [Button("🔁 Workflow Control", callback_data="menu:workflow")],
            [Button("🤝 Agents", callback_data="menu:agents")],
            [Button("🔧 Git Platform", callback_data="menu:github")],
            [Button("ℹ️ Help", callback_data="menu:help")],
            [Button("❌ Close", callback_data="menu:close")],
        ]
        await ctx.edit_message_text(
            "📍 **Nexus Menu**\nChoose a category:",
            buttons=keyboard
        )
        return

    menu_texts = {
        "chat": (
            "🗣️ **Chat**\n"
            "- /chat — Open chat threads and context controls\n"
            "- /chatagents [project] — Show ordered chat agent types (first is primary)\n"
            "- Configure project, mode, and primary agent for conversational routing"
        ),
        "tasks": (
            "✨ **Task Creation**\n"
            "- /menu — Open command menu\n"
            "- /new — Start task creation\n"
            "- /cancel — Abort the current guided process\n\n"
            "Tip: send a voice note or text to auto-create a task."
        ),
        "monitor": (
            "📊 **Monitoring**\n"
            "- /status — View pending tasks in inbox\n"
            "- /inboxq [limit] — Inspect inbox queue status\n"
            "- /active — View tasks currently being worked on\n"
            "- /myissues — View your tracked issues\n"
            "- /logs <project> <issue#> — View task logs\n"
            "- /logsfull <project> <issue#> — Full log lines (no truncation)\n"
            "- /tail <project> <issue#> [lines] [seconds] — Follow live logs\n"
            "- /tailstop — Stop current live tail session\n"
            "- /fuse <project> <issue#> — View retry fuse state\n"
            "- /audit <project> <issue#> — View workflow audit trail\n"
            "- /stats [days] — View system analytics (default: 30 days)\n"
            "- /comments <project> <issue#> — View issue comments\n"
            "- /track <project> <issue#> — Subscribe to updates\n"
            "- /untrack <project> <issue#> — Stop tracking"
        ),
        "workflow": (
            "🔁 **Workflow Control**\n"
            "- /visualize <project> <issue#> — Show Mermaid workflow diagram\n"
            "- /reprocess <project> <issue#> — Re-run agent processing\n"
            "- /wfstate <project> <issue#> — Show workflow state + drift\n"
            "- /reconcile <project> <issue#> — Reconcile workflow/comment/local state\n"
            "- /continue <project> <issue#> — Resume a stuck agent\n"
            "- /forget <project> <issue#> — Purge local state for a stale/deleted issue\n"
            "- /kill <project> <issue#> — Stop a running agent\n"
            "- /pause <project> <issue#> — Pause auto-chaining\n"
            "- /resume <project> <issue#> — Resume auto-chaining\n"
            "- /stop <project> <issue#> — Stop workflow completely\n"
            "- /respond <project> <issue#> <text> — Respond to agent questions"
        ),
        "agents": (
            "🤝 **Agents**\n"
            "- /agents <project> — List agents for a project\n"
            "- /direct <project> <@agent> <message> — Send direct request\n"
            "- /direct <project> <@agent> --new-chat <message> — Strategic direct reply in a new chat"
        ),
        "github": (
            "🔧 **Git Platform**\n"
            "- /assign <project> <issue#> — Assign issue to yourself\n"
            "- /implement <project> <issue#> — Request Copilot implementation\n"
            "- /prepare <project> <issue#> — Add Copilot-friendly instructions"
        ),
        "help": "ℹ️ Use /help for the full command list.",
    }

    text = menu_texts.get(menu_key, "Unknown menu option.")
    await ctx.edit_message_text(
        text,
        buttons=[
            [Button("⬅️ Back", callback_data="menu:root")],
            [Button("❌ Close", callback_data="menu:close")],
        ]
    )


async def inline_keyboard_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    query_data = ctx.query.data

    if not query_data:
        return

    parts = query_data.split("_", 1)
    if len(parts) < 2:
        return

    action = parts[0]
    issue_num = parts[1]

    deps.logger.info(f"Inline keyboard action: {action} for issue #{issue_num}")

    if action in deps.action_handlers:
        ctx.user_state["pending_command"] = action
        ctx.user_state["pending_issue"] = issue_num
        await deps.prompt_project_selection(ctx, action)
    elif action == "respond":
        await ctx.edit_message_text(
            f"✍️ To respond to issue #{issue_num}, use:\n\n"
            f"`/respond {issue_num} <your message>`\n\n"
            f"Example:\n"
            f"`/respond {issue_num} Approved, proceed with implementation`"
        )
    elif action == "approve":
        ctx.args = [issue_num]
        await ctx.edit_message_text(f"✅ Approving implementation for issue #{issue_num}...")

        try:
            plugin = deps.get_direct_issue_plugin(deps.github_repo)
            if not plugin or not plugin.add_comment(
                issue_num,
                "✅ Implementation approved. Please proceed.",
            ):
                await ctx.edit_message_text(f"❌ Error approving issue #{issue_num}")
                return
            await ctx.edit_message_text(
                f"✅ Implementation approved for issue #{issue_num}\n\n"
                f"Agent will continue automatically."
            )
        except Exception as exc:
            await ctx.edit_message_text(f"❌ Error approving: {exc}")
    elif action == "reject":
        ctx.args = [issue_num]
        await ctx.edit_message_text(f"❌ Rejecting implementation for issue #{issue_num}...")

        try:
            plugin = deps.get_direct_issue_plugin(deps.github_repo)
            if not plugin or not plugin.add_comment(
                issue_num,
                "❌ Implementation rejected. Please revise.",
            ):
                await ctx.edit_message_text(f"❌ Error rejecting issue #{issue_num}")
                return
            await ctx.edit_message_text(
                f"❌ Implementation rejected for issue #{issue_num}\n\n"
                f"Agent has been notified."
            )
        except Exception as exc:
            await ctx.edit_message_text(f"❌ Error rejecting: {exc}")
    elif action == "wfapprove":
        parts2 = issue_num.split("_", 1)
        real_issue = parts2[0]
        step_num = parts2[1] if len(parts2) > 1 else "?"
        await ctx.edit_message_text(
            f"✅ Approving workflow step {step_num} for issue #{real_issue}..."
        )
        try:
            workflow_plugin = deps.get_workflow_state_plugin(
                **deps.workflow_state_plugin_kwargs,
                cache_key="workflow:state-engine",
            )
            approved_by = ctx.client.name
            if not workflow_plugin or not await workflow_plugin.approve_step(real_issue, approved_by):
                await ctx.edit_message_text(
                    f"❌ No workflow found for issue #{real_issue}"
                )
                return
            await ctx.edit_message_text(
                f"✅ Step {step_num} approved for issue #{real_issue}\n\n"
                f"Workflow will continue automatically."
            )
        except Exception as exc:
            await ctx.edit_message_text(f"❌ Error approving workflow step: {exc}")
    elif action == "wfdeny":
        parts2 = issue_num.split("_", 1)
        real_issue = parts2[0]
        step_num = parts2[1] if len(parts2) > 1 else "?"
        await ctx.edit_message_text(
            f"❌ Denying workflow step {step_num} for issue #{real_issue}..."
        )
        try:
            workflow_plugin = deps.get_workflow_state_plugin(
                **deps.workflow_state_plugin_kwargs,
                cache_key="workflow:state-engine",
            )
            denied_by = ctx.client.name
            if not workflow_plugin or not await workflow_plugin.deny_step(
                real_issue,
                denied_by,
                reason="Denied via Interactive Client",
            ):
                await ctx.edit_message_text(
                    f"❌ No workflow found for issue #{real_issue}"
                )
                return
            await ctx.edit_message_text(
                f"❌ Step {step_num} denied for issue #{real_issue}\n\n"
                f"Workflow has been stopped."
            )
        except Exception as exc:
            await ctx.edit_message_text(f"❌ Error denying workflow step: {exc}")
