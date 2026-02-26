"""Telegram update/context adapter bridge extracted from telegram_bot."""

from types import SimpleNamespace
from typing import Any


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
            msg = await effective_message.reply_text(
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
                await query_obj.edit_message_text(
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_web_page_preview,
                )
                return

            target_message_id = message_id
            if target_message_id is None and effective_message is not None:
                target_message_id = str(getattr(effective_message, "message_id", ""))

            await context.bot.edit_message_text(
                chat_id=getattr(getattr(update, "effective_chat", None), "id", None),
                message_id=target_message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )

        async def answer_callback_query(self, text: str | None = None) -> None:
            if query_obj is not None and hasattr(query_obj, "answer"):
                await query_obj.answer(text=text)

    return _TelegramInteractiveCtx()
