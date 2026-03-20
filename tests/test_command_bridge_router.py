from __future__ import annotations

from types import SimpleNamespace

import pytest

from nexus.core.command_bridge.models import CommandRequest, RequesterContext
from nexus.core.command_bridge.router import CommandRouter


@pytest.fixture
def router(monkeypatch) -> CommandRouter:
    stub_deps = SimpleNamespace(
        workflow_state_plugin_kwargs={},
        requester_context_builder=None,
    )
    monkeypatch.setattr(
        "nexus.core.command_bridge.router.workflow_bridge_deps",
        lambda **_kwargs: SimpleNamespace(**stub_deps.__dict__),
    )
    monkeypatch.setattr(
        "nexus.core.command_bridge.router.monitoring_bridge_deps",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "nexus.core.command_bridge.router.ops_bridge_deps",
        lambda **_kwargs: SimpleNamespace(requester_context_builder=None),
    )
    monkeypatch.setattr(
        "nexus.core.command_bridge.router.issue_bridge_deps",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "nexus.core.command_bridge.router.get_workflow_state",
        lambda: SimpleNamespace(
            get_workflow_id=lambda issue_num: "demo-42-full" if str(issue_num) == "42" else None,
            load_all_mappings=lambda: {"42": "demo-42-full"},
        ),
    )
    return CommandRouter(allowed_user_ids=[], default_source_platform="openclaw")


@pytest.mark.asyncio
async def test_execute_returns_structured_result_and_captured_messages(router: CommandRouter):
    async def _plan_handler(*, client, user_id, text, args, raw_event=None, attachments=None):
        del user_id, text, raw_event, attachments
        ctx = router.build_context(
            client=client,
            user_id="alice",
            text="plan demo #42",
            args=args,
        )
        message_id = await ctx.reply_text("Planning issue #42")
        await ctx.edit_message_text(message_id=message_id, text="Plan queued for issue #42")

    router.register_command("plan", _plan_handler)

    result = await router.execute(
        CommandRequest(
            command="plan",
            args=["demo#42"],
            requester=RequesterContext(source_platform="openclaw", sender_id="alice"),
        )
    )

    assert result.status == "accepted"
    assert result.workflow_id == "demo-42-full"
    assert result.issue_number == "42"
    assert result.message == "Plan queued for issue #42"
    assert result.data["messages"][-1]["edited"] is True


@pytest.mark.asyncio
async def test_route_maps_freeform_text_to_supported_command(router: CommandRouter):
    captured: dict[str, object] = {}

    async def _logs_handler(*, client, user_id, text, args, raw_event=None, attachments=None):
        del user_id, raw_event, attachments
        captured["text"] = text
        captured["args"] = list(args)
        ctx = router.build_context(
            client=client,
            user_id="alice",
            text=text,
            args=args,
        )
        await ctx.reply_text("Showing logs")

    router.register_command("logs", _logs_handler)

    result = await router.route(
        CommandRequest(
            raw_text="show logs for demo#42",
            requester=RequesterContext(source_platform="openclaw", sender_id="alice"),
        )
    )

    assert result.status == "success"
    assert result.issue_number == "42"
    assert captured["args"] == ["demo", "42"]


@pytest.mark.asyncio
async def test_route_returns_clarification_for_unknown_freeform(router: CommandRouter):
    result = await router.route(
        CommandRequest(
            raw_text="please do a dance",
            requester=RequesterContext(source_platform="openclaw", sender_id="alice"),
        )
    )

    assert result.status == "clarification"
    assert "supported Nexus ARC command" in result.message
