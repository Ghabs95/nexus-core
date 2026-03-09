from types import SimpleNamespace

import pytest

from nexus.core.feature_ideation_callback_service import handle_feature_ideation_callback


class _Ctx:
    def __init__(self, data: str):
        self.user_id = "1001"
        self.query = SimpleNamespace(data=data)
        self.user_state: dict[str, dict] = {
            "feature_suggestions": {
                "project": "nexus",
                "items": [
                    {
                        "title": "Real-Time State Streaming",
                        "summary": "Push state updates via websocket",
                        "why": "Live UX",
                        "steps": ["Implement stream"],
                    },
                    {
                        "title": "Visualizer Snapshot Export",
                        "summary": "Allow exporting a static snapshot",
                        "why": "Sharing",
                        "steps": ["Add export endpoint"],
                    },
                ],
                "selected_items": [],
                "source_text": "feature ideas",
                "feature_count": 2,
                "agent_type": "designer",
            }
        }
        self.raw_event = SimpleNamespace(
            message_id="55",
            message=SimpleNamespace(message_id=55),
        )
        self.edits: list[str] = []

    async def answer_callback_query(self) -> None:
        return None

    async def edit_message_text(self, text: str, buttons=None):  # noqa: ANN001
        self.edits.append(text)


@pytest.mark.asyncio
async def test_feature_pick_uses_unique_trigger_ids_per_selection():
    captured_ids: list[str] = []

    async def _create_feature_task(text, message_id, project_key, user_id=None):  # noqa: ANN001
        captured_ids.append(str(message_id))
        return {"message": f"Created for {project_key}: {text}"}

    deps = SimpleNamespace(
        logger=None,
        allowed_user_ids=[],
        projects={"nexus": "Nexus"},
        create_feature_task=_create_feature_task,
    )

    kwargs = dict(
        deps=deps,
        feature_state_key="feature_suggestions",
        is_project_locked=lambda _state: False,
        feature_project_keyboard=lambda _deps: [],
        clamp_feature_count=lambda value: int(value),
        build_feature_suggestions=lambda **_kw: [],
        feature_generation_retry_text=lambda _project, _deps: "retry",
        feature_list_text=lambda *args, **kwargs: "list",  # noqa: ARG005
        feature_list_keyboard=lambda *_a, **_k: [],
        feature_count_prompt_text=lambda *_a, **_k: "count",
        feature_count_keyboard=lambda **_k: [],
        feature_to_task_text=lambda _project, selected, _deps: selected["title"],
        log_unauthorized_callback_access=lambda *_a, **_k: None,
    )

    ctx1 = _Ctx("feat:pick:0")
    await handle_feature_ideation_callback(ctx=ctx1, **kwargs)

    ctx2 = _Ctx("feat:pick:0")
    ctx2.user_state = ctx1.user_state
    await handle_feature_ideation_callback(ctx=ctx2, **kwargs)

    assert len(captured_ids) == 2
    assert captured_ids[0] != captured_ids[1]
    assert captured_ids[0].startswith("55-feat-1-")
    assert captured_ids[1].startswith("55-feat-2-")
