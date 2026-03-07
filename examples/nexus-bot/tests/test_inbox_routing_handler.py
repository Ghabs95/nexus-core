import pytest

from nexus.core.handlers import inbox_routing_handler as routing


@pytest.fixture(autouse=True)
def _force_file_inbox_backend(monkeypatch):
    """Prevent tests from writing into a live Postgres inbox queue."""
    monkeypatch.setattr(routing, "get_inbox_storage_backend", lambda: "file")
    monkeypatch.setattr(routing, "enqueue_task", lambda **_kwargs: 0)


class _FakeOrchestrator:
    def __init__(self, payload):
        self.payload = payload

    def run_text_to_speech_analysis(self, **_kwargs):
        return self.payload


class _FailingOrchestrator:
    def run_text_to_speech_analysis(self, **_kwargs):
        raise AssertionError("classifier should not be called when project context is set")


class _ContextNameOrchestrator:
    def __init__(self, generated_name: str):
        self.generated_name = generated_name

    def run_text_to_speech_analysis(self, **kwargs):
        task = kwargs.get("task")
        if task != "generate_name":
            raise AssertionError("Only generate_name should be called when project context is set")
        return {"text": self.generated_name}


class _WrappedContextNameOrchestrator:
    def run_text_to_speech_analysis(self, **kwargs):
        task = kwargs.get("task")
        if task != "generate_name":
            raise AssertionError("Only generate_name should be called when project context is set")
        return {"response": '{"text":"Adapter Configurable Persistence"}'}


@pytest.mark.asyncio
async def test_process_inbox_task_parses_project_from_response_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(routing, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(routing, "PROJECT_CONFIG", {"nexus": {"workspace": "nexus"}})
    monkeypatch.setattr(
        routing,
        "get_inbox_dir",
        lambda workspace_root, project: str(
            tmp_path / workspace_root.split("/")[-1] / project / "inbox"
        ),
    )

    orchestrator = _FakeOrchestrator(
        {
            "session_id": "abc",
            "response": (
                '{"project": "nexus", "type": "feature", '
                '"task_name": "evaluate-feature-alignment-knowledge-base"}'
            ),
        }
    )

    result = await routing.process_inbox_task(
        text="evaluate this feature",
        orchestrator=orchestrator,
        message_id_or_unique_id="123",
    )

    assert result["success"] is True
    assert result["project"] == "nexus"


@pytest.mark.asyncio
async def test_process_inbox_task_uses_project_hint_when_classifier_project_missing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(routing, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(routing, "PROJECT_CONFIG", {"sampleco": {"workspace": "sampleco"}})
    monkeypatch.setattr(
        routing,
        "get_inbox_dir",
        lambda workspace_root, project: str(
            tmp_path / workspace_root.split("/")[-1] / project / "inbox"
        ),
    )

    orchestrator = _FakeOrchestrator(
        {"response": '{"type": "feature", "task_name": "missing-project"}'}
    )

    result = await routing.process_inbox_task(
        text="please route this",
        orchestrator=orchestrator,
        message_id_or_unique_id="456",
        project_hint="sampleco",
    )

    assert result["success"] is True
    assert result["project"] == "sampleco"


@pytest.mark.asyncio
async def test_process_inbox_task_skips_classification_when_project_hint_set(tmp_path, monkeypatch):
    monkeypatch.setattr(routing, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(routing, "PROJECT_CONFIG", {"sampleco": {"workspace": "sampleco"}})
    monkeypatch.setattr(
        routing,
        "get_inbox_dir",
        lambda workspace_root, project: str(
            tmp_path / workspace_root.split("/")[-1] / project / "inbox"
        ),
    )

    result = await routing.process_inbox_task(
        text="route directly with context",
        orchestrator=_FailingOrchestrator(),
        message_id_or_unique_id="789",
        project_hint="sampleco",
    )

    assert result["success"] is True
    assert result["project"] == "sampleco"


@pytest.mark.asyncio
async def test_process_inbox_task_generates_task_name_when_project_hint_set(tmp_path, monkeypatch):
    monkeypatch.setattr(routing, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(routing, "PROJECT_CONFIG", {"nexus": {"workspace": "nexus"}})
    monkeypatch.setattr(
        routing,
        "get_inbox_dir",
        lambda workspace_root, project: str(
            tmp_path / workspace_root.split("/")[-1] / project / "inbox"
        ),
    )

    result = await routing.process_inbox_task(
        text="Design a YAML-based workflow orchestration system",
        orchestrator=_ContextNameOrchestrator("YAML Workflow Orchestration"),
        message_id_or_unique_id="901",
        project_hint="nexus",
    )

    assert result["success"] is True
    task_file = tmp_path / "nexus" / "nexus" / "inbox" / "task_901.md"
    assert task_file.exists()
    content = task_file.read_text(encoding="utf-8")
    assert "**Task Name:** yaml-workflow-orchestration" in content


@pytest.mark.asyncio
async def test_process_inbox_task_generates_task_name_from_wrapped_response(tmp_path, monkeypatch):
    monkeypatch.setattr(routing, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(routing, "PROJECT_CONFIG", {"nexus": {"workspace": "nexus"}})
    monkeypatch.setattr(
        routing,
        "get_inbox_dir",
        lambda workspace_root, project: str(
            tmp_path / workspace_root.split("/")[-1] / project / "inbox"
        ),
    )

    result = await routing.process_inbox_task(
        text="Use adapter so storage backend can switch between JSON and Postgres",
        orchestrator=_WrappedContextNameOrchestrator(),
        message_id_or_unique_id="902",
        project_hint="nexus",
    )

    assert result["success"] is True
    task_file = tmp_path / "nexus" / "nexus" / "inbox" / "task_902.md"
    assert task_file.exists()
    content = task_file.read_text(encoding="utf-8")
    assert "**Task Name:** adapter-configurable-persistence" in content


def test_render_task_markdown_omits_sensitive_requester_fields():
    content = routing._render_task_markdown(
        project="nexus",
        task_type="feature",
        task_name="privacy-test",
        content="Implement privacy-safe task rendering",
        raw_text="raw text",
        requester_context={
            "nexus_id": "31efac50-8610-4b4b-9129-6a48e96a110c",
            "platform": "telegram",
            "platform_user_id": "47168736",
        },
    )

    assert "**Requester Nexus ID:**" not in content
    assert "**Requester Platform:**" not in content
    assert "**Requester Platform User ID:**" not in content
