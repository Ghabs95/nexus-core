from nexus.core.telegram import telegram_bootstrap_ui_service as svc


def test_build_help_text_hides_filesystem_commands_in_db_mode(monkeypatch):
    monkeypatch.setattr(
        svc,
        "get_storage_capabilities",
        lambda: type("Caps", (), {"local_task_files": False})(),
    )
    text = svc.build_help_text()
    assert "/active" not in text
    assert "/logs <project> <issue#>" not in text
    assert "/tail <project> <issue#>" not in text
    assert "/status [project|all]" in text
    assert "/wfstate <project> <issue#>" in text


def test_build_startup_commands_hides_active_in_db_mode(monkeypatch):
    monkeypatch.setattr(svc, "is_command_visible", lambda command: command != "active")

    class _BotCommand:
        def __init__(self, command, description):
            self.command = command
            self.description = description

    commands = svc.build_startup_commands(bot_command_cls=_BotCommand)
    assert [command.command for command in commands] == ["menu", "chat", "new", "status", "help"]
