import logging
from types import SimpleNamespace

import pytest
from services.workflow_reprocess_continue_service import handle_continue, handle_reprocess


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
