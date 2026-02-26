from __future__ import annotations

from typing import Any

from services.callbacks.callback_registry_service import dispatch_callback_action

from nexus.adapters.notifications.base import Button


def menu_root_buttons() -> list[list[Button]]:
    return [
        [Button("🗣️ Chat", callback_data="menu:chat")],
        [Button("✨ Task Creation", callback_data="menu:tasks")],
        [Button("📊 Monitoring", callback_data="menu:monitor")],
        [Button("🔁 Workflow Control", callback_data="menu:workflow")],
        [Button("🤝 Agents", callback_data="menu:agents")],
        [Button("🔧 Git Platform", callback_data="menu:github")],
        [Button("ℹ️ Help", callback_data="menu:help")],
        [Button("❌ Close", callback_data="menu:close")],
    ]


def menu_section_text(menu_key: str) -> str:
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
    return menu_texts.get(menu_key, "Unknown menu option.")


def menu_back_close_buttons() -> list[list[Button]]:
    return [
        [Button("⬅️ Back", callback_data="menu:root")],
        [Button("❌ Close", callback_data="menu:close")],
    ]


async def handle_menu_callback(ctx: Any) -> None:
    await ctx.answer_callback_query()
    query_data = ctx.query.data
    if not query_data:
        return

    menu_key = query_data.split(":", 1)[1]

    async def _handle_close() -> None:
        await ctx.edit_message_text(ctx.text, buttons=[])

    async def _handle_root() -> None:
        await ctx.edit_message_text(
            "📍 **Nexus Menu**\nChoose a category:", buttons=menu_root_buttons()
        )

    async def _handle_default() -> None:
        await ctx.edit_message_text(menu_section_text(menu_key), buttons=menu_back_close_buttons())

    await dispatch_callback_action(
        action=menu_key,
        handlers={
            "close": _handle_close,
            "root": _handle_root,
        },
        default_handler=_handle_default,
    )
