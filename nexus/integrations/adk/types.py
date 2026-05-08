"""
ADK-style neutral workflow specs for Nexus ARC interoperability.

These dataclasses intentionally do not import Google ADK. They describe the
stable ARC-owned boundary we can later translate into ADK runtime objects,
MCP/A2A payloads, or documentation artifacts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

AdkNodeKind = Literal["agent", "sequential", "parallel", "loop", "coordinator"]
AdkEdgeKind = Literal["next", "branch", "loop", "delegates"]


@dataclass(frozen=True)
class AdkNodeSpec:
    """A node in an ADK-style workflow graph."""

    id: str
    kind: AdkNodeKind
    name: str
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdkEdgeSpec:
    """A directed edge between ADK-style workflow nodes."""

    source: str
    target: str
    kind: AdkEdgeKind = "next"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdkWorkflowSpec:
    """ARC-owned neutral representation of an ADK-style workflow."""

    name: str
    entrypoint: str
    nodes: tuple[AdkNodeSpec, ...]
    edges: tuple[AdkEdgeSpec, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def node_by_id(self, node_id: str) -> AdkNodeSpec | None:
        """Return a node by id, if present."""
        return next((node for node in self.nodes if node.id == node_id), None)
