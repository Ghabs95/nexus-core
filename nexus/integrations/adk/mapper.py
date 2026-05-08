"""Mapping helpers from Nexus ARC agents to ADK-style neutral specs."""

from __future__ import annotations

import re
from collections.abc import Iterable

from nexus.agents.base import BaseAgent
from nexus.agents.coordinator import Coordinator
from nexus.agents.loop import LoopAgent
from nexus.agents.parallel import ParallelAgent
from nexus.agents.sequential import SequentialAgent
from nexus.integrations.adk.types import AdkEdgeSpec, AdkNodeSpec, AdkWorkflowSpec


def export_agent_to_adk_spec(agent: BaseAgent) -> AdkWorkflowSpec:
    """Export a supported ARC agent/composition to an ADK-style workflow spec.

    v0 supports SequentialAgent, ParallelAgent, LoopAgent, Coordinator, and
    leaf sub-agents. The returned spec is ARC-owned and does not require Google ADK.
    """
    nodes: list[AdkNodeSpec] = []
    edges: list[AdkEdgeSpec] = []
    used_ids: set[str] = set()
    entrypoint = _walk_agent(agent, nodes=nodes, edges=edges, used_ids=used_ids)
    return AdkWorkflowSpec(
        name=agent.name,
        entrypoint=entrypoint,
        nodes=tuple(nodes),
        edges=tuple(edges),
        metadata={"origin": "arc", "format": "adk-style-neutral", "version": 0},
    )


def _walk_agent(
    agent: BaseAgent,
    *,
    nodes: list[AdkNodeSpec],
    edges: list[AdkEdgeSpec],
    used_ids: set[str],
) -> str:
    node_id = _unique_id(agent.name, used_ids)

    if isinstance(agent, SequentialAgent):
        nodes.append(_node_for_agent(agent, node_id, "sequential"))
        child_ids = [
            _walk_agent(child, nodes=nodes, edges=edges, used_ids=used_ids)
            for child in agent.sub_agents
        ]
        for child_id in child_ids:
            edges.append(AdkEdgeSpec(source=node_id, target=child_id, kind="next"))
        for source, target in _pairwise(child_ids):
            edges.append(
                AdkEdgeSpec(source=source, target=target, kind="next", metadata={"sequence": True})
            )
        return node_id

    if isinstance(agent, ParallelAgent):
        nodes.append(
            _node_for_agent(
                agent,
                node_id,
                "parallel",
                metadata={"merge_strategy": agent.merge_strategy, "separator": agent.separator},
            )
        )
        child_ids = [
            _walk_agent(child, nodes=nodes, edges=edges, used_ids=used_ids)
            for child in agent.sub_agents
        ]
        for child_id in child_ids:
            edges.append(AdkEdgeSpec(source=node_id, target=child_id, kind="branch"))
        return node_id

    if isinstance(agent, LoopAgent):
        nodes.append(
            _node_for_agent(
                agent,
                node_id,
                "loop",
                metadata={
                    "max_iterations": agent.max_iterations,
                    "stop_condition_exported": False,
                    "unsupported": ["python_stop_condition"],
                },
            )
        )
        child_id = _walk_agent(agent.sub_agent, nodes=nodes, edges=edges, used_ids=used_ids)
        edges.append(AdkEdgeSpec(source=node_id, target=child_id, kind="loop"))
        return node_id

    if isinstance(agent, Coordinator):
        nodes.append(
            _node_for_agent(
                agent,
                node_id,
                "coordinator",
                metadata={"router_url": agent.router_url, "workspace_path": agent.workspace_path},
            )
        )
        child_ids = [
            _walk_agent(child, nodes=nodes, edges=edges, used_ids=used_ids)
            for child in agent.sub_agents
        ]
        for child_id in child_ids:
            edges.append(AdkEdgeSpec(source=node_id, target=child_id, kind="delegates"))
        return node_id

    nodes.append(_node_for_agent(agent, node_id, "agent"))
    return node_id


def _node_for_agent(
    agent: BaseAgent, node_id: str, kind: str, metadata: dict | None = None
) -> AdkNodeSpec:
    return AdkNodeSpec(
        id=node_id,
        kind=kind,  # type: ignore[arg-type]
        name=agent.name,
        description=agent.description,
        metadata={"arc_class": type(agent).__name__, **(metadata or {})},
    )


def _unique_id(name: str, used_ids: set[str]) -> str:
    base = _slug(name) or "agent"
    candidate = base
    index = 2
    while candidate in used_ids:
        candidate = f"{base}-{index}"
        index += 1
    used_ids.add(candidate)
    return candidate


def _slug(value: str) -> str:
    return re.sub(r"(^-|-$)", "", re.sub(r"[^a-z0-9]+", "-", value.lower()))


def _pairwise(values: Iterable[str]) -> Iterable[tuple[str, str]]:
    items = list(values)
    return zip(items, items[1:], strict=False)
