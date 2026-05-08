"""Provider-neutral graph workflow runtime for Nexus ARC.

Implements the ADK 2.0 workflow ideas Nexus needs: START edges, sequential graph
chains, route dictionaries, function/tool/agent/nested-workflow nodes, typed
payload validation, deterministic execution records, HITL resume inputs,
parallel child execution, and checkpointable dynamic nodes. It does not depend
on Google ADK or Gemini.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .events import Event, RequestInput
from .nodes import BaseNode, as_node
from .schema import validate_payload

START = "START"


@dataclass(frozen=True)
class WorkflowResult:
    output: Any
    messages: tuple[str, ...] = ()
    state: dict[str, Any] = field(default_factory=dict)
    visited: tuple[str, ...] = ()
    paused: RequestInput | None = None
    events: tuple[Event, ...] = ()
    records: tuple[WorkflowRunRecord, ...] = ()


@dataclass(frozen=True)
class WorkflowRunRecord:
    node_name: str
    run_id: str
    output: Any
    cached: bool = False


class WorkflowPausedError(Exception):
    """Internal control-flow exception for HITL pauses."""

    def __init__(self, request: RequestInput) -> None:
        super().__init__(request.message)
        self.request = request


class WorkflowContext:
    """Execution context for graph and dynamic workflows."""

    def __init__(
        self,
        *,
        workflow_name: str,
        root_input: Any = None,
        metadata: dict[str, Any] | None = None,
        checkpoints: MutableMapping[str, Any] | None = None,
        resume_inputs: dict[str, Any] | None = None,
    ) -> None:
        self.workflow_name = workflow_name
        self.root_input = root_input
        self.metadata = dict(metadata or {})
        self.state: dict[str, Any] = {}
        self.messages: list[str] = []
        self.events: list[Event] = []
        self.records: list[WorkflowRunRecord] = []
        self._checkpoints = checkpoints if checkpoints is not None else {}
        self._resume_inputs = dict(resume_inputs or {})
        self._counter = 0

    async def run_node(
        self, node_ref: Any, node_input: Any = None, *, run_id: str | None = None
    ) -> Any:
        """Run a child node with deterministic checkpoint-aware execution IDs.

        Custom run IDs must include at least one non-numeric character. Numeric
        IDs are reserved for Nexus' auto-generated sequence to avoid collisions.
        """
        node = as_node(node_ref)
        if run_id is not None and _is_reserved_numeric_run_id(run_id):
            raise ValueError("custom run_id must include at least one non-numeric character")
        effective_run_id = run_id or self._next_run_id()
        if not _valid_run_id(effective_run_id):
            raise ValueError("run_id must be non-empty")
        key = f"{self.workflow_name}:{effective_run_id}:{node.name}"
        if key in self._checkpoints and not node.rerun_on_resume:
            output = self._checkpoints[key]
            self.records.append(
                WorkflowRunRecord(
                    node_name=node.name,
                    run_id=effective_run_id,
                    output=output,
                    cached=True,
                )
            )
            return output

        validated_input = validate_payload(
            node.input_schema,
            node_input,
            node_name=node.name,
            direction="input",
        )
        output = await _execute_node(node, self, validated_input)
        if isinstance(output, RequestInput):
            if output.key in self._resume_inputs:
                output = self._resume_inputs[output.key]
            else:
                raise WorkflowPausedError(output)
        output = validate_payload(
            node.output_schema,
            output,
            node_name=node.name,
            direction="output",
        )
        self._checkpoints[key] = output
        self.records.append(
            WorkflowRunRecord(node_name=node.name, run_id=effective_run_id, output=output)
        )
        return output

    async def run_parallel(
        self,
        node_refs: Sequence[Any],
        node_inputs: Sequence[Any] | None = None,
        *,
        run_ids: Sequence[str] | None = None,
        run_id_prefix: str = "parallel",
    ) -> list[Any]:
        """Run child nodes concurrently with deterministic checkpoint keys."""
        inputs = list(node_inputs) if node_inputs is not None else [None] * len(node_refs)
        if len(inputs) != len(node_refs):
            raise ValueError("node_inputs length must match node_refs length")
        if run_ids is not None and len(run_ids) != len(node_refs):
            raise ValueError("run_ids length must match node_refs length")
        effective_ids = (
            list(run_ids)
            if run_ids is not None
            else [f"{run_id_prefix}-{index}" for index in range(len(node_refs))]
        )
        return list(
            await asyncio.gather(
                *[
                    self.run_node(node_ref, node_input, run_id=effective_id)
                    for node_ref, node_input, effective_id in zip(
                        node_refs, inputs, effective_ids, strict=True
                    )
                ]
            )
        )

    def _next_run_id(self) -> str:
        self._counter += 1
        return str(self._counter)


class Workflow:
    """Graph workflow container with ADK-like edges."""

    def __init__(self, name: str, edges: Sequence[Any], metadata: dict[str, Any] | None = None):
        self.name = name
        self.edges = list(edges)
        self.metadata = dict(metadata or {})
        self._linear_edges: list[tuple[Any, Any]] = []
        self._route_edges: dict[Any, dict[str, Any]] = {}
        self._compile_edges(edges)

    async def run(
        self,
        node_input: Any = None,
        *,
        metadata: dict[str, Any] | None = None,
        checkpoints: MutableMapping[str, Any] | None = None,
        resume_inputs: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        ctx = WorkflowContext(
            workflow_name=self.name,
            root_input=node_input,
            metadata={**self.metadata, **dict(metadata or {})},
            checkpoints=checkpoints,
            resume_inputs=resume_inputs,
        )
        current = self._first_after_start()
        payload = node_input
        visited: list[str] = []

        try:
            while current is not None:
                node = as_node(current)
                visited.append(node.name)
                output = await ctx.run_node(node, payload)
                if isinstance(output, Event) and ctx.events and ctx.events[-1] is output:
                    payload = _event_payload(output)
                else:
                    payload = _apply_event(ctx, output)
                current = self._next_node(current, output)
        except WorkflowPausedError as pause:
            return _result(ctx, output=None, visited=visited, paused=pause.request)

        return _result(ctx, output=payload, visited=visited)

    def _compile_edges(self, edges: Sequence[Any]) -> None:
        for edge in edges:
            if not isinstance(edge, tuple) or len(edge) < 2:
                raise ValueError(f"workflow edge must be a tuple with at least two items: {edge!r}")
            if len(edge) == 2 and isinstance(edge[1], dict):
                self._route_edges[edge[0]] = {str(k): v for k, v in edge[1].items()}
                continue
            for source, target in zip(edge, edge[1:], strict=False):
                if isinstance(target, dict):
                    self._route_edges[source] = {str(k): v for k, v in target.items()}
                else:
                    self._linear_edges.append((source, target))

    def _first_after_start(self) -> Any | None:
        for source, target in self._linear_edges:
            if source == START:
                return target
        return None

    def _next_node(self, current: Any, output: Any) -> Any | None:
        event = output if isinstance(output, Event) else None
        if event and event.route is not None and current in self._route_edges:
            routes = event.route if isinstance(event.route, list) else [event.route]
            route_map = self._route_edges[current]
            for route in routes:
                if str(route) in route_map:
                    return route_map[str(route)]
            return None
        for source, target in self._linear_edges:
            if source is current:
                return target
        return None


def run_workflow_sync(workflow: Workflow, node_input: Any = None) -> WorkflowResult:
    """Convenience sync runner for tests and CLI glue."""
    import asyncio

    return asyncio.run(workflow.run(node_input))


async def _execute_node(node: BaseNode, ctx: WorkflowContext, node_input: Any) -> Any:
    output = await node.run(ctx, node_input)
    if inspect.isasyncgen(output):
        final_item = None
        async for item in output:
            _apply_event(ctx, item)
            final_item = item
            if isinstance(item, RequestInput):
                return item
        return final_item
    if inspect.isgenerator(output):
        final_item = None
        for item in output:
            _apply_event(ctx, item)
            final_item = item
            if isinstance(item, RequestInput):
                return item
        return final_item
    return output


def _apply_event(ctx: WorkflowContext, output: Any) -> Any:
    if isinstance(output, Event):
        ctx.events.append(output)
        if output.message:
            ctx.messages.append(output.message)
        if output.state:
            ctx.state.update(output.state)
        return _event_payload(output)
    return output


def _event_payload(output: Event) -> Any:
    return output.message if output.message is not None else output.state or output.route


def _result(
    ctx: WorkflowContext,
    *,
    output: Any,
    visited: list[str],
    paused: RequestInput | None = None,
) -> WorkflowResult:
    return WorkflowResult(
        output=output,
        messages=tuple(ctx.messages),
        state=dict(ctx.state),
        visited=tuple(visited),
        paused=paused,
        events=tuple(ctx.events),
        records=tuple(ctx.records),
    )


def _valid_run_id(run_id: str) -> bool:
    return bool(str(run_id).strip())


def _is_reserved_numeric_run_id(run_id: str) -> bool:
    return str(run_id).strip().isdigit()
