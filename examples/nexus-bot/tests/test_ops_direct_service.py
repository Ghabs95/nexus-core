import logging
from types import SimpleNamespace

import pytest

from nexus.core import ops_direct_service as svc
from nexus.core.ops_direct_service import handle_direct_request


class _Ctx:
    def __init__(self, args=None, user_id="1"):
        self.args = args or []
        self.user_id = user_id
        self.replies = []
        self.edits = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        return "msg"

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)


@pytest.mark.asyncio
async def test_direct_service_usage_returns_handled():
    ctx = _Ctx(args=["only", "two"])
    deps = SimpleNamespace(logger=logging.getLogger("test"), allowed_user_ids=[], project_config={})
    handled = await handle_direct_request(
        ctx,
        deps,
        resolve_agent_type=lambda *a, **k: None,
        build_direct_chat_persona=lambda *a, **k: "",
    )
    assert handled is True
    assert "Usage: /direct" in ctx.replies[-1]


@pytest.mark.asyncio
async def test_direct_service_handles_issue_fallback_path():
    ctx = _Ctx(args=["acme", "@dev", "hello"])
    plugin = SimpleNamespace(
        create_issue=lambda **_k: "https://github.com/acme/repo/issues/12",
        add_comment=lambda *_a, **_k: None,
    )
    captured = {}

    def _get_direct_issue_plugin(repo, requester_nexus_id=None):
        captured["repo"] = repo
        captured["requester_nexus_id"] = requester_nexus_id
        return plugin

    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        allowed_user_ids=[],
        project_config={"acme": {"agents_dir": "agents"}},
        base_dir="/tmp",
        nexus_dir_name="nexus",
        get_repo=lambda project: "acme/repo",
        get_direct_issue_plugin=_get_direct_issue_plugin,
        requester_context_builder=lambda user_id: {"nexus_id": f"nexus-{user_id}"},
    )
    original = svc.resolve_agents_for_project
    try:
        svc.resolve_agents_for_project = lambda *_a, **_k: {"dev": "developer.md"}
        handled = await handle_direct_request(
            ctx,
            deps,
            resolve_agent_type=lambda *a, **k: None,
            build_direct_chat_persona=lambda *a, **k: "",
        )
    finally:
        svc.resolve_agents_for_project = original
    assert handled is True
    assert ctx.edits
    assert "Direct request created" in str(ctx.edits[-1].get("text", ""))
    assert captured["repo"] == "acme/repo"
    assert captured["requester_nexus_id"] == "nexus-1"


@pytest.mark.asyncio
async def test_direct_service_passes_requester_context_to_chat_analysis():
    ctx = _Ctx(args=["acme", "@designer", "hello"])
    captured = {}

    class _Orchestrator:
        def run_text_to_speech_analysis(self, **kwargs):
            captured.update(kwargs)
            return {"text": "ok"}

    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        allowed_user_ids=[],
        project_config={"acme": {"agents_dir": "agents"}},
        base_dir="/tmp",
        nexus_dir_name="nexus",
        create_chat=lambda *a, **k: "chat-1",
        append_message=lambda *a, **k: None,
        get_chat_history=lambda _user_id: "history",
        ai_persona="persona",
        orchestrator=_Orchestrator(),
        requester_context_builder=lambda user_id: {"nexus_id": f"nexus-{user_id}"},
    )
    original = svc.resolve_agents_for_project
    original_get_project_chat_agent_types = svc.get_project_chat_agent_types
    try:
        svc.resolve_agents_for_project = lambda *_a, **_k: {"designer": "designer.md"}
        svc.get_project_chat_agent_types = lambda _cfg: ["designer"]
        handled = await handle_direct_request(
            ctx,
            deps,
            resolve_agent_type=lambda *a, **k: "designer",
            build_direct_chat_persona=lambda *a, **k: "persona",
        )
    finally:
        svc.resolve_agents_for_project = original
        svc.get_project_chat_agent_types = original_get_project_chat_agent_types

    assert handled is True
    assert captured["requester_context"] == {"nexus_id": "nexus-1"}
