import types

import discord_bot as svc
import pytest


class _Response:
    def __init__(self):
        self.calls = []

    def is_done(self):
        return False

    async def edit_message(self, **kwargs):
        self.calls.append(("edit_message", kwargs))

    async def send_message(self, *args, **kwargs):
        self.calls.append(("send_message", args, kwargs))


class _BridgeResponse:
    def __init__(self):
        self.calls = []
        self._done = False

    def is_done(self):
        return self._done

    async def defer(self, *, thinking=False, ephemeral=False):
        self.calls.append(("defer", {"thinking": thinking, "ephemeral": ephemeral}))
        self._done = True

    async def send_message(self, *args, **kwargs):
        self.calls.append(("send_message", args, kwargs))
        self._done = True


class _Followup:
    def __init__(self):
        self.calls = []

    async def send(self, *args, **kwargs):
        self.calls.append(("send", args, kwargs))
        return None


class _Interaction:
    def __init__(self, user_id=7):
        self.user = types.SimpleNamespace(id=user_id)
        self.response = _Response()


class _BridgeInteraction:
    def __init__(self, user_id=7):
        self.user = types.SimpleNamespace(id=user_id)
        self.response = _BridgeResponse()
        self.followup = _Followup()


class _MessageChannel:
    async def fetch_message(self, _message_id):
        return types.SimpleNamespace(edit=lambda **_kwargs: None)


class _Message:
    def __init__(self, *, user_id=7):
        self.author = types.SimpleNamespace(id=user_id)
        self.channel = _MessageChannel()
        self.replies = []

    async def reply(self, content, **_kwargs):
        self.replies.append(content)
        return types.SimpleNamespace(id=len(self.replies))


class _IdeationChannel:
    def __init__(self):
        self.sent = []

    async def send(self, content, **_kwargs):
        self.sent.append(content)
        return None


class _IdeationMessage:
    def __init__(self, *, user_id=7):
        self.author = types.SimpleNamespace(id=user_id)
        self.channel = _IdeationChannel()


@pytest.mark.asyncio
async def test_send_chat_menu_activates_chat_session(monkeypatch):
    monkeypatch.setattr(svc, "get_active_chat", lambda _user_id: "chat-1")
    monkeypatch.setattr(svc, "_autoselect_chat_project_from_auth", lambda *_args: (False, None))
    monkeypatch.setattr(svc, "list_chats", lambda _user_id: [{"id": "chat-1", "title": "Main"}])
    monkeypatch.setattr(svc, "get_chat", lambda _user_id, _chat_id: {"metadata": {}})
    svc._discord_chat_sessions.clear()

    interaction = _Interaction()
    await svc.send_chat_menu(interaction, 7)

    assert 7 in svc._discord_chat_sessions
    kind, kwargs = interaction.response.calls[-1]
    assert kind == "edit_message"
    assert "Exit chat mode to use hands-free task creation" in kwargs["content"]


@pytest.mark.asyncio
async def test_chat_menu_exit_button_clears_chat_session():
    svc._discord_chat_sessions.clear()
    svc._discord_chat_sessions.add(7)

    interaction = _Interaction()
    view = svc.ChatMenuView(user_id=7)
    exit_button = next(child for child in view.children if getattr(child, "custom_id", "") == "chat:exit")
    await exit_button.callback(interaction)

    assert 7 not in svc._discord_chat_sessions
    kind, kwargs = interaction.response.calls[-1]
    assert kind == "edit_message"
    assert "Chat mode exited" in kwargs["content"]


@pytest.mark.asyncio
async def test_setup_hook_removes_filesystem_only_commands_in_db_mode(monkeypatch):
    removed = []
    synced = []

    monkeypatch.setattr(svc, "DISCORD_GUILD_ID", None)
    monkeypatch.setattr(svc, "DISCORD_ENABLE_USER_INSTALL_PRIVATE_CHAT", True)
    monkeypatch.setattr(
        svc,
        "get_storage_capabilities",
        lambda: types.SimpleNamespace(local_task_files=False),
    )
    monkeypatch.setattr(svc.bot.tree, "remove_command", lambda name: removed.append(name))

    async def _sync(guild=None):
        synced.append(guild)
        return []

    monkeypatch.setattr(svc.bot.tree, "sync", _sync)
    monkeypatch.setattr(svc.bot.tree, "copy_global_to", lambda guild=None: None)
    monkeypatch.setattr(svc.bot.tree, "clear_commands", lambda guild=None: None)

    await svc.setup_hook()

    assert set(removed) == set(svc.FILESYSTEM_ONLY_COMMANDS)
    assert synced == [None]
    assert getattr(svc.bot.tree.allowed_installs, "guild", None) is True
    assert getattr(svc.bot.tree.allowed_installs, "user", None) is True
    assert getattr(svc.bot.tree.allowed_contexts, "guild", None) is True
    assert getattr(svc.bot.tree.allowed_contexts, "dm_channel", None) is True
    assert getattr(svc.bot.tree.allowed_contexts, "private_channel", None) is True


@pytest.mark.asyncio
async def test_setup_hook_guild_scope_clears_global_without_user_install_private_chat(monkeypatch):
    synced = []
    clear_calls = []
    copy_calls = []

    monkeypatch.setattr(svc, "DISCORD_GUILD_ID", 12345)
    monkeypatch.setattr(svc, "DISCORD_ENABLE_USER_INSTALL_PRIVATE_CHAT", False)
    monkeypatch.setattr(
        svc,
        "get_storage_capabilities",
        lambda: types.SimpleNamespace(local_task_files=True),
    )
    monkeypatch.setattr(svc.bot.tree, "remove_command", lambda _name: None)
    monkeypatch.setattr(
        svc.bot.tree,
        "copy_global_to",
        lambda guild=None: copy_calls.append(getattr(guild, "id", None)),
    )
    monkeypatch.setattr(
        svc.bot.tree,
        "clear_commands",
        lambda guild=None: clear_calls.append(guild),
    )

    async def _sync(guild=None):
        synced.append(guild)
        return []

    monkeypatch.setattr(svc.bot.tree, "sync", _sync)

    await svc.setup_hook()

    assert copy_calls == [12345]
    assert clear_calls == [None]
    assert len(synced) == 2
    assert getattr(synced[0], "id", None) == 12345
    assert synced[1] is None
    assert getattr(svc.bot.tree.allowed_installs, "guild", None) is True
    assert getattr(svc.bot.tree.allowed_installs, "user", None) is False
    assert getattr(svc.bot.tree.allowed_contexts, "guild", None) is True
    assert getattr(svc.bot.tree.allowed_contexts, "dm_channel", None) is False
    assert getattr(svc.bot.tree.allowed_contexts, "private_channel", None) is False


@pytest.mark.asyncio
async def test_setup_hook_guild_scope_keeps_global_with_user_install_private_chat(monkeypatch):
    synced = []
    clear_calls = []
    copy_calls = []

    monkeypatch.setattr(svc, "DISCORD_GUILD_ID", 12345)
    monkeypatch.setattr(svc, "DISCORD_ENABLE_USER_INSTALL_PRIVATE_CHAT", True)
    monkeypatch.setattr(
        svc,
        "get_storage_capabilities",
        lambda: types.SimpleNamespace(local_task_files=True),
    )
    monkeypatch.setattr(svc.bot.tree, "remove_command", lambda _name: None)
    monkeypatch.setattr(
        svc.bot.tree,
        "copy_global_to",
        lambda guild=None: copy_calls.append(getattr(guild, "id", None)),
    )
    monkeypatch.setattr(
        svc.bot.tree,
        "clear_commands",
        lambda guild=None: clear_calls.append(guild),
    )

    async def _sync(guild=None):
        synced.append(guild)
        return []

    monkeypatch.setattr(svc.bot.tree, "sync", _sync)

    await svc.setup_hook()

    assert copy_calls == []
    assert len(clear_calls) == 1
    assert getattr(clear_calls[0], "id", None) == 12345
    assert len(synced) == 2
    assert getattr(synced[0], "id", None) == 12345
    assert synced[1] is None
    assert getattr(svc.bot.tree.allowed_installs, "guild", None) is True
    assert getattr(svc.bot.tree.allowed_installs, "user", None) is True
    assert getattr(svc.bot.tree.allowed_contexts, "guild", None) is True
    assert getattr(svc.bot.tree.allowed_contexts, "dm_channel", None) is True
    assert getattr(svc.bot.tree.allowed_contexts, "private_channel", None) is True


@pytest.mark.asyncio
async def test_run_bridge_with_picker_auto_defers_and_uses_followup(monkeypatch):
    monkeypatch.setattr(svc, "check_permission_for_action", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(svc, "_command_issue_state", lambda _name: "open")

    interaction = _BridgeInteraction(user_id=42)
    await svc._run_bridge_with_picker(
        interaction,
        command_name="resume",
        handler=lambda *_args, **_kwargs: None,
        deps_factory=lambda: None,
        project=None,
        require_issue=True,
    )

    assert interaction.response.calls
    first_kind, first_payload = interaction.response.calls[0]
    assert first_kind == "defer"
    assert first_payload == {"thinking": True, "ephemeral": True}
    assert interaction.followup.calls
    kind, args, kwargs = interaction.followup.calls[-1]
    assert kind == "send"
    assert "Select a project" in args[0]
    assert kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_dispatch_message_bridge_command_runs_reprocess_from_typed_slash(monkeypatch):
    captured = {}

    async def _handler(ctx, _deps):
        captured["args"] = list(ctx.args)
        await ctx.reply_text("ok")

    monkeypatch.setattr(
        svc,
        "_message_bridge_command_specs",
        lambda: {"reprocess": (_handler, lambda: object())},
    )
    monkeypatch.setattr(svc, "check_permission_for_action", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(svc, "bot", types.SimpleNamespace(user=types.SimpleNamespace(name="NexusBot")))

    message = _Message(user_id=42)
    handled = await svc._dispatch_message_bridge_command(message, "/reprocess example-org 1")

    assert handled is True
    assert captured["args"] == ["example-org", "1"]
    assert message.replies and message.replies[-1] == "ok"


@pytest.mark.asyncio
async def test_begin_feature_ideation_requires_model_match(monkeypatch):
    svc._pending_feature_ideation.clear()
    monkeypatch.setattr(svc, "get_chat", lambda _user_id: {"metadata": {"project_key": "nexus"}})
    monkeypatch.setattr(
        svc,
        "detect_feature_ideation_intent",
        lambda *_args, **_kwargs: (False, 0.91, "conversation_question"),
    )

    message = _IdeationMessage(user_id=77)
    handled = await svc._begin_feature_ideation(
        message,
        "Is there any enhancement or addition we can do?",
    )

    assert handled is False
    assert 77 not in svc._pending_feature_ideation
    assert message.channel.sent == []


@pytest.mark.asyncio
async def test_begin_feature_ideation_prompts_count_when_model_matches(monkeypatch):
    svc._pending_feature_ideation.clear()
    monkeypatch.setattr(svc, "get_chat", lambda _user_id: {"metadata": {}})
    monkeypatch.setattr(svc, "detect_feature_project", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        svc,
        "detect_feature_ideation_intent",
        lambda *_args, **_kwargs: (True, 0.88, "explicit_multi_feature_request"),
    )

    message = _IdeationMessage(user_id=88)
    handled = await svc._begin_feature_ideation(
        message,
        "Can you propose some feature ideas for this project?",
    )

    assert handled is True
    assert svc._pending_feature_ideation[88]["step"] == "awaiting_count"
    assert any("How many feature proposals" in sent for sent in message.channel.sent)
