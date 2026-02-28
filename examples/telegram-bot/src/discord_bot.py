import glob
import io
import logging
import os
import sys
from datetime import datetime
from typing import Any

import discord
from discord.ext import commands

from nexus.core.utils.logging_filters import install_secret_redaction

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Add src directories to check for local imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    AI_PERSONA,
    BASE_DIR,
    DISCORD_ALLOWED_USER_IDS,
    DISCORD_GUILD_ID,
    DISCORD_TOKEN,
    ORCHESTRATOR_CONFIG,
    PROJECT_CONFIG,
    get_inbox_dir,
)
from project_key_utils import normalize_project_key_str as _normalize_project_key

install_secret_redaction([DISCORD_TOKEN or ""], logging.getLogger())

from utils.voice_utils import transcribe_audio

from handlers.common_routing import (
    extract_json_dict,
    parse_intent_result,
    route_task_with_context,
    run_conversation_turn,
)
from handlers.feature_ideation_handlers import (
    FeatureIdeationHandlerDeps,
    _build_feature_suggestions,
    detect_feature_project,
    is_feature_ideation_request,
)
from handlers.inbox_routing_handler import PROJECTS as ROUTING_PROJECTS
from handlers.inbox_routing_handler import process_inbox_task, save_resolved_task
from orchestration.ai_orchestrator import get_orchestrator
from services.command_contract import (
    validate_command_parity,
    validate_required_command_interface,
)
from services.memory_service import (
    append_message,
    create_chat,
    delete_chat,
    get_active_chat,
    get_chat,
    get_chat_history,
    list_chats,
    rename_chat,
    switch_chat,
)
from state_manager import HostStateManager
from user_manager import get_user_manager

# --- SETUP BOT ---
intents = discord.Intents.default()
intents.message_content = True  # Required to read text messages
bot = commands.Bot(command_prefix="!", intents=intents)

# Initialize Orchestrator
orchestrator = get_orchestrator(ORCHESTRATOR_CONFIG)
user_manager = get_user_manager()
_pending_project_resolution: dict[int, dict] = {}
_pending_feature_ideation: dict[int, dict] = {}


def _get_project_label(project_key: str) -> str:
    return str(ROUTING_PROJECTS.get(project_key, project_key))


def _feature_ideation_handler_deps() -> FeatureIdeationHandlerDeps:
    return FeatureIdeationHandlerDeps(
        logger=logger,
        allowed_user_ids=DISCORD_ALLOWED_USER_IDS,
        projects=ROUTING_PROJECTS,
        get_project_label=_get_project_label,
        orchestrator=orchestrator,
        base_dir=BASE_DIR,
        project_config=PROJECT_CONFIG,
    )


def _clamp_feature_count(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 3
    return max(1, min(5, parsed))


def _build_feature_task_text(project_key: str, selected: dict[str, Any]) -> str:
    lines = [
        f"New feature proposal for {_get_project_label(project_key)}",
        "",
        f"Title: {selected.get('title', '')}",
        f"Summary: {selected.get('summary', '')}",
        f"Why now: {selected.get('why', '')}",
        "",
        "Implementation outline:",
    ]
    steps = selected.get("steps") if isinstance(selected.get("steps"), list) else []
    if steps:
        for index, step in enumerate(steps, start=1):
            lines.append(f"{index}. {step}")
    else:
        lines.extend(
            [
                "1. Define technical approach",
                "2. Implement core changes",
                "3. Validate and document",
            ]
        )
    return "\n".join(lines).strip()


def _feature_list_text(project_key: str, features: list[dict[str, Any]], feature_count: int) -> str:
    lines = [
        f"💡 **Feature proposals for {_get_project_label(project_key)}**",
        f"Requested: {feature_count}",
        "",
        "Reply with the feature number to start implementation:",
    ]
    for index, item in enumerate(features, start=1):
        lines.append(f"{index}. **{item['title']}** — {item['summary']}")
    lines.append("")
    lines.append("Type a project key anytime to switch project, or `cancel` to stop.")
    return "\n".join(lines)


def _parse_count_reply(text: str) -> int | None:
    candidate = str(text or "").strip().lower()
    if not candidate:
        return None
    if candidate.isdigit():
        return _clamp_feature_count(candidate)

    words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
    }
    return words.get(candidate)


async def _begin_feature_ideation(message: discord.Message, text: str) -> bool:
    if not is_feature_ideation_request(text):
        return False

    user_id = message.author.id
    active_chat = get_chat(user_id) or {}
    metadata = active_chat.get("metadata") if isinstance(active_chat, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}

    preferred_project_key = metadata.get("project_key")
    project_key = detect_feature_project(text, ROUTING_PROJECTS)
    if not project_key and preferred_project_key in ROUTING_PROJECTS:
        project_key = preferred_project_key

    _pending_feature_ideation[user_id] = {
        "step": "awaiting_count",
        "source_text": text,
        "project": project_key,
        "feature_count": None,
        "items": [],
    }

    project_label = _get_project_label(project_key) if project_key else "not selected"
    await message.channel.send(
        "🔢 How many feature proposals do you want? Reply with a number from 1 to 5.\n\n"
        f"Current project: **{project_label}**"
    )
    return True


async def _handle_pending_feature_ideation(message: discord.Message, text: str) -> bool:
    state = _pending_feature_ideation.get(message.author.id)
    if not isinstance(state, dict):
        return False

    candidate_text = str(text or "").strip()
    candidate_lower = candidate_text.lower()
    if candidate_lower in {"cancel", "/cancel"}:
        _pending_feature_ideation.pop(message.author.id, None)
        await message.channel.send("❎ Feature ideation canceled.")
        return True

    if state.get("step") == "awaiting_count":
        feature_count = _parse_count_reply(candidate_text)
        if feature_count is None:
            await message.channel.send("⚠️ Please reply with a number between 1 and 5.")
            return True

        state["feature_count"] = _clamp_feature_count(feature_count)
        project_key = state.get("project")
        if project_key not in ROUTING_PROJECTS:
            state["step"] = "awaiting_project"
            options = ", ".join(sorted(ROUTING_PROJECTS.keys()))
            await message.channel.send(
                "📁 Great — now choose a project key to continue:\n" f"{options}"
            )
            return True

        deps = _feature_ideation_handler_deps()
        features = _build_feature_suggestions(
            project_key=project_key,
            text=str(state.get("source_text") or ""),
            deps=deps,
            preferred_agent_type=None,
            feature_count=state["feature_count"],
        )
        state["items"] = features
        state["step"] = "awaiting_pick"

        if not features:
            _pending_feature_ideation.pop(message.author.id, None)
            await message.channel.send(
                "⚠️ I couldn't generate feature proposals right now. Please try again."
            )
            return True

        await message.channel.send(
            _feature_list_text(project_key, features, state["feature_count"])
        )
        return True

    if state.get("step") == "awaiting_project":
        project_key = _normalize_project_key(candidate_text)
        if project_key not in ROUTING_PROJECTS:
            options = ", ".join(sorted(ROUTING_PROJECTS.keys()))
            await message.channel.send(f"⚠️ Invalid project key. Choose one of: {options}")
            return True

        state["project"] = project_key
        deps = _feature_ideation_handler_deps()
        features = _build_feature_suggestions(
            project_key=project_key,
            text=str(state.get("source_text") or ""),
            deps=deps,
            preferred_agent_type=None,
            feature_count=_clamp_feature_count(state.get("feature_count")),
        )
        state["items"] = features
        state["step"] = "awaiting_pick"

        if not features:
            _pending_feature_ideation.pop(message.author.id, None)
            await message.channel.send(
                "⚠️ I couldn't generate feature proposals right now. Please try again."
            )
            return True

        await message.channel.send(
            _feature_list_text(
                project_key, features, _clamp_feature_count(state.get("feature_count"))
            )
        )
        return True

    if state.get("step") == "awaiting_pick":
        project_key = state.get("project")
        items = state.get("items") if isinstance(state.get("items"), list) else []
        if project_key not in ROUTING_PROJECTS or not items:
            _pending_feature_ideation.pop(message.author.id, None)
            await message.channel.send("⚠️ Feature session expired. Start a new request.")
            return True

        project_candidate = _normalize_project_key(candidate_text)
        if project_candidate in ROUTING_PROJECTS:
            state["project"] = project_candidate
            deps = _feature_ideation_handler_deps()
            features = _build_feature_suggestions(
                project_key=project_candidate,
                text=str(state.get("source_text") or ""),
                deps=deps,
                preferred_agent_type=None,
                feature_count=_clamp_feature_count(state.get("feature_count")),
            )
            state["items"] = features
            if not features:
                _pending_feature_ideation.pop(message.author.id, None)
                await message.channel.send(
                    "⚠️ I couldn't generate feature proposals right now. Please try again."
                )
                return True
            await message.channel.send(
                _feature_list_text(
                    project_candidate, features, _clamp_feature_count(state.get("feature_count"))
                )
            )
            return True

        if not candidate_text.isdigit():
            await message.channel.send("⚠️ Reply with a feature number to start implementation.")
            return True

        selected_index = int(candidate_text) - 1
        if selected_index < 0 or selected_index >= len(items):
            await message.channel.send(
                "⚠️ Invalid feature selection. Reply with one of the listed numbers."
            )
            return True

        selected = items[selected_index]
        task_text = _build_feature_task_text(project_key, selected)
        result = await process_inbox_task(
            task_text,
            orchestrator,
            str(message.id),
            project_hint=project_key,
        )
        _pending_feature_ideation.pop(message.author.id, None)
        await message.channel.send(str(result.get("message") or "⚠️ Task processing completed."))
        return True

    _pending_feature_ideation.pop(message.author.id, None)
    return False


def check_permission(user_id: int) -> bool:
    """Check if the user is allowed to interact with the bot."""
    if not DISCORD_ALLOWED_USER_IDS:
        return True
    return user_id in DISCORD_ALLOWED_USER_IDS


def _active_status(value: str) -> bool:
    status = str(value or "").strip().lower()
    if not status:
        status = "active"
    return status not in {"done", "closed", "resolved", "completed", "implemented", "rejected"}


def _iter_configured_projects() -> list[str]:
    projects: list[str] = []
    for key, cfg in PROJECT_CONFIG.items():
        if isinstance(cfg, dict) and cfg.get("workspace"):
            projects.append(str(key))
    return projects


def _project_workspace(project_key: str) -> str:
    cfg = PROJECT_CONFIG.get(project_key, {})
    if isinstance(cfg, dict):
        workspace = cfg.get("workspace")
        if isinstance(workspace, str) and workspace.strip():
            return workspace.strip()
    return project_key


# --- DISCORD UI VIEWS (Similar to Telegram Inline Keyboards) ---


class ChatRenameModal(discord.ui.Modal, title="Rename Active Chat"):
    name = discord.ui.TextInput(
        label="New Chat Name",
        placeholder="Enter the new active chat name...",
        min_length=1,
        max_length=100,
    )

    def __init__(self, user_id: int, chat_id: str):
        super().__init__()
        self.user_id = user_id
        self.chat_id = chat_id

    async def on_submit(self, interaction: discord.Interaction):
        new_name = str(self.name.value or "").strip()
        rename_chat(self.user_id, self.chat_id, new_name)
        await send_chat_menu(
            interaction,
            self.user_id,
            notice=f"✅ Active chat renamed to: **{new_name}**",
        )


class ChatRenamePromptView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(
        label="✏️ Open Rename", style=discord.ButtonStyle.primary, custom_id="chat:rename:open"
    )
    async def open_rename_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_permission(interaction.user.id):
            return

        active_chat_id = get_active_chat(interaction.user.id)
        if active_chat_id:
            await interaction.response.send_modal(
                ChatRenameModal(interaction.user.id, active_chat_id)
            )
        else:
            await interaction.response.send_message("No active chat to rename.", ephemeral=True)

    @discord.ui.button(
        label="❌ Cancel", style=discord.ButtonStyle.secondary, custom_id="chat:rename:cancel"
    )
    async def cancel_rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_permission(interaction.user.id):
            return
        await send_chat_menu(interaction, interaction.user.id, notice="❎ Rename canceled.")


class ChatMenuView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="📝 New Chat", style=discord.ButtonStyle.primary, custom_id="chat:new")
    async def new_chat(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_permission(interaction.user.id):
            return

        create_chat(interaction.user.id)
        # Re-render the menu
        await send_chat_menu(interaction, interaction.user.id)

    @discord.ui.button(
        label="📋 Switch Chat", style=discord.ButtonStyle.secondary, custom_id="chat:list"
    )
    async def switch_chat_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_permission(interaction.user.id):
            return

        view = ChatListView(interaction.user.id)
        await interaction.response.edit_message(content="**Select a chat:**", view=view)

    @discord.ui.button(
        label="✏️ Rename", style=discord.ButtonStyle.secondary, custom_id="chat:rename"
    )
    async def rename_current_chat(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not check_permission(interaction.user.id):
            return

        await interaction.response.edit_message(
            content=(
                "✏️ **Rename Active Chat**\n\n"
                "Open rename to enter a new name, or cancel to go back."
            ),
            view=ChatRenamePromptView(interaction.user.id),
        )

    @discord.ui.button(
        label="🗑️ Delete Current", style=discord.ButtonStyle.danger, custom_id="chat:delete"
    )
    async def delete_active_chat(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_permission(interaction.user.id):
            return

        active_chat_id = get_active_chat(interaction.user.id)
        if active_chat_id:
            delete_chat(interaction.user.id, active_chat_id)

        # After deleting, send the main menu which will pick the next active chat or create a default
        await send_chat_menu(interaction, interaction.user.id)


class ChatListView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

        chats = list_chats(user_id)
        active_chat_id = get_active_chat(user_id)

        for c in chats:
            chat_id = c.get("id")
            title = c.get("title", "Chat")

            # Truncate title for button label limits (Discord limit is 80)
            if len(title) > 70:
                title = title[:67] + "..."

            label = f"✅ {title}" if chat_id == active_chat_id else title
            style = (
                discord.ButtonStyle.success
                if chat_id == active_chat_id
                else discord.ButtonStyle.secondary
            )

            # Using dynamic callback creation
            button = discord.ui.Button(label=label, style=style, custom_id=f"switch_chat:{chat_id}")
            button.callback = self.create_switch_callback(chat_id)
            self.add_item(button)

        # Back button
        back_btn = discord.ui.Button(
            label="⬅️ Back", style=discord.ButtonStyle.danger, custom_id="chat:back"
        )
        back_btn.callback = self.back_callback
        self.add_item(back_btn)

    def create_switch_callback(self, chat_id: str):
        async def callback(interaction: discord.Interaction):
            switch_chat(interaction.user.id, chat_id)
            await send_chat_menu(interaction, interaction.user.id)

        return callback

    async def back_callback(self, interaction: discord.Interaction):
        await send_chat_menu(interaction, interaction.user.id)


async def send_chat_menu(interaction: discord.Interaction, user_id: int, notice: str = ""):
    """Helper to send or edit the current message with the main chat menu."""
    active_chat_id = get_active_chat(user_id)
    chats = list_chats(user_id)

    active_chat_title = "Unknown"
    for c in chats:
        if c.get("id") == active_chat_id:
            active_chat_title = c.get("title")
            break

    text = "🗣️ **Nexus Chat Menu**\n\n"
    if notice:
        text += f"{notice}\n"
    text += f"**Active Chat:** {active_chat_title}\n"
    text += "_(All conversational history is saved under this thread)_"

    view = ChatMenuView(user_id)

    # If this is responding to a button click, edit the message
    if interaction.response.is_done():
        await interaction.message.edit(content=text, view=view)
    else:
        await interaction.response.edit_message(content=text, view=view)


# --- SLASH COMMANDS ---


@bot.tree.command(name="chat", description="Manage conversational chat threads")
async def chat_command(interaction: discord.Interaction):
    if not check_permission(interaction.user.id):
        await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
        return

    user_id = interaction.user.id
    active_chat_id = get_active_chat(user_id)
    chats = list_chats(user_id)

    active_chat_title = "Unknown"
    for c in chats:
        if c.get("id") == active_chat_id:
            active_chat_title = c.get("title")
            break

    text = "🗣️ **Nexus Chat Menu**\n\n"
    text += f"**Active Chat:** {active_chat_title}\n"
    text += "_(All conversational history is saved under this thread)_"

    view = ChatMenuView(user_id)
    await interaction.response.send_message(content=text, view=view)


@bot.tree.command(name="track", description="Track an issue globally or for a specific project")
@discord.app_commands.describe(issue="Issue number (e.g., 123)", project="Optional project key")
async def track_command(interaction: discord.Interaction, issue: str, project: str | None = None):
    if not check_permission(interaction.user.id):
        await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
        return

    issue_num = str(issue).lstrip("#").strip()
    if not issue_num.isdigit():
        await interaction.response.send_message("❌ Invalid issue number.", ephemeral=True)
        return

    if project:
        normalized_project = _normalize_project_key(project)
        if normalized_project not in ROUTING_PROJECTS:
            options = ", ".join(sorted(ROUTING_PROJECTS.keys()))
            await interaction.response.send_message(
                f"❌ Invalid project '{project}'. Valid: {options}",
                ephemeral=True,
            )
            return

        user = user_manager.get_or_create_user_by_identity(
            platform="discord",
            platform_user_id=str(interaction.user.id),
            username=interaction.user.name,
            first_name=getattr(interaction.user, "display_name", None),
        )
        user_manager.track_issue_by_nexus_id(
            nexus_id=user.nexus_id,
            project=normalized_project,
            issue_number=issue_num,
        )
        await interaction.response.send_message(
            f"👁️ Now tracking {normalized_project} issue #{issue_num} for you."
        )
        return

    tracked = HostStateManager.load_tracked_issues() or {}
    tracked[str(issue_num)] = {
        "project": "global",
        "status": "active",
        "description": f"Issue #{issue_num}",
        "added_at": datetime.now().isoformat(),
        "last_seen_state": None,
        "last_seen_labels": [],
    }
    HostStateManager.save_tracked_issues(tracked)
    await interaction.response.send_message(f"👁️ Now globally tracking issue #{issue_num}.")


@bot.tree.command(name="tracked", description="Show active globally tracked issues")
async def tracked_command(interaction: discord.Interaction):
    if not check_permission(interaction.user.id):
        await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
        return

    tracked = HostStateManager.load_tracked_issues() or {}
    lines = ["📌 **Global Tracked Issues**", ""]
    active_count = 0
    for issue_num, payload in sorted(
        tracked.items(),
        key=lambda item: int(item[0]) if str(item[0]).isdigit() else 10**9,
    ):
        entry = payload if isinstance(payload, dict) else {}
        status = str(entry.get("status", "active")).strip().lower() or "active"
        if not _active_status(status):
            continue
        project = str(entry.get("project", "global")).strip() or "global"
        lines.append(f"• #{issue_num} ({project}) — {status}")
        active_count += 1

    if active_count == 0:
        await interaction.response.send_message("📌 No active globally tracked issues.")
        return

    lines.append("")
    lines.append(f"**Active:** {active_count}")
    await interaction.response.send_message("\n".join(lines))


@bot.tree.command(name="myissues", description="Show your tracked issues")
async def myissues_command(interaction: discord.Interaction):
    if not check_permission(interaction.user.id):
        await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
        return

    nexus_id = user_manager.resolve_nexus_id("discord", str(interaction.user.id))
    tracked = user_manager.get_user_tracked_issues_by_nexus_id(nexus_id) if nexus_id else {}
    if not tracked:
        await interaction.response.send_message("📋 You're not tracking any project issues yet.")
        return

    lines = ["📋 **Your Tracked Issues**", ""]
    total = 0
    for project, issues in sorted(tracked.items()):
        if not issues:
            continue
        lines.append(f"**{project}**")
        for issue_num in issues:
            lines.append(f"• #{issue_num}")
            total += 1
        lines.append("")
    lines.append(f"**Total:** {total}")
    await interaction.response.send_message("\n".join(lines))


@bot.tree.command(name="status", description="Show pending inbox tasks")
@discord.app_commands.describe(project="Optional project key")
async def status_command(interaction: discord.Interaction, project: str | None = None):
    if not check_permission(interaction.user.id):
        await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
        return

    projects = _iter_configured_projects()
    if project:
        requested = _normalize_project_key(project)
        if requested not in projects:
            options = ", ".join(sorted(projects))
            await interaction.response.send_message(
                f"❌ Invalid project '{project}'. Valid: {options}",
                ephemeral=True,
            )
            return
        projects = [requested]

    lines = ["📊 **Pending Inbox Tasks**", ""]
    total = 0
    for project_key in sorted(projects):
        workspace = _project_workspace(project_key)
        inbox_dir = get_inbox_dir(os.path.join(BASE_DIR, workspace), project_key)
        count = len(glob.glob(os.path.join(inbox_dir, "*.md"))) if os.path.isdir(inbox_dir) else 0
        total += count
        lines.append(f"• {project_key}: {count}")

    lines.append("")
    lines.append(f"**Total Pending:** {total}")
    await interaction.response.send_message("\n".join(lines))


# --- MESSAGE HANDLING ---


@bot.event
async def on_message(message: discord.Message):
    # Ignore bot's own messages
    if message.author == bot.user:
        return

    # Ignore messages not from allowed user
    if not check_permission(message.author.id):
        return

    # Ignore slash commands or other prefix commands
    if message.content.startswith("!") or message.content.startswith("/"):
        return

    text = ""
    status_msg = await message.reply("⚡ Processing...")

    # Check for voice attachments (Discord native voice messages are just .ogg attachments)
    if message.attachments:
        attachment = message.attachments[0]
        if attachment.content_type and "audio/ogg" in attachment.content_type:
            logger.info("Processing voice message...")

            # Download audio to a BytesIO object
            audio_data = io.BytesIO()
            await attachment.save(audio_data)
            audio_data.seek(0)

            # Since our transcribe_audio expects a path, write to a temp file
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".oga", delete=False) as tmp:
                tmp.write(audio_data.read())
                tmp_path = tmp.name

            try:
                # Transcribe
                text = transcribe_audio(tmp_path)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

            if not text:
                logger.warning("Voice transcription returned empty text")
                await status_msg.edit(content="⚠️ Transcription failed")
                return

    # If no voice, or in addition to voice, use message text
    if not text:
        text = message.content

    if not text:
        await status_msg.edit(content="I didn't understand that.")
        return

    if await _handle_pending_feature_ideation(message, text):
        await status_msg.delete()
        return

    if await _begin_feature_ideation(message, text):
        await status_msg.delete()
        return

    pending_resolution = _pending_project_resolution.get(message.author.id)
    if isinstance(pending_resolution, dict):
        candidate = _normalize_project_key(text)

        if candidate in {"cancel", "/cancel"}:
            _pending_project_resolution.pop(message.author.id, None)
            await status_msg.edit(content="❎ Pending project resolution canceled.")
            return

        if candidate in ROUTING_PROJECTS:
            result = await save_resolved_task(pending_resolution, candidate, str(message.id))
            _pending_project_resolution.pop(message.author.id, None)
            await status_msg.edit(content=result.get("message", "✅ Task routed."))
            return

        options = ", ".join(sorted(ROUTING_PROJECTS.keys()))
        await status_msg.edit(
            content=(
                "⚠️ Pending task needs a project key. "
                f"Reply with one of: {options} (or type `cancel`)."
            )
        )
        return

    logger.info(f"Detecting intent for: {text[:50]}...")
    intent_result = parse_intent_result(orchestrator, text, extract_json_dict)
    intent = intent_result.get("intent", "task")

    if intent == "conversation":
        user_id = message.author.id
        await status_msg.edit(content="🤖 **Nexus:** Thinking...")

        reply_text = run_conversation_turn(
            user_id=user_id,
            text=text,
            orchestrator=orchestrator,
            get_chat_history=get_chat_history,
            append_message=append_message,
            persona=AI_PERSONA,
            project_name=((get_chat(user_id) or {}).get("metadata", {}) or {}).get("project_key"),
        )

        await status_msg.edit(content=f"🤖 **Nexus**: \n\n{reply_text}")
        return

    # If it's a task, route through the shared inbox_routing_handler
    result = await route_task_with_context(
        user_id=message.author.id,
        text=text,
        orchestrator=orchestrator,
        message_id=str(message.id),
        get_chat=get_chat,
        process_inbox_task=process_inbox_task,
    )

    # Store pending_resolution state if manual project selection is needed
    if not result["success"] and "pending_resolution" in result:
        _pending_project_resolution[message.author.id] = result["pending_resolution"]
        logger.warning(f"Task needs manual project resolution: {result['pending_resolution']}")

    await status_msg.edit(content=result["message"])


@bot.event
async def on_ready():
    logger.info(f"Discord bot connected as {bot.user}")

    try:
        validate_required_command_interface()
        parity = validate_command_parity()
        telegram_only = sorted(parity.get("telegram_only", set()))
        discord_only = sorted(parity.get("discord_only", set()))
        if telegram_only or discord_only:
            logger.warning(
                "Command parity drift detected: telegram_only=%s discord_only=%s",
                telegram_only,
                discord_only,
            )
    except Exception:
        logger.exception("Command parity strict check failed")
        raise

    # Sync slash commands
    if DISCORD_GUILD_ID:
        guild = discord.Object(id=DISCORD_GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        logger.info(f"Synced slash commands to guild {DISCORD_GUILD_ID}")
    else:
        await bot.tree.sync()
        logger.info("Synced slash commands globally")


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN environment variable not set.")
        sys.exit(1)

    bot.run(DISCORD_TOKEN)
