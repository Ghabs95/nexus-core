from typing import Any, Callable


async def prompt_issue_selection(
    *,
    update: Any,
    command: str,
    project_key: str,
    list_project_issues: Callable[..., list[dict]],
    get_project_label: Callable[[str], str],
    inline_keyboard_button_cls: Any,
    inline_keyboard_markup_cls: Any,
    edit_message: bool = False,
    issue_state: str = "open",
) -> None:
    """Show a list of issues for the user to pick from."""
    issues = list_project_issues(project_key, state=issue_state)
    state_label = "open" if issue_state == "open" else "closed"

    if not issues:
        keyboard = []
        if issue_state == "open":
            keyboard.append(
                [
                    inline_keyboard_button_cls(
                        "📦 Closed issues",
                        callback_data=f"pickissue_state:closed:{command}:{project_key}",
                    )
                ]
            )
        else:
            keyboard.append(
                [
                    inline_keyboard_button_cls(
                        "🔓 Open issues",
                        callback_data=f"pickissue_state:open:{command}:{project_key}",
                    )
                ]
            )
        keyboard.append(
            [
                inline_keyboard_button_cls(
                    "✏️ Enter manually", callback_data=f"pickissue_manual:{command}:{project_key}"
                )
            ]
        )
        keyboard.append([inline_keyboard_button_cls("❌ Close", callback_data="flow:close")])

        text = f"No {state_label} issues found for {get_project_label(project_key)}."
        if edit_message and getattr(update, "callback_query", None):
            await update.callback_query.edit_message_text(
                text, reply_markup=inline_keyboard_markup_cls(keyboard)
            )
        else:
            await update.effective_message.reply_text(
                text, reply_markup=inline_keyboard_markup_cls(keyboard)
            )
        return

    keyboard = []
    for issue in issues:
        num = issue["number"]
        title = issue["title"]
        label = f"#{num} — {title}"
        if len(label) > 60:
            label = label[:57] + "..."
        keyboard.append(
            [
                inline_keyboard_button_cls(
                    label, callback_data=f"pickissue:{command}:{project_key}:{num}"
                )
            ]
        )

    if issue_state == "open":
        keyboard.append(
            [
                inline_keyboard_button_cls(
                    "📦 Closed issues",
                    callback_data=f"pickissue_state:closed:{command}:{project_key}",
                )
            ]
        )
    else:
        keyboard.append(
            [
                inline_keyboard_button_cls(
                    "🔓 Open issues",
                    callback_data=f"pickissue_state:open:{command}:{project_key}",
                )
            ]
        )

    keyboard.append(
        [
            inline_keyboard_button_cls(
                "✏️ Enter manually", callback_data=f"pickissue_manual:{command}:{project_key}"
            )
        ]
    )
    keyboard.append([inline_keyboard_button_cls("❌ Close", callback_data="flow:close")])

    emoji = "📋" if issue_state == "open" else "📦"
    text = f"{emoji} {state_label.capitalize()} issues for /{command} ({get_project_label(project_key)}):"
    if edit_message and getattr(update, "callback_query", None):
        await update.callback_query.edit_message_text(
            text, reply_markup=inline_keyboard_markup_cls(keyboard)
        )
    else:
        await update.effective_message.reply_text(
            text, reply_markup=inline_keyboard_markup_cls(keyboard)
        )


async def prompt_project_selection(
    *,
    update: Any,
    context: Any,
    command: str,
    get_single_project_key: Callable[[], str | None],
    dispatch_command: Callable[[Any, Any, str, str, str], Any],
    prompt_issue_selection: Callable[[Any, Any, str, str], Any],
    iter_project_keys: Callable[[], list[str] | tuple[str, ...] | Any],
    get_project_label: Callable[[str], str],
    inline_keyboard_button_cls: Any,
    inline_keyboard_markup_cls: Any,
) -> None:
    single_project = get_single_project_key()
    if single_project:
        context.user_data["pending_command"] = command
        context.user_data["pending_project"] = single_project
        if command == "agents":
            await dispatch_command(update, context, command, single_project, "")
            return
        await prompt_issue_selection(update, context, command, single_project)
        return

    keyboard = [
        [
            inline_keyboard_button_cls(
                get_project_label(key), callback_data=f"pickcmd:{command}:{key}"
            )
        ]
        for key in iter_project_keys()
    ]
    keyboard.append([inline_keyboard_button_cls("❌ Close", callback_data="flow:close")])
    await update.effective_message.reply_text(
        f"Select a project for /{command}:", reply_markup=inline_keyboard_markup_cls(keyboard)
    )
    context.user_data["pending_command"] = command
