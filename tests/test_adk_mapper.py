from __future__ import annotations

from nexus.agents.base import AgentContext, AgentOutput, BaseAgent
from nexus.agents.coordinator import Coordinator
from nexus.agents.loop import LoopAgent
from nexus.agents.parallel import ParallelAgent
from nexus.agents.sequential import SequentialAgent
from nexus.integrations.adk.mapper import export_agent_to_adk_spec


class StubAgent(BaseAgent):
    async def run(self, context: AgentContext) -> AgentOutput:
        return AgentOutput(content=self.name)


def test_exports_sequential_agent_to_adk_style_spec():
    seq = SequentialAgent(
        "Review Pipeline",
        [StubAgent("Design", "Draft design"), StubAgent("Review", "Review design")],
        description="Sequential review pipeline",
    )

    spec = export_agent_to_adk_spec(seq)

    assert spec.name == "Review Pipeline"
    assert spec.entrypoint == "review-pipeline"
    assert spec.metadata["origin"] == "arc"
    assert [(node.id, node.kind, node.name) for node in spec.nodes] == [
        ("review-pipeline", "sequential", "Review Pipeline"),
        ("design", "agent", "Design"),
        ("review", "agent", "Review"),
    ]
    assert [(edge.source, edge.target, edge.kind) for edge in spec.edges] == [
        ("review-pipeline", "design", "next"),
        ("review-pipeline", "review", "next"),
        ("design", "review", "next"),
    ]


def test_exports_parallel_agent_to_adk_style_spec_with_merge_metadata():
    par = ParallelAgent(
        "Parallel Checks",
        [StubAgent("Security"), StubAgent("QA")],
        merge_strategy="concat",
        separator="\n--\n",
    )

    spec = export_agent_to_adk_spec(par)

    root = spec.node_by_id("parallel-checks")
    assert root is not None
    assert root.kind == "parallel"
    assert root.metadata["merge_strategy"] == "concat"
    assert root.metadata["separator"] == "\n--\n"
    assert [(edge.source, edge.target, edge.kind) for edge in spec.edges] == [
        ("parallel-checks", "security", "branch"),
        ("parallel-checks", "qa", "branch"),
    ]


def test_exports_loop_agent_to_adk_style_spec_with_bounded_metadata():
    loop = LoopAgent(
        "Refine Loop",
        StubAgent("Reviewer"),
        stop_condition=lambda output: "done" in output.content,
        max_iterations=3,
    )

    spec = export_agent_to_adk_spec(loop)

    root = spec.node_by_id("refine-loop")
    assert root is not None
    assert root.kind == "loop"
    assert root.metadata["max_iterations"] == 3
    assert root.metadata["stop_condition_exported"] is False
    assert root.metadata["unsupported"] == ["python_stop_condition"]
    assert [(edge.source, edge.target, edge.kind) for edge in spec.edges] == [
        ("refine-loop", "reviewer", "loop"),
    ]


def test_exports_coordinator_to_adk_style_spec_with_delegation_edges():
    coordinator = Coordinator(
        "Coordinator",
        [StubAgent("Planner"), StubAgent("Reviewer")],
        ai_provider=object(),
        router_url="http://router.local",
        workspace_path="/workspace",
    )

    spec = export_agent_to_adk_spec(coordinator)

    root = spec.node_by_id("coordinator")
    assert root is not None
    assert root.kind == "coordinator"
    assert root.metadata["router_url"] == "http://router.local"
    assert root.metadata["workspace_path"] == "/workspace"
    assert [(edge.source, edge.target, edge.kind) for edge in spec.edges] == [
        ("coordinator", "planner", "delegates"),
        ("coordinator", "reviewer", "delegates"),
    ]


def test_export_disambiguates_duplicate_agent_names():
    seq = SequentialAgent("Pipeline", [StubAgent("Review"), StubAgent("Review")])

    spec = export_agent_to_adk_spec(seq)

    assert [node.id for node in spec.nodes] == ["pipeline", "review", "review-2"]
