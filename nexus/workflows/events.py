"""Provider-neutral workflow events for Nexus ARC graph/dynamic workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Event:
    """Workflow event emitted by a node.

    Mirrors the useful ADK 2.0 concepts without depending on Google ADK:
    - message: user/operator-visible message
    - route: one or more route labels to follow from the current node
    - state: state patch emitted by a dynamic workflow
    """

    message: str | None = None
    route: str | list[str] | None = None
    state: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RequestInput:
    """Pause a dynamic workflow and request human input before continuing."""

    message: str
    key: str = "input"
    metadata: dict[str, Any] = field(default_factory=dict)
