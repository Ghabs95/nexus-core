"""Collaborative agent helpers for Nexus ARC workflows."""

from __future__ import annotations

from collections.abc import Iterable

from nexus.agents.base import BaseAgent

from .nodes import AgentNode, NodeMode


def collaborative_agent(agent: BaseAgent, *, mode: NodeMode = "chat") -> AgentNode:
    """Wrap a Nexus agent with ADK-style collaboration mode metadata."""
    return AgentNode(agent, mode=mode)


def single_turn_agents(agents: Iterable[BaseAgent]) -> list[AgentNode]:
    """Create parallel-safe single-turn subagent nodes."""
    return [collaborative_agent(agent, mode="single_turn") for agent in agents]
