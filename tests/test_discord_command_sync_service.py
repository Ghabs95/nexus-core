from nexus.core.discord.discord_command_sync_service import build_command_sync_plan


def test_build_command_sync_plan_global_only_without_guild():
    plan = build_command_sync_plan(
        guild_id=None,
        enable_user_install_private_chat=False,
    )

    assert plan.sync_guild_commands is False
    assert plan.sync_global_commands is True
    assert plan.clear_guild_commands is False
    assert plan.clear_global_commands is False


def test_build_command_sync_plan_guild_only_mode_clears_global():
    plan = build_command_sync_plan(
        guild_id=1234,
        enable_user_install_private_chat=False,
    )

    assert plan.sync_guild_commands is True
    assert plan.sync_global_commands is True
    assert plan.clear_guild_commands is False
    assert plan.clear_global_commands is True


def test_build_command_sync_plan_user_install_private_chat_is_global_only():
    plan = build_command_sync_plan(
        guild_id=1234,
        enable_user_install_private_chat=True,
    )

    assert plan.sync_guild_commands is False
    assert plan.sync_global_commands is True
    assert plan.clear_guild_commands is True
    assert plan.clear_global_commands is False
