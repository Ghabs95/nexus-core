from __future__ import annotations

import asyncio
from dataclasses import dataclass

from nexus.agents.base import AgentContext, AgentOutput, BaseAgent
from nexus.workflows import START, Event, RequestInput, Workflow, collaborative_agent, node


class EchoAgent(BaseAgent):
    def __init__(self, name: str, response_prefix: str = "agent"):
        super().__init__(name=name, description=f"{name} test agent")
        self.response_prefix = response_prefix
        self.contexts: list[AgentContext] = []

    async def run(self, context: AgentContext) -> AgentOutput:
        self.contexts.append(context)
        return AgentOutput(
            content=f"{self.response_prefix}:{context.task}", metadata={"agent": self.name}
        )


def test_graph_workflow_runs_agent_function_event_chain():
    city_agent = EchoAgent("city_generator", "city")

    @dataclass
    class CityTime:
        time_info: str
        city: str

    def lookup_time(node_input: str):
        return CityTime(time_info="10:10 AM", city=node_input)

    def completed(node_input: CityTime):
        return Event(
            message=f"It is {node_input.time_info} in {node_input.city}.\nWORKFLOW COMPLETED."
        )

    workflow = Workflow(
        name="city_workflow",
        edges=[(START, city_agent, lookup_time, completed)],
    )

    result = asyncio.run(workflow.run("Paris"))

    assert result.visited == ("city_generator", "lookup_time", "completed")
    assert result.output.endswith("WORKFLOW COMPLETED.")
    assert result.messages == (result.output,)
    assert city_agent.contexts[0].task == "Paris"


def test_graph_workflow_supports_event_route_dictionary_edges():
    def classify(node_input: str):
        if "bug" in node_input.lower():
            return Event(route="BUG")
        return Event(route="SUPPORT")

    def bug_handler():
        return Event(message="Handling bug...")

    def support_handler():
        return Event(message="Handling support...")

    workflow = Workflow(
        name="routing_workflow",
        edges=[
            (START, classify),
            (classify, {"BUG": bug_handler, "SUPPORT": support_handler}),
        ],
    )

    result = asyncio.run(workflow.run("I found a bug"))

    assert result.visited == ("classify", "bug_handler")
    assert result.output == "Handling bug..."


def test_collaborative_single_turn_agent_is_isolated_and_auto_returns():
    weather = EchoAgent("weather_checker", "weather")
    weather_node = collaborative_agent(weather, mode="single_turn")

    def done(node_input: str):
        return Event(message=f"done:{node_input}")

    workflow = Workflow(name="collab", edges=[(START, weather_node, done)])
    result = asyncio.run(workflow.run("London"))

    assert result.visited == ("weather_checker", "done")
    assert result.output == "done:weather:London"
    assert weather.contexts[0].metadata["collaboration_mode"] == "single_turn"
    assert weather.contexts[0].prior_outputs == []


def test_dynamic_node_context_run_node_checkpoints_successful_children():
    calls = {"expensive": 0}

    @node(name="expensive_step")
    def expensive_step(node_input: str):
        calls["expensive"] += 1
        return node_input.upper()

    @node(rerun_on_resume=True)
    async def dynamic_workflow(ctx, node_input: str):
        result = await ctx.run_node(expensive_step, node_input)
        return f"result:{result}"

    checkpoints: dict[str, object] = {}
    workflow = Workflow(name="dynamic", edges=[(START, dynamic_workflow)])

    first = asyncio.run(workflow.run("hello", checkpoints=checkpoints))
    second = asyncio.run(workflow.run("hello", checkpoints=checkpoints))

    assert first.output == "result:HELLO"
    assert second.output == "result:HELLO"
    assert calls["expensive"] == 1


def test_dynamic_node_can_pause_for_human_input():
    @node(rerun_on_resume=False)
    async def approval(ctx, node_input):
        return RequestInput(message="Approve?", key="approval")

    workflow = Workflow(name="approval", edges=[(START, approval)])
    result = asyncio.run(workflow.run("deploy"))

    assert result.paused is not None
    assert result.paused.message == "Approve?"
    assert result.visited == ("approval",)
