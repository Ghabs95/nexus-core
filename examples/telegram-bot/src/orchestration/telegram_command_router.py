"""Telegram command dispatch helpers extracted from telegram_bot."""

from collections.abc import Awaitable, Callable
from typing import Any


async def dispatch_command(
    *,
    update: Any,
    context: Any,
    command: str,
    project_key: str,
    issue_num: str,
    rest: list[str] | None,
    command_handler_map: Callable[[], dict[str, Callable[..., Awaitable[None]]]],
    reply_unsupported: Callable[[Any], Awaitable[None]],
) -> None:
    """Dispatch command to Telegram handlers while normalizing args."""
    project_only_commands = {"agents", "feature_done", "feature_list", "feature_forget"}
    if command in project_only_commands:
        context.args = [project_key] + (rest or [])
    else:
        context.args = [project_key, issue_num] + (rest or [])

    handler = command_handler_map().get(command)
    if handler:
        await handler(update, context)
        return
    await reply_unsupported(update)
