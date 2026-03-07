import pytest

from nexus.core.handlers import chat_command_handlers as svc


class _Ctx:
    def __init__(self):
        self.user_id = "7"
        self.user_state = {}
        self.query = None
        self.calls = []

    async def reply_text(self, text, buttons=None):
        self.calls.append(("reply", text, buttons))

    async def edit_message_text(self, message_id, text, buttons=None):
        self.calls.append(("edit", message_id, text, buttons))

    async def answer_callback_query(self):
        self.calls.append(("answer",))


@pytest.mark.asyncio
async def test_chat_menu_handler_activates_chat_session(monkeypatch):
    monkeypatch.setattr(svc, "get_active_chat", lambda _user_id: "chat-1")
    monkeypatch.setattr(svc, "list_chats", lambda _user_id: [{"id": "chat-1", "title": "Main"}])
    monkeypatch.setattr(svc, "get_chat", lambda _user_id, _chat_id: {"metadata": {}})

    ctx = _Ctx()
    await svc.chat_menu_handler(ctx)

    assert ctx.user_state["chat_session_active"] is True
    assert "Exit chat mode to use hands-free task creation" in ctx.calls[-1][1]


@pytest.mark.asyncio
async def test_chat_callback_exit_clears_chat_session(monkeypatch):
    monkeypatch.setattr(svc, "get_active_chat", lambda _user_id: "chat-1")
    monkeypatch.setattr(svc, "list_chats", lambda _user_id: [])
    monkeypatch.setattr(svc, "get_chat", lambda _user_id, _chat_id: {"metadata": {}})

    ctx = _Ctx()
    ctx.user_state["chat_session_active"] = True
    ctx.query = type(
        "Q",
        (),
        {"action_data": "chat:exit", "message_id": "55"},
    )()

    await svc.chat_callback_handler(ctx)

    assert "chat_session_active" not in ctx.user_state
    assert ctx.calls[-1][0] == "edit"
    assert "Chat mode exited" in ctx.calls[-1][2]
