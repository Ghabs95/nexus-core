from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedDiscordTextCommand:
    name: str
    args: list[str]


def parse_discord_text_command(
    text: str,
    *,
    bot_username: str | None = None,
    prefixes: tuple[str, ...] = ("/",),
) -> ParsedDiscordTextCommand | None:
    raw = str(text or "").strip()
    if not raw:
        return None

    prefix = next((item for item in prefixes if raw.startswith(item)), None)
    if not prefix:
        return None

    candidate = raw[len(prefix) :].strip()
    if not candidate:
        return None

    try:
        tokens = shlex.split(candidate)
    except ValueError:
        tokens = candidate.split()
    if not tokens:
        return None

    command_token = str(tokens[0] or "").strip()
    if "@" in command_token:
        command_base, _, mention = command_token.partition("@")
        mention_value = mention.strip().lower()
        if bot_username and mention_value and mention_value != str(bot_username).strip().lower():
            return None
        command_token = command_base

    command_name = command_token.replace("-", "_").strip().lower()
    if not command_name:
        return None

    return ParsedDiscordTextCommand(name=command_name, args=[str(item) for item in tokens[1:]])
