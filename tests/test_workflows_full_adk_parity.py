from __future__ import annotations

import asyncio

import pytest

from nexus.workflows import (
    START,
    RequestInput,
    SQLiteCheckpointStore,
    Workflow,
    node,
    tool_node,
)


def test_custom_numeric_run_id_is_rejected_to_avoid_auto_id_collision():
    @node
    async def dynamic(ctx, node_input):
        return await ctx.run_node(lambda value: value, node_input, run_id="123")

    workflow = Workflow("run_ids", edges=[(START, dynamic)])

    with pytest.raises(ValueError, match="non-numeric"):
        asyncio.run(workflow.run("hello"))


def test_tool_node_preserves_explicit_tool_kind_and_executes_callable():
    def double(value: int) -> int:
        return value * 2

    tool = tool_node(double, name="double_tool")
    workflow = Workflow("tool_graph", edges=[(START, tool)])

    result = asyncio.run(workflow.run(21))

    assert tool.kind == "tool"
    assert result.output == 42
    assert result.visited == ("double_tool",)


def test_dynamic_run_parallel_checkpoints_completed_children_on_partial_failure():
    calls = {"good": 0, "bad": 0}
    fail_bad = {"enabled": True}

    @node(name="good_child")
    async def good_child(node_input: str):
        calls["good"] += 1
        return f"good:{node_input}"

    @node(name="bad_child")
    async def bad_child(node_input: str):
        calls["bad"] += 1
        if fail_bad["enabled"]:
            raise RuntimeError("temporary failure")
        return f"bad:{node_input}"

    @node(rerun_on_resume=True)
    async def dynamic(ctx, node_input: str):
        good, bad = await ctx.run_parallel(
            [good_child, bad_child],
            [node_input, node_input],
            run_id_prefix="branch",
        )
        return f"{good}|{bad}"

    workflow = Workflow("parallel", edges=[(START, dynamic)])
    checkpoints: dict[str, object] = {}

    with pytest.raises(RuntimeError, match="temporary failure"):
        asyncio.run(workflow.run("x", checkpoints=checkpoints))

    assert calls == {"good": 1, "bad": 1}
    assert "parallel:branch-0:good_child" in checkpoints

    fail_bad["enabled"] = False
    result = asyncio.run(workflow.run("x", checkpoints=checkpoints))

    assert result.output == "good:x|bad:x"
    assert calls == {"good": 1, "bad": 2}
    assert result.records[0].node_name == "good_child"
    assert result.records[0].cached is True


def test_hitl_request_input_can_resume_with_supplied_input_and_checkpoint_answer():
    @node
    def ask_approval(ctx, plan: str):
        return RequestInput(message="Approve?", key="approval", metadata={"plan": plan})

    @node(rerun_on_resume=True)
    async def dynamic(ctx, plan: str):
        answer = await ctx.run_node(ask_approval, plan, run_id="approval-step")
        return f"approved={answer}"

    workflow = Workflow("hitl", edges=[(START, dynamic)])
    checkpoints: dict[str, object] = {}

    paused = asyncio.run(workflow.run("deploy", checkpoints=checkpoints))

    assert paused.paused is not None
    assert paused.paused.key == "approval"
    assert paused.paused.metadata == {"plan": "deploy"}

    resumed = asyncio.run(
        workflow.run("deploy", checkpoints=checkpoints, resume_inputs={"approval": "yes"})
    )
    replayed = asyncio.run(workflow.run("deploy", checkpoints=checkpoints))

    assert resumed.output == "approved=yes"
    assert replayed.output == "approved=yes"
    assert replayed.records[0].cached is True


def test_sqlite_checkpoint_store_persists_json_safe_outputs(tmp_path):
    checkpoint_path = tmp_path / "workflow.sqlite"
    calls = {"expensive": 0}

    @node(name="expensive")
    def expensive(node_input: str):
        calls["expensive"] += 1
        return {"normalized": node_input.upper()}

    @node(rerun_on_resume=True)
    async def dynamic(ctx, node_input: str):
        return await ctx.run_node(expensive, node_input, run_id="normalize")

    workflow = Workflow("sqlite", edges=[(START, dynamic)])
    first_store = SQLiteCheckpointStore(checkpoint_path)
    first = asyncio.run(workflow.run("hello", checkpoints=first_store))
    first_store.close()

    second_store = SQLiteCheckpointStore(checkpoint_path)
    second = asyncio.run(workflow.run("hello", checkpoints=second_store))
    second_store.close()

    assert first.output == {"normalized": "HELLO"}
    assert second.output == {"normalized": "HELLO"}
    assert calls["expensive"] == 1
    assert second.records[0].cached is True
