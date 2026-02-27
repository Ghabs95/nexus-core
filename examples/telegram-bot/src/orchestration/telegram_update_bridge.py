"""Telegram update/context adapter bridge extracted from telegram_bot."""

import logging
import re
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)
TELEGRAM_TEXT_LIMIT = 4096


def buttons_to_reply_markup(buttons, inline_keyboard_button_cls, inline_keyboard_markup_cls):
    """Convert abstract buttons into Telegram InlineKeyboardMarkup."""
    if not buttons:
        return None

    keyboard = []
    for row in buttons:
        keyboard_row = []
        for btn in row:
            label = getattr(btn, "label", "")
            callback_data = getattr(btn, "callback_data", None)
            url = getattr(btn, "url", None)
            if url:
                keyboard_row.append(inline_keyboard_button_cls(label, url=url))
            else:
                keyboard_row.append(
                    inline_keyboard_button_cls(label, callback_data=callback_data or "")
                )
        if keyboard_row:
            keyboard.append(keyboard_row)
    return inline_keyboard_markup_cls(keyboard) if keyboard else None


def _normalize_telegram_markdown(text: str, parse_mode: str | None) -> str:
    """Normalize common GFM markdown into Telegram legacy Markdown."""
    if parse_mode != "Markdown" or not text:
        return text

    def _is_table_separator(line: str) -> bool:
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if not parts:
            return False
        return all(part and re.fullmatch(r"[:\- ]+", part) for part in parts)

    def _parse_table_row(line: str) -> list[str]:
        return [part.strip() for part in line.strip().strip("|").split("|")]

    def _table_to_text(block: list[str]) -> list[str]:
        if len(block) < 2:
            return block
        headers = _parse_table_row(block[0])
        rows = [_parse_table_row(row) for row in block[2:]]
        if not headers or not rows:
            return block
        converted: list[str] = []
        for row in rows:
            pairs = []
            for idx, value in enumerate(row):
                header = headers[idx] if idx < len(headers) else f"col{idx + 1}"
                if header and value:
                    pairs.append(f"{header}: {value}")
                elif value:
                    pairs.append(value)
            if pairs:
                converted.append(f"- {' | '.join(pairs)}")
        return converted or block

    text = text.replace("\r\n", "\n")
    text = re.sub(r"```([a-zA-Z0-9_+-]+)\n", "```\n", text)

    out_lines: list[str] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if i + 1 < len(lines) and "|" in line and _is_table_separator(lines[i + 1]):
            table_block = [line, lines[i + 1]]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                table_block.append(lines[i])
                i += 1
            out_lines.extend(_table_to_text(table_block))
            continue

        heading_match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if heading_match:
            out_lines.append(f"*{heading_match.group(1)}*")
            i += 1
            continue

        if re.match(r"^\s*[-*_]{3,}\s*$", stripped):
            i += 1
            continue

        out_lines.append(line)
        i += 1

    normalized = "\n".join(out_lines)
    normalized = re.sub(r"\*\*(.+?)\*\*", r"*\1*", normalized)
    normalized = re.sub(r"__(.+?)__", r"*\1*", normalized)
    return normalized


def _clip_telegram_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> str:
    """Ensure outgoing Telegram text respects message length limits."""
    if not text:
        return text
    if len(text) <= limit:
        return text
    suffix = "\n\n[truncated]"
    budget = max(0, limit - len(suffix))
    clipped = text[:budget].rstrip()
    logger.warning(
        "Clipping Telegram message from %d to %d chars",
        len(text),
        len(clipped) + len(suffix),
    )
    return f"{clipped}{suffix}"


def _is_parse_entity_error(exc: Exception) -> bool:
    return "Can't parse entities" in str(exc)


async def _reply_with_parse_fallback(
    effective_message: Any,
    text: str,
    *,
    reply_markup: Any,
    parse_mode: str | None,
    disable_web_page_preview: bool,
):
    normalized_text = _clip_telegram_text(_normalize_telegram_markdown(text, parse_mode))
    try:
        return await effective_message.reply_text(
            normalized_text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
    except Exception as exc:
        if parse_mode and _is_parse_entity_error(exc):
            logger.warning("Retrying reply_text without parse_mode due to Telegram entity parsing")
            return await effective_message.reply_text(
                normalized_text,
                reply_markup=reply_markup,
                parse_mode=None,
                disable_web_page_preview=disable_web_page_preview,
            )
        raise


async def _edit_with_parse_fallback(callable_edit, *, text: str, reply_markup: Any, parse_mode: str | None, disable_web_page_preview: bool):
    normalized_text = _clip_telegram_text(_normalize_telegram_markdown(text, parse_mode))
    try:
        return await callable_edit(
            text=normalized_text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
    except Exception as exc:
        if parse_mode and _is_parse_entity_error(exc):
            logger.warning("Retrying edit_message_text without parse_mode due to Telegram entity parsing")
            return await callable_edit(
                text=normalized_text,
                reply_markup=reply_markup,
                parse_mode=None,
                disable_web_page_preview=disable_web_page_preview,
            )
        raise


def build_telegram_interactive_ctx(
    update: Any,
    context: Any,
    *,
    buttons_to_reply_markup_fn,
):
    """Build the interactive context wrapper used by core Telegram handlers."""
    query_obj = update.callback_query
    effective_message = update.effective_message

    class _TelegramInteractiveCtx:
        def __init__(self):
            self.user_id = str(getattr(getattr(update, "effective_user", None), "id", ""))
            self.chat_id = int(getattr(getattr(update, "effective_chat", None), "id", 0) or 0)
            self.text = str(getattr(effective_message, "text", "") or "")
            self.args = list(getattr(context, "args", []) or [])
            self.raw_event = update
            self.telegram_context = context
            self.user_state = getattr(context, "user_data", {})
            self.client = SimpleNamespace(name="telegram")
            self.query = (
                SimpleNamespace(
                    data=str(getattr(query_obj, "data", "") or ""),
                    action_data=str(getattr(query_obj, "data", "") or ""),
                    message_id=str(getattr(getattr(query_obj, "message", None), "message_id", "")),
                )
                if query_obj is not None
                else None
            )

        async def reply_text(
            self,
            text: str,
            buttons=None,
            parse_mode: str | None = "Markdown",
            disable_web_page_preview: bool = True,
        ) -> str:
            reply_markup = buttons_to_reply_markup_fn(buttons)
            msg = await _reply_with_parse_fallback(
                effective_message,
                text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )
            return str(getattr(msg, "message_id", ""))

        async def edit_message_text(
            self,
            text: str,
            message_id: str | None = None,
            buttons=None,
            parse_mode: str | None = "Markdown",
            disable_web_page_preview: bool = True,
        ) -> None:
            reply_markup = buttons_to_reply_markup_fn(buttons)
            if query_obj is not None and hasattr(query_obj, "edit_message_text"):
                await _edit_with_parse_fallback(
                    query_obj.edit_message_text,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_web_page_preview,
                )
                return

            target_message_id = message_id
            if target_message_id is None and effective_message is not None:
                target_message_id = str(getattr(effective_message, "message_id", ""))
            if isinstance(target_message_id, str) and target_message_id.isdigit():
                target_message_id = int(target_message_id)

            async def _edit_via_bot(**kwargs):
                return await context.bot.edit_message_text(
                    chat_id=getattr(getattr(update, "effective_chat", None), "id", None),
                    message_id=target_message_id,
                    **kwargs,
                )

            await _edit_with_parse_fallback(
                _edit_via_bot,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )

        async def answer_callback_query(self, text: str | None = None) -> None:
            if query_obj is not None and hasattr(query_obj, "answer"):
                await query_obj.answer(text=text)

    return _TelegramInteractiveCtx()
