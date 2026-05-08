# DESIGN: Provider-Neutral ADK-Style Workflows for Nexus ARC

**Status:** Draft  
**Date:** 2026-05-06  
**Related issue:** [#151](https://github.com/ghabs-org/nexus-arc/issues/151)  
**Related ADR:** `docs/adr/ADR-001-multi-agent-composition.md`  
**Decision owner:** Nexus ARC maintainers

## Summary

See also: `docs/WORKFLOWS.md` for practical usage examples.

Nexus ARC should replicate the useful ADK 2.0 workflow architecture as a **provider-neutral Nexus runtime**, not merely interoperate with Google ADK and not depend on Gemini-only execution.

Issue #151 already established the correct ARC-native foundation:

- `nexus/agents/` owns programmatic composition primitives.
- `Coordinator` owns LLM-driven delegation across ARC sub-agents.
- `nexus-router` owns model selection only.
- Nexus Workflow owns DB/Git-backed auditability.
- OpenClaw owns conversational invocation and session context.

ADK 2.0 is useful because it brings external ecosystem alignment around graph workflows, collaborative agents, MCP, A2A, and resumable dynamic workflows. The integration should let ARC **exchange**, **call**, and **be called by** ADK-style systems while preserving ARC's provider, router, workflow, and audit boundaries.

## Context

Google ADK 2.0 introduces concepts that overlap with ARC's post-#151 direction:

- graph-oriented workflows
- collaborative coordinator/sub-agent teams
- dynamic/checkpointable workflows
- MCP tool integration
- A2A agent interoperability

Those capabilities are strategically relevant, but ADK 2.0 is still a moving external runtime. ARC should therefore avoid coupling its core state model or workflow semantics to ADK internals.

The durable opportunity is to implement ADK-like workflow semantics natively in ARC:

1. graph-based workflows with `START`, nodes, edges, route events, and deterministic code/tool/agent nodes
2. collaborative agents with `chat`, `task`, and `single_turn` sub-agent modes
3. dynamic workflows with a `@node` decorator, `ctx.run_node(...)`, loops/conditionals, HITL pauses, deterministic execution IDs, and checkpoint-aware resume
4. optional Google ADK import/export only after Nexus owns the provider-neutral runtime

## Decision

Adopt a **provider-neutral ADK-style workflow runtime** with these boundaries:

- ARC remains source of truth for native composition.
- ADK support starts by implementing equivalent workflow primitives in `nexus/workflows/`.
- Google ADK runtime integration remains optional import/export glue under `nexus/integrations/adk/`.
- Protocol integrations (MCP/A2A) remain valuable, but they sit on top of the Nexus workflow runtime rather than replacing it.

## Architectural boundary

```text
OpenClaw / Nexus Workflow
        │
        ▼
   nexus/agents/              ← ARC source of truth
        │
   Coordinator
        │
   nexus-router               ← model selection only
        │
  ┌──────────── Interop Layer ────────────┐
  │                                       │
  │  ADK spec mapper                      │
  │  ADK runtime adapter                  │
  │  MCP bridge                           │
  │  A2A bridge                           │
  │                                       │
  └───────────────────────────────────────┘
```


## Required ADK 2.0 semantic coverage

The Nexus implementation must cover these ADK 2.0 surfaces in a provider-neutral way:

### Graph-based workflows

- `Workflow(name, edges=[...])` container
- `START` entry edges
- sequential tuple edges such as `(START, node_a, node_b, node_c)`
- route dictionary edges such as `(router_node, {"BUG": bug_handler})`
- node types for ARC agents, Python functions/tools, and nested workflow nodes
- `Event(message=...)`, `Event(route=...)`, and state patches
- typed input/output payload pass-through without Gemini assumptions

### Collaborative agents

- coordinator/sub-agent teams remain ARC-owned
- sub-agent modes: `chat`, `task`, `single_turn`
- `task` and `single_turn` agents auto-return to parent
- `single_turn` agents are parallel-safe and run with isolated branch context
- `task`/`single_turn` mode agents must be leaf agents initially

### Dynamic workflows

- `@node(...)` decorator and explicit `FunctionNode` wrappers
- dynamic parent nodes that call `await ctx.run_node(...)`
- support for normal Python control flow: loops, conditionals, recursion where safe
- checkpoint-aware execution records so successful child nodes are skipped on resume
- deterministic run IDs with optional custom run IDs
- human-input pauses via `RequestInput`
- yielded `Event(state=...)` patches from dynamic nodes

The implementation lives in `nexus/workflows/`; Google ADK import/export, if added, lives in `nexus/integrations/adk/`.


## Implemented deeper runtime features — 2026-05-08

The first deeper pass adds the following Nexus-native runtime features:

- nested workflow nodes via `WorkflowNode` / `workflow_node(...)`
- lightweight input/output schema validation with dataclass and Pydantic-style model support
- `WorkflowResult.events` and `WorkflowResult.records` for event streams and execution traces
- checkpoint cache-hit records via `WorkflowRunRecord.cached`
- JSON-backed persistent checkpoints via `JsonCheckpointStore`
- generator/async-generator dynamic nodes that can yield `Event(...)` updates and preserve final route events

These features remain provider-neutral and do not import Google ADK.

## Full-parity follow-up features — 2026-05-08

The next pass closed the remaining ADK workflow semantic gaps identified for Nexus-native parity:

- custom `run_id` validation reserves all-numeric IDs for auto-generated sequence IDs
- explicit `ToolNode` / `tool_node(...)` support
- `ctx.run_parallel(...)` for durable parallel child execution with deterministic checkpoint keys
- `RequestInput` resume support through `Workflow.run(..., resume_inputs={...})`
- SQLite-backed persistent checkpoints via `SQLiteCheckpointStore`

These are still Nexus-owned primitives; no Google ADK runtime dependency was introduced.

## Invariants

The integration must preserve these invariants:

1. **ARC-native primitives remain canonical.** `SequentialAgent`, `ParallelAgent`, `LoopAgent`, and `Coordinator` are not wrappers around ADK.
2. **`AIProvider` remains authoritative for model execution.** ADK cannot bypass ARC's provider abstraction when running inside ARC.
3. **`nexus-router` remains model-selection-only.** It does not spawn agents, own workflow state, or manage context passing.
4. **Nexus Workflow remains the audit boundary.** DB state, Git artifacts, issue comments, and traceability stay ARC-owned.
5. **OpenClaw session semantics stay outside ADK.** Conversational state is translated into bounded `AgentContext`, not handed wholesale to ADK.
6. **Interop code is optional.** ARC must continue working without ADK installed.

## Explicit non-goals

The ADK 2.0 integration should **not**:

- make ARC depend on Vertex/Gemini assumptions
- replace `nexus/agents/`
- replace `AIProvider`
- replace `nexus-router`
- move workflow truth/state into ADK
- let ADK own model routing or provider selection
- replace ARC's Git artifact and DB-backed audit model
- require OpenClaw to understand ADK internals

## Primitive mapping

| ARC primitive | ADK 2.0 analogue | Integration stance |
|---|---|---|
| `SequentialAgent` | workflow/graph chain | direct mapping |
| `ParallelAgent` | parallel branches / agent team fan-out | direct mapping |
| `LoopAgent` | dynamic workflow loop / refinement loop | partial mapping; bounded loops only at first |
| `Coordinator` | collaborative coordinator | semantic mapping, not 1:1 |
| `CompositeStep` | embedded graph section inside workflow | ARC-owned wrapper |
| `nexus-router` | none | ARC-local dependency, not exported as coordinator |
| `AgentContext` | workflow invocation context | translated/sliced, not copied wholesale |
| `AgentOutput` | node/team result | normalized result envelope |

## Recommended implementation phases

### Phase 1 — Spec alignment and mapper-only export

Create a stable Nexus-side representation before adding a runtime dependency.

Deliverables:

- `nexus/integrations/adk/types.py`
- `nexus/integrations/adk/mapper.py`
- unit tests for mapper behavior
- this design document

Scope:

- define ARC → ADK mapping
- define ADK → ARC import subset, but do not implement full import yet
- mark unsupported constructs explicitly
- define metadata needed for checkpoints, workflow ids, and provenance

### Phase 2 — Protocol-first integration

Implement **MCP** and **A2A** bridges before full ADK runtime execution.

Why first:

- lower coupling
- useful beyond ADK
- likely to remain stable longer than ADK beta runtime internals

#### MCP bridge

ARC should be able to:

- consume MCP tools from external systems
- expose selected ARC capabilities as MCP-callable tools

Candidate ARC tools to expose:

- workflow status lookup
- workflow launch
- issue/work item inspection
- artifact retrieval

#### A2A bridge

ARC should be able to:

- expose a Nexus agent/composition as an A2A-capable remote agent
- delegate a sub-task to a remote A2A agent and normalize the result into `AgentOutput`

### Phase 3 — Optional ADK runtime adapter

Add an adapter for executing an ADK workflow/team as a delegated backend.

Candidate module shape:

- `nexus/integrations/adk/runner.py`
- `nexus/integrations/adk/mapper.py`
- `nexus/integrations/adk/types.py`

Responsibilities:

- translate ARC `AgentContext` → ADK input
- execute ADK workflow/team
- collect result/checkpoint/error state
- normalize ADK output → ARC `AgentOutput`

Non-responsibilities:

- no direct Git artifact writing
- no provider/routing ownership
- no mutation of ARC workflow truth

## Initial import/export scope

Keep the first supported subset intentionally small.

### ARC → ADK export v0

Support:

- `SequentialAgent`
- `ParallelAgent`
- simple coordinator-to-subagent delegation metadata
- bounded `LoopAgent` metadata, without executable stop-condition export

Do not support yet:

- arbitrary Python stop conditions
- ARC-specific persistence policies
- Git artifact hooks
- OpenClaw-specific conversational state
- provider-specific runtime settings that bypass `AIProvider`

### ADK → ARC import v0

Support later:

- basic graph nodes that map to ARC sub-agents
- simple edges
- parallel fan-out / fan-in where merge semantics are explicit

Do not support yet:

- ADK-only runtime features with no ARC analogue
- opaque stateful middleware
- provider-specific prompt/runtime settings that bypass `AIProvider`
- unbounded dynamic loops

## Data model guidance

The interop layer should preserve these fields across boundaries when possible:

- task id / workflow id
- parent workflow id
- checkpoint id
- agent name
- agent role / description
- provenance (`origin = arc|adk|remote-a2a|mcp`)
- model hint requested
- model actually used
- resumability metadata
- structured error category
- unsupported-feature notes

## Proposed package layout

```text
nexus/integrations/
  __init__.py
  adk/
    __init__.py
    types.py      # ADK-neutral graph/spec dataclasses
    mapper.py     # ARC → ADK-spec export, later ADK-spec → ARC import
    runner.py     # optional future ADK runtime execution adapter
```

The first slice should add only `types.py`, `mapper.py`, and tests.

## Mapping model v0

The v0 mapper should export to an ARC-owned neutral spec, not directly to ADK Python objects. This avoids taking a hard dependency on ADK 2.0 while its API is still moving.

Suggested shape:

```python
@dataclass(frozen=True)
class AdkNodeSpec:
    id: str
    kind: Literal["agent", "sequential", "parallel", "loop", "coordinator"]
    name: str
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class AdkEdgeSpec:
    source: str
    target: str
    kind: Literal["next", "branch", "loop", "delegates"] = "next"

@dataclass(frozen=True)
class AdkWorkflowSpec:
    name: str
    entrypoint: str
    nodes: tuple[AdkNodeSpec, ...]
    edges: tuple[AdkEdgeSpec, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
```

The name says ADK because it represents an ADK-style workflow boundary, but the object is intentionally ARC-owned and stable.

## Where this plugs into existing ARC code

Likely touchpoints:

- `nexus/agents/` — native source-of-truth primitives
- `nexus/core/command_bridge/agents_handler.py` — external invocation surface
- `nexus/core/workflow_engine/composite_step.py` — enterprise wrapper
- `nexus/adapters/ai/` — authoritative provider execution
- future `nexus/integrations/adk/` package — interop-only code

## Recommended first implementation slice

The best low-risk next slice is:

1. add `nexus/integrations/adk/types.py`
2. add `nexus/integrations/adk/mapper.py`
3. implement ARC → ADK-style export for `SequentialAgent` and `ParallelAgent`
4. add unit tests for mapping only
5. do **not** execute ADK runtime yet

Why this first:

- easiest to test
- no provider dependency
- creates a stable seam for later MCP/A2A/runtime work
- keeps #151's architecture intact

## Risks

- ADK 2.0 may change shape while still beta/alpha
- graph semantics may not map cleanly to ARC loops/coordinator behavior
- output merge semantics can become subtly incompatible
- checkpoint/resume semantics can diverge quickly across runtimes
- naming the neutral spec `Adk*` may imply stronger compatibility than v0 provides

## Open questions

1. Should the neutral spec be named `AdkWorkflowSpec` or a more generic `InteropWorkflowSpec`?
2. Should MCP and A2A live under `nexus/integrations/adk/` or separate `nexus/integrations/mcp/` and `nexus/integrations/a2a/` packages?
3. Should ADK runtime execution be exposed through the command bridge, Nexus Workflow, or both?
4. How much checkpoint metadata should be carried before ARC has native resumable graph execution?

## Recommendation

Proceed in this order:

1. **spec + mapper first**
2. **MCP/A2A second**
3. **runtime adapter third**

This gives ARC practical ecosystem reach without betting the core architecture on a still-moving external runtime.
