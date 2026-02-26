import types

import pytest
from orchestration.telegram_update_bridge import (
    build_telegram_interactive_ctx,
    buttons_to_reply_markup,
)


def test_buttons_to_reply_markup_builds_keyboard():
    class Btn:
        def __init__(self, label, callback_data=None, url=None):
            self.label = label
            self.callback_data = callback_data
            self.url = url

    class InlineBtn:
        def __init__(self, label, callback_data=None, url=None):
            self.label = label
            self.callback_data = callback_data
            self.url = url

    class Markup:
        def __init__(self, keyboard):
            self.keyboard = keyboard

    markup = buttons_to_reply_markup(
        [[Btn("A", callback_data="x"), Btn("B", url="https://x")]],
        InlineBtn,
        Markup,
    )
    assert isinstance(markup, Markup)
    assert markup.keyboard[0][0].callback_data == "x"
    assert markup.keyboard[0][1].url == "https://x"


@pytest.mark.asyncio
async def test_build_telegram_interactive_ctx_reply_text_uses_message():
    captured = {}

    class Msg:
        text = "hello"
        message_id = 99

        async def reply_text(self, text, **kwargs):
            captured["text"] = text
            captured["kwargs"] = kwargs
            return types.SimpleNamespace(message_id=123)

    update = types.SimpleNamespace(
        callback_query=None,
        effective_message=Msg(),
        effective_user=types.SimpleNamespace(id=7),
        effective_chat=types.SimpleNamespace(id=8),
    )
    context = types.SimpleNamespace(args=["a"], user_data={}, bot=types.SimpleNamespace())

    ctx = build_telegram_interactive_ctx(
        update,
        context,
        buttons_to_reply_markup_fn=lambda buttons: "markup",
    )
    message_id = await ctx.reply_text("hi", buttons=[["ignored"]], parse_mode=None)
    assert message_id == "123"
    assert captured["text"] == "hi"
    assert captured["kwargs"]["reply_markup"] == "markup"
