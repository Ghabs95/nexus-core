from nexus.core.discord.discord_text_command_service import parse_discord_text_command


def test_parse_discord_text_command_basic():
    parsed = parse_discord_text_command("/reprocess example-org 1")

    assert parsed is not None
    assert parsed.name == "reprocess"
    assert parsed.args == ["example-org", "1"]


def test_parse_discord_text_command_allows_bot_mention():
    parsed = parse_discord_text_command(
        "/reprocess@NexusBot example-org 1",
        bot_username="NexusBot",
    )

    assert parsed is not None
    assert parsed.name == "reprocess"
    assert parsed.args == ["example-org", "1"]


def test_parse_discord_text_command_skips_other_bot_mentions():
    parsed = parse_discord_text_command(
        "/reprocess@OtherBot example-org 1",
        bot_username="NexusBot",
    )

    assert parsed is None


def test_parse_discord_text_command_handles_quoted_args():
    parsed = parse_discord_text_command('/respond example-org 1 "ship this now"')

    assert parsed is not None
    assert parsed.name == "respond"
    assert parsed.args == ["example-org", "1", "ship this now"]
