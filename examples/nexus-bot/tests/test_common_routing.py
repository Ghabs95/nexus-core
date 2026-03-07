import pytest

from nexus.core.handlers.common_routing import (
    extract_json_dict,
    parse_intent_result,
    route_task_with_context,
    run_conversation_turn,
)


class _IntentOrchestrator:
    def __init__(self, payload):
        self.payload = payload

    def run_text_to_speech_analysis(self, **_kwargs):
        return self.payload


class _ConversationOrchestrator:
    def __init__(self):
        self.calls = []

    def run_text_to_speech_analysis(self, **kwargs):
        self.calls.append(kwargs)
        assert kwargs.get("task") == "chat"
        return {"text": "ok"}


def test_extract_json_dict_handles_markdown_wrapped_json():
    payload = '```json\n{"intent": "conversation"}\n```'

    parsed = extract_json_dict(payload)

    assert parsed == {"intent": "conversation"}


def test_parse_intent_result_reparses_embedded_json():
    orchestrator = _IntentOrchestrator({"text": '```json\n{"intent":"conversation"}\n```'})

    result = parse_intent_result(orchestrator, "hello", extract_json_dict)

    assert result["intent"] == "conversation"


def test_run_conversation_turn_persists_user_and_assistant_messages():
    saved = []
    orchestrator = _ConversationOrchestrator()

    def append_message(_user_id, role, text):
        saved.append((role, text))

    result = run_conversation_turn(
        user_id=1,
        text="hello",
        orchestrator=orchestrator,
        get_chat_history=lambda _uid: "history",
        append_message=append_message,
        persona="persona",
        requester_context={"nexus_id": "nexus-1"},
    )

    assert result == "ok"
    assert saved == [("user", "hello"), ("assistant", "ok")]
    assert orchestrator.calls[0]["requester_context"] == {"nexus_id": "nexus-1"}


def test_parse_intent_result_passes_requester_context():
    captured = {}

    class _IntentCaptureOrchestrator:
        def run_text_to_speech_analysis(self, **kwargs):
            captured.update(kwargs)
            return {"intent": "conversation"}

    result = parse_intent_result(
        _IntentCaptureOrchestrator(),
        "hello",
        extract_json_dict,
        requester_context={"nexus_id": "nexus-7"},
    )

    assert result["intent"] == "conversation"
    assert captured["requester_context"] == {"nexus_id": "nexus-7"}


@pytest.mark.asyncio
async def test_route_task_with_context_passes_project_hint():
    called = {}

    async def fake_process(text, orchestrator, message_id, project_hint=None):
        called.update(
            {
                "text": text,
                "message_id": message_id,
                "project_hint": project_hint,
            }
        )
        return {"success": True}

    result = await route_task_with_context(
        user_id=7,
        text="route this",
        orchestrator=object(),
        message_id="42",
        get_chat=lambda _uid: {"metadata": {"project_key": "sampleco"}},
        process_inbox_task=fake_process,
    )

    assert result["success"] is True
    assert called == {"text": "route this", "message_id": "42", "project_hint": "sampleco"}
