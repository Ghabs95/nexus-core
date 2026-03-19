import types

import pytest

from nexus.core.orchestration.telegram.telegram_update_bridge import (
    _clip_telegram_text,
    _edit_with_parse_fallback,
    _is_message_not_modified_error,
    _normalize_telegram_markdown,
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


@pytest.mark.asyncio
async def test_build_telegram_interactive_ctx_edit_message_text_uses_explicit_message_id_over_callback():
    captured = {}

    class Query:
        message = types.SimpleNamespace(message_id=99)

        async def edit_message_text(self, **kwargs):
            raise AssertionError("callback message should not be edited when message_id is explicit")

    class Msg:
        text = "hello"
        message_id = 99

    class Bot:
        async def edit_message_text(self, **kwargs):
            captured.update(kwargs)

    update = types.SimpleNamespace(
        callback_query=Query(),
        effective_message=Msg(),
        effective_user=types.SimpleNamespace(id=7),
        effective_chat=types.SimpleNamespace(id=8),
    )
    context = types.SimpleNamespace(args=[], user_data={}, bot=Bot())

    ctx = build_telegram_interactive_ctx(
        update,
        context,
        buttons_to_reply_markup_fn=lambda buttons: "markup",
    )
    await ctx.edit_message_text("done", message_id="123", buttons=[["ignored"]], parse_mode=None)
    assert captured["chat_id"] == 8
    assert captured["message_id"] == 123
    assert captured["text"] == "done"
    assert captured["reply_markup"] == "markup"


def test_normalize_telegram_markdown_converts_gfm_bold_and_heading():
    text = "## Title\n\nUse **bold** text."
    normalized = _normalize_telegram_markdown(text, "Markdown")
    assert normalized == "*Title*\n\nUse *bold* text."


def test_normalize_telegram_markdown_flattens_gfm_table():
    text = (
        "| Dimension | Nexus | OpenClaw |\n"
        "|---|---|---|\n"
        "| Primary purpose | Dev-ops command center | Personal AI assistant |\n"
    )
    normalized = _normalize_telegram_markdown(text, "Markdown")
    assert "Dimension: Primary purpose" in normalized
    assert "Nexus: Dev-ops command center" in normalized
    assert "OpenClaw: Personal AI assistant" in normalized


def test_clip_telegram_text_truncates_long_messages():
    clipped = _clip_telegram_text("x" * 5000, limit=50)
    assert len(clipped) <= 50
    assert clipped.endswith("[truncated]")


def test_is_message_not_modified_error_matches_telegram_bad_request():
    exc = RuntimeError(
        "BadRequest: Message is not modified: specified new message content and reply markup are exactly the same as a current content and reply markup of the message"
    )
    assert _is_message_not_modified_error(exc) is True


@pytest.mark.asyncio
async def test_edit_with_parse_fallback_ignores_not_modified_error():
    async def _edit(**_kwargs):
        raise RuntimeError("BadRequest: Message is not modified")

    result = await _edit_with_parse_fallback(
        _edit,
        text="same",
        reply_markup=None,
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )
    assert result is None
