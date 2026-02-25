import logging

from nexus.adapters.notifications.base import Button

from config import get_chat_agent_types, get_chat_agents
from handlers.inbox_routing_handler import PROJECTS
from interactive_context import InteractiveContext
from services.memory_service import (
    create_chat,
    delete_chat,
    get_active_chat,
    get_chat,
    list_chats,
    set_active_chat,
    update_chat_metadata,
)

logger = logging.getLogger(__name__)

CHAT_RENAME_INPUT = 10

CHAT_MODES = {
    "strategy": "Strategy",
    "execution": "Execution",
}

PRIMARY_AGENT_TYPES = {
    "ceo": "CEO",
    "business": "Business Advisor",
    "marketing": "Marketing Advisor",
    "cto": "CTO",
    "architect": "Architect",
    "triage": "Triage",
    "developer": "Developer",
    "reviewer": "Reviewer",
    "compliance": "Compliance",
    "deployer": "Deployer",
    "debug": "Debug",
    "designer": "Designer",
    "docs": "Docs",
    "writer": "Writer",
    "finalizer": "Finalizer",
}


def _agent_type_label(agent_type: str) -> str:
    value = str(agent_type or "").strip().lower()
    if not value:
        return "Unknown"
    return PRIMARY_AGENT_TYPES.get(value, value.replace("_", " ").title())


def _agent_display_label(agent: dict) -> str:
    label = str(agent.get("label") or agent.get("display_name") or "").strip()
    if label:
        return label
    return _agent_type_label(str(agent.get("agent_type") or ""))


def _available_chat_agents(chat_data: dict) -> list[dict]:
    metadata = (chat_data or {}).get("metadata") or {}
    project_key = metadata.get("project_key")

    configured_agents = get_chat_agents(project_key or "nexus") or []
    normalized_agents: list[dict] = []
    for item in configured_agents:
        if not isinstance(item, dict):
            continue
        agent_type = str(item.get("agent_type") or "").strip().lower()
        if not agent_type:
            continue
        payload = dict(item)
        payload["agent_type"] = agent_type
        normalized_agents.append(payload)
    if normalized_agents:
        return normalized_agents

    configured_types = get_chat_agent_types(project_key or "nexus") or []
    cleaned_configured = [
        str(agent_type).strip().lower()
        for agent_type in configured_types
        if str(agent_type).strip()
    ]
    if cleaned_configured:
        return [{"agent_type": value} for value in cleaned_configured]

    allowed = metadata.get("allowed_agent_types")
    if isinstance(allowed, list):
        cleaned = [str(item).strip().lower() for item in allowed if isinstance(item, str) and str(item).strip()]
        if cleaned:
            return [{"agent_type": value} for value in cleaned]

    return [{"agent_type": "triage"}]


def _available_primary_agent_types(chat_data: dict) -> list[str]:
    return [item["agent_type"] for item in _available_chat_agents(chat_data)]


def _build_main_menu_keyboard(active_chat_id: str) -> list[list[Button]]:
    keyboard = [
        [
            Button("📝 New Chat", callback_data="chat:new"),
            Button("📋 Switch Chat", callback_data="chat:list"),
        ],
        [
            Button("⚙️ Context", callback_data="chat:context"),
            Button("✏️ Rename", callback_data="chat:rename"),
        ],
        [Button("🗑️ Delete Current", callback_data=f"chat:delete:{active_chat_id}")],
    ]
    return keyboard


def _resolve_active_chat_title(chats: list, active_chat_id: str) -> str:
    for chat in chats:
        if chat.get("id") == active_chat_id:
            return chat.get("title") or "Unknown"
    return "Unknown"


def _chat_context_summary(chat_data: dict) -> str:
    metadata = (chat_data or {}).get("metadata") or {}
    project_key = metadata.get("project_key")
    project_label = PROJECTS.get(project_key, "Not set") if project_key else "Not set"
    chat_mode = CHAT_MODES.get(str(metadata.get("chat_mode", "strategy")), "Strategy")
    available_agents = _available_chat_agents(chat_data)
    available_agent_types = [item["agent_type"] for item in available_agents]
    primary_agent_type = str(metadata.get("primary_agent_type") or "").strip().lower()
    if not primary_agent_type or primary_agent_type not in available_agent_types:
        primary_agent_type = available_agent_types[0]
    agent_by_type = {item["agent_type"]: item for item in available_agents}
    primary_agent_label = _agent_display_label(agent_by_type.get(primary_agent_type, {"agent_type": primary_agent_type}))

    return (
        f"*Project:* {project_label}\n"
        f"*Mode:* {chat_mode}\n"
        f"*Primary Agent:* {primary_agent_label} (`{primary_agent_type}`)"
    )


def _build_chat_context_keyboard() -> list[list[Button]]:
    keyboard = [
        [Button("📁 Set Project", callback_data="chat:ctx:project")],
        [Button("🧭 Set Mode", callback_data="chat:ctx:mode")],
        [Button("🤖 Set Primary Agent", callback_data="chat:ctx:agent")],
        [Button("🔙 Back to Menu", callback_data="chat:menu")],
    ]
    return keyboard


async def _render_menu(ctx: InteractiveContext, user_id: int, notice: str = "") -> None:
    active_chat_id = get_active_chat(user_id)
    chats = list_chats(user_id)
    active_chat_title = _resolve_active_chat_title(chats, active_chat_id)
    active_chat = get_chat(user_id, active_chat_id)

    text = "🗣️ *Nexus Chat Menu*\n\n"
    if notice:
        text += f"{notice}\n"
    text += f"*Active Chat:* {active_chat_title}\n"
    text += f"{_chat_context_summary(active_chat)}\n"
    text += "_(All conversational history is saved under this thread)_"

    if ctx.query:
        await ctx.edit_message_text(
            message_id=ctx.query.message_id,
            text=text,
            buttons=_build_main_menu_keyboard(active_chat_id),
        )
    else:
        await ctx.reply_text(
            text=text,
            buttons=_build_main_menu_keyboard(active_chat_id),
        )


async def _render_context_menu(ctx: InteractiveContext, user_id: int, notice: str = "") -> None:
    active_chat_id = get_active_chat(user_id)
    active_chat = get_chat(user_id, active_chat_id)

    text = "⚙️ *Chat Context*\n\n"
    if notice:
        text += f"{notice}\n"
    text += _chat_context_summary(active_chat)

    if ctx.query:
        await ctx.edit_message_text(
            message_id=ctx.query.message_id,
            text=text,
            buttons=_build_chat_context_keyboard(),
        )
    else:
        await ctx.reply_text(
            text=text,
            buttons=_build_chat_context_keyboard(),
        )


def _project_picker_keyboard() -> list[list[Button]]:
    keyboard = [
        [Button(label, callback_data=f"chat:ctx:setproject:{key}")]
        for key, label in PROJECTS.items()
    ]
    keyboard.append([Button("🔙 Back", callback_data="chat:context")])
    return keyboard


def _mode_picker_keyboard() -> list[list[Button]]:
    keyboard = [
        [Button(label, callback_data=f"chat:ctx:setmode:{mode}")]
        for mode, label in CHAT_MODES.items()
    ]
    keyboard.append([Button("🔙 Back", callback_data="chat:context")])
    return keyboard


def _agent_picker_keyboard(chat_data: dict) -> list[list[Button]]:
    available_agents = _available_chat_agents(chat_data)
    keyboard = [
        [
            Button(
                _agent_display_label(agent),
                callback_data=f"chat:ctx:setagent:{agent['agent_type']}",
            )
        ]
        for agent in available_agents
    ]
    keyboard.append([Button("🔙 Back", callback_data="chat:context")])
    return keyboard

async def chat_menu_handler(ctx: InteractiveContext):
    """Handler for the /chat command to show the active chat and options."""
    user_id = int(ctx.user_id)
    
    active_chat_id = get_active_chat(user_id)
    chats = list_chats(user_id)
    
    active_chat_title = _resolve_active_chat_title(chats, active_chat_id)
    active_chat = get_chat(user_id, active_chat_id)

    text = "🗣️ *Nexus Chat Menu*\n\n"
    text += f"*Active Chat:* {active_chat_title}\n"
    text += f"{_chat_context_summary(active_chat)}\n"
    text += "_(All conversational history is saved under this thread)_"
    
    await ctx.reply_text(
        text=text,
        buttons=_build_main_menu_keyboard(active_chat_id)
    )

async def chat_callback_handler(ctx: InteractiveContext):
    """Handles inline keyboard callbacks for the chat menu."""
    if not ctx.query:
        return
        
    await ctx.answer_callback_query()
    
    user_id = int(ctx.user_id)
    data = ctx.query.action_data
    message_id = ctx.query.message_id
    
    if data == "chat:new":
        chat_id = create_chat(user_id)
        await _render_menu(ctx, user_id, notice="✅ *New Chat Created & Activated!*")
        
    elif data == "chat:list":
        chats = list_chats(user_id)
        active_chat_id = get_active_chat(user_id)
        
        if not chats:
            await ctx.edit_message_text(message_id=message_id, text="You have no saved chats.")
            return
            
        text = "📋 *Select a Chat Thread:*"
        keyboard = []
        for c in chats:
            chat_id = c.get("id")
            title = c.get("title")
            prefix = "✅ " if chat_id == active_chat_id else ""
            keyboard.append([Button(f"{prefix}{title}", callback_data=f"chat:select:{chat_id}")])
            
        keyboard.append([Button("🔙 Back to Menu", callback_data="chat:menu")])
        await ctx.edit_message_text(message_id=message_id, text=text, buttons=keyboard)
        
    elif data.startswith("chat:delete:"):
        chat_id = data.split(":")[2]
        delete_chat(user_id, chat_id)
        await _render_menu(ctx, user_id, notice="🗑️ *Chat Deleted!*")
        
    elif data.startswith("chat:select:"):
        chat_id = data.split(":")[2]
        set_active_chat(user_id, chat_id)
        await _render_menu(ctx, user_id, notice="✅ *Switched Active Chat!*")

    elif data == "chat:rename":
        ctx.user_state["pending_chat_rename"] = True
        await ctx.edit_message_text(
            message_id=message_id,
            text=(
                "✏️ *Rename Active Chat*\n\n"
                "Send the new chat name as a message.\n"
                "Or tap cancel below."
            ),
            buttons=[
                [Button("❌ Cancel", callback_data="chat:rename:cancel")],
                [Button("🔙 Back to Menu", callback_data="chat:menu")],
            ]
        )

    elif data == "chat:rename:cancel":
        ctx.user_state.pop("pending_chat_rename", None)
        await _render_menu(ctx, user_id, notice="❎ *Rename canceled.*")

    elif data == "chat:context":
        await _render_context_menu(ctx, user_id)

    elif data == "chat:ctx:project":
        await ctx.edit_message_text(
            message_id=message_id,
            text="📁 *Select project for active chat:*",
            buttons=_project_picker_keyboard(),
        )

    elif data == "chat:ctx:mode":
        await ctx.edit_message_text(
            message_id=message_id,
            text="🧭 *Select mode for active chat:*",
            buttons=_mode_picker_keyboard(),
        )

    elif data == "chat:ctx:agent":
        active_chat_id = get_active_chat(user_id)
        active_chat = get_chat(user_id, active_chat_id)
        await ctx.edit_message_text(
            message_id=message_id,
            text="🤖 *Select primary agent type for active chat:*",
            buttons=_agent_picker_keyboard(active_chat),
        )

    elif data.startswith("chat:ctx:setproject:"):
        project_key = data.split(":", 3)[3]
        active_chat_id = get_active_chat(user_id)
        if project_key not in PROJECTS:
            await _render_context_menu(ctx, user_id, notice="⚠️ Invalid project.")
            return
        project_agent_types = get_chat_agent_types(project_key)
        primary_agent_type = project_agent_types[0] if project_agent_types else "triage"
        update_chat_metadata(
            user_id,
            active_chat_id,
            {
                "project_key": project_key,
                "allowed_agent_types": project_agent_types,
                "primary_agent_type": primary_agent_type,
            },
        )
        await _render_context_menu(
            ctx,
            user_id,
            notice=(
                f"✅ Project set to *{PROJECTS[project_key]}*.\n"
                f"✅ Primary agent reloaded to *{_agent_type_label(primary_agent_type)}* (`{primary_agent_type}`)."
            ),
        )

    elif data.startswith("chat:ctx:setmode:"):
        mode = data.split(":", 3)[3]
        active_chat_id = get_active_chat(user_id)
        if mode not in CHAT_MODES:
            await _render_context_menu(ctx, user_id, notice="⚠️ Invalid mode.")
            return
        update_chat_metadata(user_id, active_chat_id, {"chat_mode": mode})
        await _render_context_menu(ctx, user_id, notice=f"✅ Mode set to *{CHAT_MODES[mode]}*.")

    elif data.startswith("chat:ctx:setagent:"):
        agent_type = data.split(":", 3)[3]
        active_chat_id = get_active_chat(user_id)
        active_chat = get_chat(user_id, active_chat_id)
        if agent_type not in _available_primary_agent_types(active_chat):
            await _render_context_menu(ctx, user_id, notice="⚠️ Invalid primary agent.")
            return
        update_chat_metadata(user_id, active_chat_id, {"primary_agent_type": agent_type})
        await _render_context_menu(
            ctx,
            user_id,
            notice=f"✅ Primary agent set to *{_agent_type_label(agent_type)}* (`{agent_type}`).",
        )
        
    elif data == "chat:menu":
        ctx.user_state.pop("pending_chat_rename", None)
        await _render_menu(ctx, user_id)


async def chat_agents_handler(ctx: InteractiveContext):
    """Show effective ordered chat agent types for a project.

    Usage:
    - /chatagents              -> uses active chat project (or nexus fallback)
    - /chatagents <project>    -> explicit project
    """
    user_id = int(ctx.user_id)
    active_chat = get_chat(user_id, get_active_chat(user_id))
    metadata = (active_chat or {}).get("metadata") or {}

    if ctx.args:
        project_key = str(ctx.args[0]).strip().lower()
    else:
        project_key = str(metadata.get("project_key") or "").strip().lower()

    if not project_key:
        project_key = "nexus"

    if project_key not in PROJECTS and project_key != "nexus":
        available = ", ".join(sorted(PROJECTS.keys()))
        await ctx.reply_text(
            f"⚠️ Unknown project `{project_key}`.\n\nAvailable: {available}"
        )
        return

    effective_types = get_chat_agent_types(project_key)
    if not effective_types:
        await ctx.reply_text(
            f"⚠️ No chat agent types configured for `{project_key}`."
        )
        return

    lines = [f"🤖 *Chat Agents for {PROJECTS.get(project_key, project_key)}*", ""]
    for index, agent_type in enumerate(effective_types, start=1):
        marker = " *(primary)*" if index == 1 else ""
        lines.append(f"{index}. {_agent_type_label(agent_type)} (`{agent_type}`){marker}")

    await ctx.reply_text("\n".join(lines))
