# Nexus Workflows

Nexus workflows are a provider-neutral runtime inspired by ADK workflow semantics.
They do **not** import Google ADK and they do **not** assume Gemini. Agent nodes run
through Nexus ARC agents/providers; function nodes are normal Python code.

Use this when you need deterministic graph execution, collaborative agents,
dynamic control flow, typed payloads, checkpoints, or nested workflows.

## Quick start

```python
import asyncio

from nexus.workflows import START, Event, Workflow


def classify(text: str):
    if "bug" in text.lower():
        return Event(route="BUG")
    return Event(route="SUPPORT")


def handle_bug():
    return Event(message="Routing to bug triage")


def handle_support():
    return Event(message="Routing to support")


workflow = Workflow(
    "triage",
    edges=[
        (START, classify),
        (classify, {"BUG": handle_bug, "SUPPORT": handle_support}),
    ],
)

result = asyncio.run(workflow.run("I found a bug"))
assert result.output == "Routing to bug triage"
assert result.visited == ("classify", "handle_bug")
```

## Core concepts

| Concept | Use |
| --- | --- |
| `Workflow(name, edges=[...])` | Defines a graph workflow. |
| `START` | Entry marker for the graph. |
| Function node | Any sync/async Python callable. |
| `AgentNode` | Wraps a Nexus `BaseAgent`. Usually created automatically. |
| `WorkflowNode` | Embeds a nested `Workflow`. |
| `Event(message=...)` | Emits user/operator-visible output. |
| `Event(route=...)` | Selects a route edge from a dictionary. |
| `Event(state=...)` | Patches workflow state. |
| `@node(...)` | Defines a dynamic/checkpoint-aware node. |
| `ctx.run_node(...)` | Runs child nodes inside dynamic workflows. |
| `RequestInput(...)` | Pauses for human input. |
| `JsonCheckpointStore` | Persists checkpointable node outputs to JSON. |
| `SQLiteCheckpointStore` | Persists JSON-safe checkpoints to SQLite. |
| `tool_node(...)` | Wraps a callable as an explicit tool node. |
| `ctx.run_parallel(...)` | Runs child nodes concurrently with deterministic checkpoint keys. |

`Workflow.run(...)` returns `WorkflowResult`:

```python
result.output    # final payload
result.messages  # tuple[str, ...] from Event(message=...)
result.state     # merged Event(state=...) patches
result.visited   # node names visited by the top-level graph
result.paused    # RequestInput when paused, else None
result.events    # all emitted Event objects
result.records   # execution trace with cache-hit metadata
```

## 1. Graph-based workflows

Graph workflows connect nodes through tuple edges.

### Sequential graph

```python
import asyncio
from dataclasses import dataclass

from nexus.workflows import START, Event, Workflow


@dataclass
class CityTime:
    city: str
    time_info: str


def choose_city(prompt: str) -> str:
    return prompt.strip() or "London"


def lookup_time(city: str) -> CityTime:
    return CityTime(city=city, time_info="10:10 AM")


def final_answer(info: CityTime) -> Event:
    return Event(message=f"It is {info.time_info} in {info.city}.")


workflow = Workflow(
    "city_time",
    edges=[(START, choose_city, lookup_time, final_answer)],
)

result = asyncio.run(workflow.run("Paris"))
print(result.output)  # It is 10:10 AM in Paris.
```

### Routed graph

Route edges use `Event(route=...)` and a dictionary mapping route labels to
nodes.

```python
from nexus.workflows import START, Event, Workflow


def router(text: str) -> Event:
    return Event(route="BUG" if "bug" in text.lower() else "GENERAL")


def bug_handler() -> Event:
    return Event(message="Create a bug workflow")


def general_handler() -> Event:
    return Event(message="Handle as a general request")


workflow = Workflow(
    "request_router",
    edges=[
        (START, router),
        (router, {"BUG": bug_handler, "GENERAL": general_handler}),
    ],
)
```

### Agent node in a graph

You can place a Nexus `BaseAgent` directly in the graph. It is wrapped as an
`AgentNode` automatically.

```python
import asyncio

from nexus.agents.base import AgentContext, AgentOutput, BaseAgent
from nexus.workflows import START, Event, Workflow


class Reviewer(BaseAgent):
    async def run(self, context: AgentContext) -> AgentOutput:
        return AgentOutput(content=f"reviewed: {context.task}")


def done(review: str) -> Event:
    return Event(message=review)


reviewer = Reviewer("reviewer", "Reviews an input")
workflow = Workflow("review", edges=[(START, reviewer, done)])

result = asyncio.run(workflow.run("check the design"))
```

## 2. Collaborative agents

Collaboration helpers add ADK-style collaboration mode metadata while keeping
execution in Nexus ARC.

```python
from nexus.workflows import collaborative_agent, single_turn_agents

chat_node = collaborative_agent(agent, mode="chat")
task_node = collaborative_agent(agent, mode="task")
single_turn_node = collaborative_agent(agent, mode="single_turn")

parallel_safe_nodes = single_turn_agents([agent_a, agent_b])
```

Modes:

| Mode | Behavior |
| --- | --- |
| `chat` | Long-lived conversational/collaborative agent mode. |
| `task` | Leaf task agent that auto-returns to the parent. |
| `single_turn` | Leaf, parallel-safe, isolated context branch. |

`task` and `single_turn` currently require leaf agents. If you pass an agent
with `sub_agents`, Nexus raises `ValueError` to avoid ambiguous nesting.

Example:

```python
import asyncio

from nexus.workflows import START, Event, Workflow, collaborative_agent

weather_node = collaborative_agent(weather_agent, mode="single_turn")


def summarize(weather: str) -> Event:
    return Event(message=f"Weather result: {weather}")


workflow = Workflow("weather", edges=[(START, weather_node, summarize)])
result = asyncio.run(workflow.run("London"))
```

Inside the agent, `context.metadata["collaboration_mode"]` contains the selected
mode.

## 3. Dynamic workflows

Dynamic workflows use `@node` and can call child nodes with `ctx.run_node(...)`.
This is where normal Python control flow lives: conditionals, loops, retries,
recursive decomposition, and HITL pauses.

```python
import asyncio

from nexus.workflows import START, Workflow, node


@node(name="expensive_step")
def expensive_step(text: str) -> str:
    return text.upper()


@node(rerun_on_resume=True)
async def dynamic_workflow(ctx, text: str) -> str:
    # Stable run_id means this child can be skipped on resume/checkpoint hits.
    result = await ctx.run_node(expensive_step, text, run_id="normalize-input")
    return f"result:{result}"


workflow = Workflow("dynamic", edges=[(START, dynamic_workflow)])
result = asyncio.run(workflow.run("hello"))
```

### Human-in-the-loop pause

Return `RequestInput` to pause execution.

```python
from nexus.workflows import RequestInput, node


@node
def approval(ctx, deploy_plan: str):
    return RequestInput(
        message="Approve deployment?",
        key="deployment_approval",
        metadata={"plan": deploy_plan},
    )
```

When a workflow pauses:

```python
result = await workflow.run("deploy production")
if result.paused:
    print(result.paused.message)
```

Resume wiring is intentionally left to the caller/runtime layer for now; the
workflow runtime exposes the pause object and checkpoint primitives.

### Streaming/yielded events

Dynamic nodes can be generators or async generators that yield `Event(...)`
updates. The last yielded event can also route the graph.

```python
from nexus.workflows import Event, node


@node(rerun_on_resume=True)
def router(text: str):
    yield Event(message="Classifying request", state={"started": True})
    yield Event(route="APPROVE")
```

`result.events` contains all emitted events, and `result.messages` contains the
message text from message events.

## 4. Nested workflows

Use `workflow_node(...)` to embed one workflow as a node in another.

```python
import asyncio

from nexus.workflows import START, Event, Workflow, workflow_node


def child_step(city: str) -> Event:
    return Event(message=f"child:{city}", state={"city": city})


child = Workflow("child", edges=[(START, child_step)])


def parent_done(child_output: str) -> Event:
    return Event(message=f"parent saw {child_output}")


parent = Workflow(
    "parent",
    edges=[(START, workflow_node(child), parent_done)],
)

result = asyncio.run(parent.run("Rome"))
assert result.messages == ("child:Rome", "parent saw child:Rome")
assert result.state == {"city": "Rome"}
```

Nested workflow messages, state, and events are merged into the parent result.

## 5. Input/output schemas

Nodes can validate or coerce input/output payloads with lightweight schemas.
Dataclasses and Pydantic-style models are supported.

```python
from dataclasses import dataclass

from nexus.workflows import START, Workflow, node


@dataclass
class CityRequest:
    city: str


@dataclass
class CityResult:
    text: str


@node(input_schema=CityRequest, output_schema=CityResult)
def answer(request: CityRequest):
    # Returning a dict is okay; it is coerced to CityResult.
    return {"text": f"hello {request.city}"}


workflow = Workflow("schemas", edges=[(START, answer)])
import asyncio

result = asyncio.run(workflow.run({"city": "Paris"}))
assert result.output == CityResult(text="hello Paris")
```

Invalid payloads raise `WorkflowSchemaError` with the node name and direction
(`input` or `output`).

## 6. Tool nodes

Use `tool_node(...)` when a callable represents an explicit tool rather than a plain function. Runtime behavior is callable-based, but `kind == "tool"` is preserved for introspection and future MCP/A2A export.

```python
from nexus.workflows import START, Workflow, tool_node


def double(value: int) -> int:
    return value * 2


tool = tool_node(double, name="double_tool")
workflow = Workflow("tool_graph", edges=[(START, tool)])
```

## 7. Durable parallel execution

Inside dynamic workflows, `ctx.run_parallel(...)` runs child nodes concurrently and gives each branch a stable checkpoint key. If one branch fails after another succeeds, the successful branch can be skipped on retry/resume.

```python
@node(rerun_on_resume=True)
async def dynamic(ctx, payload):
    left, right = await ctx.run_parallel(
        [left_step, right_step],
        [payload, payload],
        run_id_prefix="branch",
    )
    return {"left": left, "right": right}
```

You can also pass explicit `run_ids=[...]`. Custom IDs must contain at least one non-numeric character; numeric IDs are reserved for Nexus auto-generated sequence IDs.

## 8. HITL resume inputs

`RequestInput` pauses execution when no answer is available. Resume by calling `workflow.run(...)` again with the same checkpoints and a `resume_inputs` mapping keyed by `RequestInput.key`.

```python
@node
def ask_approval(ctx, plan):
    return RequestInput(message="Approve?", key="approval", metadata={"plan": plan})


@node(rerun_on_resume=True)
async def dynamic(ctx, plan):
    answer = await ctx.run_node(ask_approval, plan, run_id="approval-step")
    return f"approved={answer}"


checkpoints = {}
paused = asyncio.run(workflow.run("deploy", checkpoints=checkpoints))
resumed = asyncio.run(
    workflow.run("deploy", checkpoints=checkpoints, resume_inputs={"approval": "yes"})
)
```

## 9. Checkpoints

By default, checkpoints can be any mutable mapping, e.g. a dict.

```python
checkpoints = {}
asyncio.run(workflow.run("hello", checkpoints=checkpoints))
asyncio.run(workflow.run("hello", checkpoints=checkpoints))
```

Use `JsonCheckpointStore` for JSON-backed persistence:

```python
from nexus.workflows import JsonCheckpointStore

store = JsonCheckpointStore(".nexus/workflow-checkpoints.json")
result = asyncio.run(workflow.run("hello", checkpoints=store))
```

Checkpoint keys are generated as:

```text
<workflow_name>:<run_id>:<node_name>
```

Use stable `run_id` values inside dynamic workflows when you want successful
child nodes to be skipped on resume:

```python
@node(rerun_on_resume=True)
async def dynamic(ctx, payload):
    normalized = await ctx.run_node(normalize, payload, run_id="normalize")
    planned = await ctx.run_node(plan, normalized, run_id="plan")
    return planned
```

Execution trace records show cache hits:

```python
for record in result.records:
    print(record.node_name, record.run_id, record.cached)
```

`JsonCheckpointStore` and `SQLiteCheckpointStore` only support JSON-serializable outputs. If a node returns
a custom object, either use an in-memory dict, return JSON-safe data, or provide a
custom checkpoint store.

Use SQLite when you want durable checkpoints without rewriting one JSON file:

```python
from nexus.workflows import SQLiteCheckpointStore

store = SQLiteCheckpointStore(".nexus/workflow-checkpoints.sqlite")
result = asyncio.run(workflow.run("hello", checkpoints=store))
store.close()
```

## 10. Patterns

### Router + handler pattern

```python
workflow = Workflow(
    "router",
    edges=[
        (START, classify),
        (classify, {"BUG": bug_handler, "FEATURE": feature_handler}),
    ],
)
```

### Dynamic planner pattern

```python
@node(rerun_on_resume=True)
async def planner(ctx, request):
    plan = await ctx.run_node(make_plan, request, run_id="plan")
    if plan["needs_review"]:
        review = await ctx.run_node(review_plan, plan, run_id="review")
        return review
    return plan
```

### Nested sub-workflow pattern

```python
child = Workflow("child", edges=[(START, step_a, step_b)])
parent = Workflow("parent", edges=[(START, workflow_node(child), final_step)])
```

## Current limitations

- This is a Nexus-native runtime, not a Google ADK runtime adapter.
- Resume orchestration after `RequestInput` is exposed as primitives, not yet a
  full persisted workflow service.
- `JsonCheckpointStore` requires JSON-serializable outputs.
- Parallel branch execution is available inside dynamic workflows through
  `ctx.run_parallel(...)`; graph-level parallel edge syntax is intentionally not
  introduced yet.
- Import/export to actual Google ADK objects is intentionally separate from this
  runtime and belongs under `nexus/integrations/adk/`.
