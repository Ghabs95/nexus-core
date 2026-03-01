from typing import Any


def build_menu_keyboard(
    *,
    button_rows,
    inline_keyboard_button_cls,
    inline_keyboard_markup_cls,
    include_back: bool = True,
):
    keyboard = button_rows[:]
    if include_back:
        keyboard.append([inline_keyboard_button_cls("⬅️ Back", callback_data="menu:root")])
    keyboard.append([inline_keyboard_button_cls("❌ Close", callback_data="menu:close")])
    return inline_keyboard_markup_cls(keyboard)


def build_help_text() -> str:
    return (
        "🤖 **Nexus Commands**\n\n"
        "Use /menu for a categorized, button-driven view.\n\n"
        "🗣️ **Chat & Strategy:**\n"
        "/rename <name> - Rename the active chat\n"
        "/chat - Open chat threads and context controls\n\n"
        "/chatagents [project] - Show effective ordered chat agent types (first is primary)\n\n"
        "✨ **Task Creation:**\n"
        "/menu - Open command menu\n"
        "/new - Start a menu-driven task creation\n"
        "/cancel - Abort the current guided process\n\n"
        "⚡ **Hands-Free Mode:**\n"
        "Send a **Voice Note** or **Text Message** directly. "
        "The bot will transcribe, route, and save the task.\n"
        "Task safety guard: confirmation may be required before creation (mode: off|smart|always via `TASK_CONFIRMATION_MODE`).\n\n"
        "📋 **Workflow Tiers:**\n"
        "• 🔥 Hotfix/Chore → fast-track (triage → implement → verify → deploy)\n"
        "• 🩹 Bug → shortened (triage → debug → fix → verify → deploy → close)\n"
        "• ✨ Feature → full (triage → design → develop → review → compliance → deploy → close)\n"
        "• ✨ Simple Feature → fast-track (skip design)\n\n"
        "📊 **Monitoring & Tracking:**\n"
        "/status [project|all] - View pending tasks in inbox\n"
        "/inboxq [limit] - Inspect inbox queue status (postgres mode)\n"
        "/active [project|all] [cleanup] - View tasks currently being worked on\n"
        "/track <project> <issue#> - Track issue per-project\n"
        "/tracked - View active globally tracked issues\n"
        "/untrack <project> <issue#> - Stop tracking per-project\n"
        "/myissues - View all your tracked issues\n"
        "/logs <project> <issue#> - View task logs\n"
        "/logsfull <project> <issue#> - Full log lines (no truncation)\n"
        "/tail <project> <issue#> [lines] [seconds] - Follow live log tail\n"
        "/tailstop - Stop current live tail session\n"
        "/fuse <project> <issue#> - View retry fuse state\n"
        "/audit <project> <issue#> - View workflow audit trail\n"
        "/stats [days] - View system analytics (default: 30 days)\n"
        "/comments <project> <issue#> - View issue comments\n\n"
        "🔁 **Recovery & Control:**\n"
        "/reprocess <project> <issue#> - Re-run agent processing\n"
        "/wfstate <project> <issue#> - Show workflow state and drift snapshot\n"
        "/visualize <project> <issue#> - Show Mermaid workflow diagram for an issue\n"
        "/watch <project> <issue#> - Stream live workflow updates in chat\n"
        "/reconcile <project> <issue#> - Reconcile workflow/comment/local state\n"
        "/continue <project> <issue#> - Check stuck agent status\n"
        "/forget <project> <issue#> - Permanently clear local state for an issue\n"
        "/kill <project> <issue#> - Stop running agent process\n"
        "/pause <project> <issue#> - Pause auto-chaining (agents work but no auto-launch)\n"
        "/resume <project> <issue#> - Resume auto-chaining\n"
        "/stop <project> <issue#> - Stop workflow completely (closes issue, kills agent)\n"
        "/respond <project> <issue#> <text> - Respond to agent questions\n\n"
        "🤝 **Agent Management:**\n"
        "/agents <project> - List all agents for a project\n"
        "/direct <project> <@agent> <message> - Send direct request to an agent\n"
        "/direct <project> <@agent> --new-chat <message> - Strategic direct reply in a new chat thread\n\n"
        "🧾 **Feature Registry:**\n"
        "/feature_done <project> <title> - Mark a feature as implemented\n"
        "/feature_list <project> - List implemented features for dedup\n"
        "/feature_forget <project> <feature_id|title> - Remove an implemented feature\n\n"
        "🔧 **Git Platform Management:**\n"
        "/assign <project> <issue#> - Assign issue to yourself\n"
        "/implement <project> <issue#> - Request Copilot agent implementation\n"
        "/prepare <project> <issue#> - Add Copilot-friendly instructions\n\n"
        "ℹ️ /help - Show this list"
    )


async def handle_help(*, update, logger, allowed_user_ids) -> None:
    logger.info("Help triggered by user: %s", update.effective_user.id)
    if allowed_user_ids and update.effective_user.id not in allowed_user_ids:
        logger.warning("Unauthorized access attempt by ID: %s", update.effective_user.id)
        return
    await update.message.reply_text(build_help_text(), parse_mode="Markdown")


async def handle_menu(
    *, update, logger, allowed_user_ids, inline_keyboard_button_cls, inline_keyboard_markup_cls
) -> None:
    if allowed_user_ids and update.effective_user.id not in allowed_user_ids:
        logger.warning("Unauthorized access attempt by ID: %s", update.effective_user.id)
        return
    keyboard = [
        [inline_keyboard_button_cls("🗣️ Chat", callback_data="menu:chat")],
        [inline_keyboard_button_cls("✨ Task Creation", callback_data="menu:tasks")],
        [inline_keyboard_button_cls("📊 Monitoring", callback_data="menu:monitor")],
        [inline_keyboard_button_cls("🔁 Workflow Control", callback_data="menu:workflow")],
        [inline_keyboard_button_cls("🤝 Agents", callback_data="menu:agents")],
        [inline_keyboard_button_cls("🔧 Git Platform", callback_data="menu:github")],
        [inline_keyboard_button_cls("ℹ️ Help", callback_data="menu:help")],
        [inline_keyboard_button_cls("❌ Close", callback_data="menu:close")],
    ]
    await update.effective_message.reply_text(
        "📍 **Nexus Menu**\nChoose a category:",
        reply_markup=inline_keyboard_markup_cls(keyboard),
        parse_mode="Markdown",
    )


async def handle_start(
    *,
    update,
    logger,
    allowed_user_ids,
    reply_keyboard_markup_cls,
) -> None:
    logger.info("Start triggered by user: %s", update.effective_user.id)
    if allowed_user_ids and update.effective_user.id not in allowed_user_ids:
        logger.warning("Unauthorized access attempt by ID: %s", update.effective_user.id)
        return
    welcome = (
        "👋 Welcome to Nexus!\n\n"
        "Use the menu buttons to create tasks or monitor queues.\n"
        "Use /chat for project-scoped conversational threads.\n"
        "Send voice or text to create a task automatically.\n\n"
        "💡 **Workflow Tiers:**\n"
        "• 🔥 Hotfix/Chore/Simple Feature → 4 steps (fast)\n"
        "• 🩹 Bug → 6 steps (moderate)\n"
        "• ✨ Feature/Improvement → 9 steps (full)\n\n"
        "Type /help for all commands."
    )
    keyboard = [["/menu"], ["/chat"], ["/new"], ["/status"], ["/active"], ["/help"]]
    reply_markup = reply_keyboard_markup_cls(
        keyboard, resize_keyboard=True, one_time_keyboard=False
    )
    await update.message.reply_text(welcome, reply_markup=reply_markup)


def build_startup_commands(*, bot_command_cls) -> list[Any]:
    return [
        bot_command_cls("menu", "Open command menu"),
        bot_command_cls("chat", "Open chat menu"),
        bot_command_cls("new", "Start task creation"),
        bot_command_cls("status", "Show pending tasks"),
        bot_command_cls("active", "Show active tasks"),
        bot_command_cls("help", "Show help"),
    ]


async def check_tool_health(
    *,
    application,
    orchestrator,
    ai_providers: list[Any],
    logger,
    telegram_chat_id,
) -> None:
    unavailable: list[str] = []
    checked: list[str] = []
    for tool in ai_providers:
        checked.append(str(getattr(tool, "value", tool)))
        try:
            available = orchestrator.check_tool_available(tool)
            if not available:
                unavailable.append(tool.value)
        except Exception as exc:
            logger.warning("Health check error for %s: %s", tool.value, exc)
            unavailable.append(tool.value)

    if unavailable:
        alert = (
            "⚠️ *Nexus Startup Alert*\n"
            f"The following AI tools are unavailable: `{', '.join(unavailable)}`\n"
            "Agents using these tools will fail until they recover."
        )
        logger.warning("Tool health check failed: %s", unavailable)
        if telegram_chat_id:
            try:
                await application.bot.send_message(
                    chat_id=telegram_chat_id,
                    text=alert,
                    parse_mode="Markdown",
                )
            except Exception as exc:
                logger.warning("Failed to send health alert to Telegram: %s", exc)
    else:
        provider_names = ", ".join(name.upper() for name in checked) if checked else "none"
        logger.info("✅ Tool health check passed: %s available", provider_names)


async def on_startup(
    *,
    application,
    logger,
    validate_required_command_interface,
    validate_command_parity,
    bot_command_cls,
    check_tool_health_fn,
) -> None:
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

    cmds = build_startup_commands(bot_command_cls=bot_command_cls)
    try:
        await application.bot.set_my_commands(cmds)
        logger.info("Registered bot commands for Telegram client menu")
    except Exception:
        logger.exception("Failed to set bot commands on startup")

    await check_tool_health_fn(application)
