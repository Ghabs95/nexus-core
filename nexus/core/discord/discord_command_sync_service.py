from dataclasses import dataclass


@dataclass(frozen=True)
class DiscordCommandSyncPlan:
    """Resolved slash-command sync behavior for Discord startup."""

    sync_guild_commands: bool
    sync_global_commands: bool
    clear_guild_commands: bool
    clear_global_commands: bool


def build_command_sync_plan(
    *,
    guild_id: int | None,
    enable_user_install_private_chat: bool,
) -> DiscordCommandSyncPlan:
    """Build sync strategy for guild-scoped and global command registrations.

    Private-chat user installs should run global-only to avoid duplicate entries
    from guild + global registrations in the slash-command picker.
    """
    has_guild_scope = guild_id is not None
    if not has_guild_scope:
        return DiscordCommandSyncPlan(
            sync_guild_commands=False,
            sync_global_commands=True,
            clear_guild_commands=False,
            clear_global_commands=False,
        )

    if enable_user_install_private_chat:
        return DiscordCommandSyncPlan(
            sync_guild_commands=False,
            sync_global_commands=True,
            clear_guild_commands=True,
            clear_global_commands=False,
        )

    return DiscordCommandSyncPlan(
        sync_guild_commands=True,
        sync_global_commands=True,
        clear_guild_commands=False,
        clear_global_commands=True,
    )
