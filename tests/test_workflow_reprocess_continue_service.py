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
