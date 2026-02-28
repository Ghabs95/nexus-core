import logging
from types import SimpleNamespace

import pytest
from services.workflow.workflow_reprocess_continue_service import (
    _maybe_reset_continue_workflow_position,
    _launch_continue_agent,
    handle_continue,
    handle_reprocess,
)


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


class _CtxEditFails(_Ctx):
    async def edit_message_text(self, **kwargs):
        raise RuntimeError("edit failed")


@pytest.mark.asyncio
async def test_reprocess_service_prompts_when_no_args():
    ctx = _Ctx()
    seen = {}
    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        allowed_user_ids=[],
        prompt_project_selection=lambda c, cmd: seen.setdefault("cmd", cmd),
    )

    async def _prompt(c, cmd):
        seen["cmd"] = cmd

    deps.prompt_project_selection = _prompt
    await handle_reprocess(
        ctx, deps, build_issue_url=lambda *a, **k: "", resolve_repo=lambda *a, **k: ""
    )
    assert seen["cmd"] == "reprocess"


@pytest.mark.asyncio
async def test_continue_service_prompts_when_no_args():
    ctx = _Ctx()
    seen = {}
    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        allowed_user_ids=[],
    )

    async def _prompt(c, cmd):
        seen["cmd"] = cmd

    deps.prompt_project_selection = _prompt
    await handle_continue(ctx, deps, finalize_workflow=lambda *a, **k: None)
    assert seen["cmd"] == "continue"


@pytest.mark.asyncio
async def test_launch_continue_agent_replaces_progress_message_on_launch_error():
    ctx = _Ctx()
    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        invoke_copilot_agent=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    continue_ctx = {
        "resumed_from": "designer",
        "agent_type": "triage",
        "agents_abs": "/tmp/agents",
        "workspace_abs": "/tmp/workspace",
        "issue_url": "https://github.com/acme/repo/issues/86",
        "tier_name": "full",
        "content": "task",
        "continuation_prompt": "continue",
        "log_subdir": "nexus",
    }

    await _launch_continue_agent(ctx, deps, issue_num="86", continue_ctx=continue_ctx)

    assert len(ctx.replies) == 1
    assert "Continuing issue #86" in ctx.replies[0]
    assert len(ctx.edits) == 1
    assert ctx.edits[0]["message_id"] == "msg"
    assert "Failed to continue agent for issue #86" in ctx.edits[0]["text"]


@pytest.mark.asyncio
async def test_launch_continue_agent_falls_back_to_reply_when_edit_fails():
    deleted = {}

    async def _delete_message(**kwargs):
        deleted.update(kwargs)

    ctx = _CtxEditFails()
    ctx.chat_id = 999
    ctx.telegram_context = SimpleNamespace(bot=SimpleNamespace(delete_message=_delete_message))
    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        invoke_copilot_agent=lambda **kwargs: (123, "copilot"),
    )
    continue_ctx = {
        "resumed_from": "designer",
        "agent_type": "triage",
        "agents_abs": "/tmp/agents",
        "workspace_abs": "/tmp/workspace",
        "issue_url": "https://github.com/acme/repo/issues/86",
        "tier_name": "full",
        "content": "task",
        "continuation_prompt": "continue",
        "log_subdir": "nexus",
    }

    await _launch_continue_agent(ctx, deps, issue_num="86", continue_ctx=continue_ctx)

    # First reply is transient progress; second is fallback final status.
    assert len(ctx.replies) == 2
    assert "Continuing issue #86" in ctx.replies[0]
    assert "Agent continued for issue #86" in ctx.replies[1]
    assert deleted["chat_id"] == 999


@pytest.mark.asyncio
async def test_maybe_reset_continue_workflow_position_resets_for_recovered_next_agent():
    called = {}

    class _WorkflowPlugin:
        async def reset_to_agent_for_issue(self, issue_num, agent_type):
            called["issue_num"] = issue_num
            called["agent_type"] = agent_type
            return True

    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        workflow_state_plugin_kwargs={},
        get_workflow_state_plugin=lambda **kwargs: _WorkflowPlugin(),
    )
    ctx = _Ctx()
    ok = await _maybe_reset_continue_workflow_position(
        ctx,
        deps,
        issue_num="106",
        continue_ctx={
            "forced_agent_override": False,
            "sync_workflow_to_agent": True,
            "agent_type": "developer",
        },
    )

    assert ok is True
    assert called == {"issue_num": "106", "agent_type": "developer"}
