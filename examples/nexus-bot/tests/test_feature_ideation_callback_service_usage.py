from nexus.core.feature_ideation_callback_service import _build_feature_trigger_message_id


def test_example_usage_feature_trigger_id_is_unique_by_feature_and_order():
    first = _build_feature_trigger_message_id(
        base_message_id="55",
        selected_feature={"title": "Real-Time State Streaming", "summary": "Push updates"},
        selection_order=1,
    )
    second = _build_feature_trigger_message_id(
        base_message_id="55",
        selected_feature={"title": "Visualizer Snapshot Export", "summary": "Export image"},
        selection_order=2,
    )

    assert first != second
    assert first.startswith("55-feat-1-")
    assert second.startswith("55-feat-2-")
