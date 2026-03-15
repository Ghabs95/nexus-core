from nexus.core.command_visibility import filter_visible_commands, is_command_visible


def test_is_command_visible_hides_filesystem_only_commands_in_db_mode():
    assert is_command_visible("active", local_task_files=False) is False
    assert is_command_visible("logs", local_task_files=False) is True
    assert is_command_visible("tail", local_task_files=False) is True
    assert is_command_visible("status", local_task_files=False) is True


def test_filter_visible_commands_preserves_order():
    commands = ["menu", "status", "active", "logs", "help"]
    assert filter_visible_commands(commands, local_task_files=False) == [
        "menu",
        "status",
        "logs",
        "help",
    ]
