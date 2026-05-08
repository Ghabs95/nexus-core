from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from nexus.workflows import (
    START,
    Event,
    JsonCheckpointStore,
    Workflow,
    WorkflowSchemaError,
    node,
    workflow_node,
)


@dataclass
class CityRequest:
    city: str


@dataclass
class CityResult:
    text: str


def test_nested_workflow_node_merges_messages_state_and_events():
    def child_step(request: CityRequest):
        return Event(message=f"child:{request.city}", state={"child_city": request.city})

    child = Workflow("child", edges=[(START, child_step)])

    def parent_done(node_input: str):
        return Event(message=f"parent:{node_input}", state={"parent_seen": True})

    embedded = workflow_node(child, input_schema=CityRequest)
    parent = Workflow("parent", edges=[(START, embedded, parent_done)])

    result = asyncio.run(parent.run({"city": "Rome"}))

    assert result.visited == ("child", "parent_done")
    assert result.output == "parent:child:Rome"
    assert result.messages == ("child:Rome", "parent:child:Rome")
    assert result.state == {"child_city": "Rome", "parent_seen": True}
    assert [event.message for event in result.events] == ["child:Rome", "parent:child:Rome"]


def test_node_input_and_output_schemas_validate_and_coerce_dataclasses():
    @node(input_schema=CityRequest, output_schema=CityResult)
    def build_result(request: CityRequest):
        return {"text": f"hello {request.city}"}

    workflow = Workflow("schemas", edges=[(START, build_result)])

    result = asyncio.run(workflow.run({"city": "Paris"}))

    assert result.output == CityResult(text="hello Paris")


def test_node_schema_validation_raises_clear_error():
    @node(input_schema=CityRequest)
    def build_result(request: CityRequest):
        return request.city

    workflow = Workflow("schemas", edges=[(START, build_result)])

    with pytest.raises(WorkflowSchemaError, match="build_result input"):
        asyncio.run(workflow.run({"not_city": "Paris"}))


def test_json_checkpoint_store_persists_successful_dynamic_children(tmp_path):
    checkpoint_file = tmp_path / "checkpoints.json"
    calls = {"expensive": 0}

    @node(name="expensive_step")
    def expensive_step(node_input: str):
        calls["expensive"] += 1
        return node_input.upper()

    @node(rerun_on_resume=True)
    async def dynamic_workflow(ctx, node_input: str):
        result = await ctx.run_node(expensive_step, node_input, run_id="stable-child")
        return f"result:{result}"

    workflow = Workflow("persistent", edges=[(START, dynamic_workflow)])

    first_store = JsonCheckpointStore(checkpoint_file)
    first = asyncio.run(workflow.run("hello", checkpoints=first_store))
    second_store = JsonCheckpointStore(checkpoint_file)
    second = asyncio.run(workflow.run("hello", checkpoints=second_store))

    assert first.output == "result:HELLO"
    assert second.output == "result:HELLO"
    assert calls["expensive"] == 1
    assert second.records[0].node_name == "expensive_step"
    assert second.records[0].cached is True


def test_generator_dynamic_node_streams_events_and_preserves_route():
    @node(rerun_on_resume=True)
    def router(node_input: str):
        yield Event(message="thinking", state={"started": True})
        yield Event(route="APPROVE")

    def approve():
        return Event(message="approved")

    workflow = Workflow("streaming", edges=[(START, router), (router, {"APPROVE": approve})])

    result = asyncio.run(workflow.run("deploy"))

    assert result.visited == ("router", "approve")
    assert result.messages == ("thinking", "approved")
    assert result.state == {"started": True}
    assert result.output == "approved"
    assert [event.route for event in result.events] == [None, "APPROVE", None]
