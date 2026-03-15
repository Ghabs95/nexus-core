import asyncio
from types import SimpleNamespace


def test_resolve_workflow_id_for_creation_force_new_instance_adds_suffix():
    from nexus.plugins.builtin.workflow_state_engine_plugin import WorkflowStateEnginePlugin

    class _Engine:
        async def get_workflow(self, workflow_id):
            if workflow_id in {"sampleproj-1-full-r1", "sampleproj-1-full-r2"}:
                return object()
            return None

    engine = _Engine()
    plugin = WorkflowStateEnginePlugin({"engine_factory": lambda: engine})

    workflow_id = asyncio.run(
        plugin._resolve_workflow_id_for_creation(
            project_name="sampleproj",
            issue_number="1",
            tier_name="full",
            force_new_instance=True,
        )
    )

    assert workflow_id == "sampleproj-1-full-r3"


def test_prepare_reprocess_workflow_instance_uses_force_new_instance():
    from nexus.core.workflow_runtime import workflow_reprocess_continue_service as service

    class _Ctx:
        def __init__(self):
            self.messages: list[str] = []

        async def reply_text(self, text):
            self.messages.append(str(text))
            return 1

    class _WorkflowPlugin:
        def __init__(self):
            self.create_kwargs = None
            self.started: list[str] = []

        async def create_workflow_for_issue(self, **kwargs):
            self.create_kwargs = dict(kwargs)
            return "sampleproj-1-full-r1"

        async def start_workflow(self, workflow_id):
            self.started.append(str(workflow_id))
            return True

    workflow_plugin = _WorkflowPlugin()
    deps = SimpleNamespace(
        get_workflow_state_plugin=lambda **_kwargs: workflow_plugin,
        workflow_state_plugin_kwargs={},
        logger=SimpleNamespace(
            info=lambda *_args, **_kwargs: None,
            error=lambda *_args, **_kwargs: None,
        ),
    )
    ctx = _Ctx()

    ok = asyncio.run(
        service._prepare_reprocess_workflow_instance(
            ctx,
            deps,
            issue_num="1",
            project_name="sampleproj",
            tier_name="full",
            details={"title": "Fix parser edge case", "labels": ["bug"]},
        )
    )

    assert ok is True
    assert workflow_plugin.create_kwargs is not None
    assert workflow_plugin.create_kwargs["force_new_instance"] is True
    assert workflow_plugin.create_kwargs["task_type"] == "bug"
    assert workflow_plugin.started == ["sampleproj-1-full-r1"]
    assert ctx.messages == []


def test_parse_reprocess_options_supports_clear_exclusions_and_tool_override():
    from nexus.core.workflow_runtime import workflow_reprocess_continue_service as service

    options, error = service._parse_reprocess_options(
        ["--clear-exclusions", "--tool", "codex"]
    )

    assert error is None
    assert options["clear_exclusions"] is True
    assert options["preferred_tool"] == "codex"


def test_parse_reprocess_options_rejects_unknown_flag():
    from nexus.core.workflow_runtime import workflow_reprocess_continue_service as service

    options, error = service._parse_reprocess_options(["--bad-flag"])

    assert options["clear_exclusions"] is False
    assert options["preferred_tool"] is None
    assert isinstance(error, str)
    assert "Unknown reprocess option" in error


def test_parse_continue_options_supports_clear_exclusions_and_tool_override():
    from nexus.core.workflow_runtime import workflow_reprocess_continue_service as service

    options, passthrough, error = service._parse_continue_options(
        ["from:reviewer", "resume now", "--clear-exclusions", "--tool", "codex"]
    )

    assert error is None
    assert options["clear_exclusions"] is True
    assert options["preferred_tool"] == "codex"
    assert passthrough == ["from:reviewer", "resume now"]


def test_parse_continue_options_rejects_unknown_flag():
    from nexus.core.workflow_runtime import workflow_reprocess_continue_service as service

    options, passthrough, error = service._parse_continue_options(["--bad-flag"])

    assert options["clear_exclusions"] is False
    assert options["preferred_tool"] is None
    assert passthrough == []
    assert isinstance(error, str)
    assert "Unknown continue option" in error


def test_launch_reprocess_applies_clear_exclusions_and_preferred_tool():
    from nexus.core.workflow_runtime import workflow_reprocess_continue_service as service

    class _Ctx:
        def __init__(self):
            self.messages: list[str] = []
            self.edits: list[str] = []

        async def reply_text(self, text):
            self.messages.append(str(text))
            return 1

        async def edit_message_text(self, *, message_id, text):
            assert message_id == 1
            self.edits.append(str(text))
            return True

    invoked: dict[str, object] = {}
    cleared: list[str] = []

    deps = SimpleNamespace(
        base_dir="/tmp/base",
        invoke_ai_agent=lambda **kwargs: (invoked.update(kwargs), (321, "codex"))[1],
        clear_issue_excluded_tools=lambda issue_num: (cleared.append(str(issue_num)), True)[1],
    )
    ctx = _Ctx()

    asyncio.run(
        service._launch_reprocess(
            ctx,
            deps,
            issue_num="119",
            config={"agents_dir": "agents", "workspace": "workspace"},
            issue_url="https://github.com/acme/repo/issues/119",
            tier_name="full",
            content="task",
            project_name="nexus",
            project_key="nexus",
            requester_nexus_id="nexus-user-119",
            clear_exclusions=True,
            preferred_tool="codex",
        )
    )

    assert cleared == ["119"]
    assert invoked["preferred_tool"] == "codex"
    assert invoked["requester_nexus_id"] == "nexus-user-119"
    assert ctx.messages
    assert "Clearing persisted tool exclusions" in ctx.messages[0]
    assert ctx.edits
    assert "Tool exclusions reset: yes" in ctx.edits[0]
    assert "Requested tool: codex" in ctx.edits[0]


def test_launch_continue_applies_clear_exclusions_and_preferred_tool():
    from nexus.core.workflow_runtime import workflow_reprocess_continue_service as service

    class _Ctx:
        def __init__(self):
            self.messages: list[str] = []
            self.edits: list[str] = []

        async def reply_text(self, text, parse_mode=None):
            assert parse_mode in {None, "Markdown"}
            self.messages.append(str(text))
            return 1

        async def edit_message_text(self, *, message_id, text, parse_mode=None):
            assert message_id == 1
            assert parse_mode is None
            self.edits.append(str(text))
            return True

    invoked: dict[str, object] = {}
    cleared: list[str] = []

    deps = SimpleNamespace(
        invoke_ai_agent=lambda **kwargs: (invoked.update(kwargs), (654, "codex"))[1],
        clear_issue_excluded_tools=lambda issue_num: (cleared.append(str(issue_num)), True)[1],
        logger=SimpleNamespace(
            warning=lambda *_args, **_kwargs: None,
            debug=lambda *_args, **_kwargs: None,
            error=lambda *_args, **_kwargs: None,
        ),
    )
    ctx = _Ctx()
    continue_ctx = {
        "resumed_from": "designer",
        "agent_type": "developer",
        "agents_abs": "/tmp/agents",
        "workspace_abs": "/tmp/workspace",
        "issue_url": "https://github.com/acme/repo/issues/119",
        "tier_name": "full",
        "content": "task",
        "continuation_prompt": "resume now",
        "log_subdir": "nexus",
        "requester_nexus_id": "nexus-user-119",
    }

    asyncio.run(
        service._launch_continue_agent(
            ctx,
            deps,
            issue_num="119",
            continue_ctx=continue_ctx,
            clear_exclusions=True,
            preferred_tool="codex",
        )
    )

    assert cleared == ["119"]
    assert invoked["preferred_tool"] == "codex"
    assert invoked["requester_nexus_id"] == "nexus-user-119"
    assert ctx.messages
    assert "Clearing persisted tool exclusions" in ctx.messages[0]
    assert ctx.edits
    assert "Tool exclusions reset: yes" in ctx.edits[0]
    assert "Requested tool: codex" in ctx.edits[0]
