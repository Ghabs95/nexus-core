"""Workflow node primitives for Nexus ARC.

These are ADK-inspired but provider-neutral. Agent execution still goes through
Nexus ARC agents/providers/router; function and tool nodes are deterministic
Python callables.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from nexus.agents.base import AgentContext, BaseAgent

NodeMode = Literal["chat", "task", "single_turn"]
NodeKind = Literal["agent", "function", "dynamic", "tool", "workflow"]


@dataclass(eq=False)
class BaseNode:
    """Executable workflow node."""

    name: str
    kind: NodeKind
    input_schema: type | None = None
    output_schema: type | None = None
    rerun_on_resume: bool = False
    mode: NodeMode | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    async def run(self, ctx: Any, node_input: Any = None) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    def __hash__(self) -> int:
        return id(self)


@dataclass(eq=False)
class FunctionNode(BaseNode):
    """Wrap a sync/async Python callable as a workflow node."""

    func: Callable[..., Any] | None = None

    def __init__(
        self,
        func: Callable[..., Any],
        *,
        name: str | None = None,
        input_schema: type | None = None,
        output_schema: type | None = None,
        rerun_on_resume: bool = False,
        kind: NodeKind = "function",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name or getattr(func, "__name__", "function_node"),
            kind=kind,
            input_schema=input_schema,
            output_schema=output_schema,
            rerun_on_resume=rerun_on_resume,
            metadata=metadata or {},
        )
        self.func = func

    async def run(self, ctx: Any, node_input: Any = None) -> Any:
        assert self.func is not None
        result = _call_with_supported_args(self.func, ctx, node_input)
        if inspect.isawaitable(result):
            result = await result
        return result


@dataclass(eq=False)
class ToolNode(FunctionNode):
    """Wrap a callable as an explicit tool node.

    Tool nodes behave like function nodes at runtime, but preserve `kind="tool"`
    for graph introspection, docs, and future MCP/A2A export.
    """

    def __init__(
        self,
        tool: Callable[..., Any],
        *,
        name: str | None = None,
        input_schema: type | None = None,
        output_schema: type | None = None,
        rerun_on_resume: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            tool,
            name=name,
            input_schema=input_schema,
            output_schema=output_schema,
            rerun_on_resume=rerun_on_resume,
            kind="tool",
            metadata=metadata,
        )


@dataclass(eq=False)
class AgentNode(BaseNode):
    """Wrap a Nexus ARC BaseAgent as a workflow node."""

    agent: BaseAgent | None = None

    def __init__(
        self,
        agent: BaseAgent,
        *,
        mode: NodeMode | None = None,
        input_schema: type | None = None,
        output_schema: type | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if mode in {"task", "single_turn"} and getattr(agent, "sub_agents", None):
            raise ValueError("task/single_turn collaboration agents must be leaf agents")
        super().__init__(
            name=agent.name,
            kind="agent",
            input_schema=input_schema,
            output_schema=output_schema,
            mode=mode,
            metadata={"description": agent.description, **(metadata or {})},
        )
        self.agent = agent

    async def run(self, ctx: Any, node_input: Any = None) -> Any:
        assert self.agent is not None
        task = str(node_input if node_input is not None else getattr(ctx, "root_input", ""))
        metadata = dict(getattr(ctx, "metadata", {}) or {})
        if self.mode:
            metadata["collaboration_mode"] = self.mode
        output = await self.agent.run(AgentContext(task=task, metadata=metadata))
        return output.content


@dataclass(eq=False)
class WorkflowNode(BaseNode):
    """Wrap a nested Nexus workflow as a node."""

    workflow: Any = None

    def __init__(
        self,
        workflow: Any,
        *,
        name: str | None = None,
        input_schema: type | None = None,
        output_schema: type | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name or getattr(workflow, "name", "workflow_node"),
            kind="workflow",
            input_schema=input_schema,
            output_schema=output_schema,
            metadata=metadata or {},
        )
        self.workflow = workflow

    async def run(self, ctx: Any, node_input: Any = None) -> Any:
        assert self.workflow is not None
        result = await self.workflow.run(
            node_input,
            metadata=getattr(ctx, "metadata", None),
            checkpoints=getattr(ctx, "_checkpoints", None),
            resume_inputs=getattr(ctx, "_resume_inputs", None),
        )
        if result.paused is not None:
            return result.paused
        if hasattr(ctx, "messages"):
            ctx.messages.extend(result.messages)
        if hasattr(ctx, "events"):
            ctx.events.extend(result.events)
        if hasattr(ctx, "state"):
            ctx.state.update(dict(result.state))
        return result.output


def node(
    func: Callable[..., Any] | None = None,
    *,
    name: str | None = None,
    rerun_on_resume: bool = False,
    input_schema: type | None = None,
    output_schema: type | None = None,
):
    """Decorator/wrapper for dynamic workflow nodes."""

    def wrap(target: Callable[..., Any]) -> FunctionNode:
        return FunctionNode(
            target,
            name=name,
            input_schema=input_schema,
            output_schema=output_schema,
            rerun_on_resume=rerun_on_resume,
            kind="dynamic",
        )

    if func is None:
        return wrap
    return wrap(func)


def tool_node(
    tool: Callable[..., Any],
    *,
    name: str | None = None,
    input_schema: type | None = None,
    output_schema: type | None = None,
    rerun_on_resume: bool = False,
) -> ToolNode:
    """Explicit helper for placing a tool/callable in a workflow graph."""
    return ToolNode(
        tool,
        name=name,
        input_schema=input_schema,
        output_schema=output_schema,
        rerun_on_resume=rerun_on_resume,
    )


def workflow_node(
    workflow: Any,
    *,
    name: str | None = None,
    input_schema: type | None = None,
    output_schema: type | None = None,
) -> WorkflowNode:
    """Explicit helper for embedding a workflow inside another workflow."""
    return WorkflowNode(
        workflow,
        name=name,
        input_schema=input_schema,
        output_schema=output_schema,
    )


def as_node(value: Any) -> BaseNode:
    """Normalize agents/functions/workflows/already-wrapped nodes to BaseNode."""
    if isinstance(value, BaseNode):
        return value
    if _looks_like_workflow(value):
        return WorkflowNode(value)
    if isinstance(value, BaseAgent):
        return AgentNode(value)
    if callable(value):
        return FunctionNode(value)
    raise TypeError(f"unsupported workflow node: {value!r}")


def _call_with_supported_args(func: Callable[..., Any], ctx: Any, node_input: Any) -> Any:
    sig = inspect.signature(func)
    params = list(sig.parameters.values())
    accepts_ctx = bool(params) and params[0].name in {"ctx", "context"}
    if accepts_ctx:
        if len(params) == 1:
            return func(ctx)
        return func(ctx, node_input)
    if len(params) == 0:
        return func()
    return func(node_input)


def _looks_like_workflow(value: Any) -> bool:
    return hasattr(value, "run") and hasattr(value, "edges") and hasattr(value, "name")
