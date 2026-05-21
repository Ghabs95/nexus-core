"""Nexus workflow YAML to n8n workflow JSON translator."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from nexus.core.workflow_engine.workflow_definition_loader import resolve_workflow_steps_list

_DEFAULT_BRIDGE_URL = "http://nexus-bridge-1:8091"


def translate_workflow_to_n8n(
    yaml_path: str | Path,
    *,
    workflow_type: str = "",
    bridge_url: str = _DEFAULT_BRIDGE_URL,
) -> str:
    """Load a Nexus workflow definition and return importable n8n JSON."""
    workflow = convert_workflow_to_n8n(
        yaml_path,
        workflow_type=workflow_type,
        bridge_url=bridge_url,
    )
    return json.dumps(workflow, indent=2, sort_keys=False) + "\n"


def convert_workflow_to_n8n(
    yaml_path: str | Path,
    *,
    workflow_type: str = "",
    bridge_url: str = _DEFAULT_BRIDGE_URL,
) -> dict[str, Any]:
    """Convert a Nexus workflow YAML file to an n8n workflow dictionary.

    The generated workflow treats n8n as the state machine and Nexus as the
    controlled execution surface. Each Nexus step emits a state update through
    the n8n bridge. Developer steps call the guarded OpenCode execution endpoint;
    other agent steps are represented as state transitions for import-time
    customization.
    """
    source_path = Path(yaml_path)
    data = _load_workflow_yaml(source_path)
    steps = resolve_workflow_steps_list(data, workflow_type)
    if not steps:
        raise ValueError(f"No workflow steps found in {source_path}")

    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    workflow_name = str(metadata.get("name") or source_path.stem).strip()
    if workflow_type:
        workflow_name = f"{workflow_name} [{workflow_type}]"

    builder = _N8nWorkflowBuilder(
        workflow_name=workflow_name,
        source_path=source_path,
        data=data,
        steps=steps,
        workflow_type=workflow_type,
        bridge_url=bridge_url.rstrip("/"),
    )
    return builder.build()


def _load_workflow_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Workflow definition must be a mapping: {path}")
    data["__yaml_path"] = str(path)
    return data


class _N8nWorkflowBuilder:
    def __init__(
        self,
        *,
        workflow_name: str,
        source_path: Path,
        data: dict[str, Any],
        steps: list[dict[str, Any]],
        workflow_type: str,
        bridge_url: str,
    ) -> None:
        self.workflow_name = workflow_name
        self.source_path = source_path
        self.data = data
        self.steps = [step for step in steps if isinstance(step, dict)]
        self.workflow_type = workflow_type
        self.bridge_url = bridge_url
        self.nodes: list[dict[str, Any]] = []
        self.connections: dict[str, dict[str, list[list[dict[str, Any]]]]] = {}
        self.step_start_names: dict[str, str] = {}
        self.step_exit_names: dict[str, str] = {}

    def build(self) -> dict[str, Any]:
        self._add_manual_trigger()
        create_run_name = self._add_create_run()
        self._register_step_nodes()
        first_step_id = _step_id(self.steps[0], 1)
        self._connect("Create Nexus Run", self.step_start_names[first_step_id])
        self._connect("Manual Trigger", create_run_name)
        self._wire_step_transitions()

        return {
            "name": self.workflow_name,
            "nodes": self.nodes,
            "connections": self.connections,
            "settings": {"executionOrder": "v1"},
            "staticData": None,
            "tags": ["nexus-arc", "generated"],
            "meta": {
                "nexusArc": {
                    "source": str(self.source_path),
                    "workflowType": self.workflow_type or self.data.get("workflow_type"),
                    "converter": "nexus.translators.to_n8n",
                }
            },
        }

    def _add_manual_trigger(self) -> None:
        self._node(
            node_id="manual-trigger",
            name="Manual Trigger",
            node_type="n8n-nodes-base.manualTrigger",
            type_version=1,
            parameters={},
            position=(0, 0),
        )

    def _add_create_run(self) -> str:
        metadata = self.data.get("metadata") if isinstance(self.data.get("metadata"), dict) else {}
        body = {
            "task": "={{$json.task || $json.title || 'Run Nexus workflow'}}",
            "project_key": "={{$json.project_key || $json.project || 'nexus'}}",
            "issue_number": "={{$json.issue_number || $json.issue || ''}}",
            "requester": {"source": "n8n", "workflow": self.workflow_name},
            "metadata": {
                "nexus_workflow": metadata,
                "workflow_type": self.workflow_type or self.data.get("workflow_type"),
                "source": str(self.source_path),
            },
        }
        self._http_node(
            node_id="create-nexus-run",
            name="Create Nexus Run",
            method="POST",
            url=f"{self.bridge_url}/api/v1/n8n/runs",
            json_body=body,
            position=(260, 0),
            notes="Creates the durable Nexus run that n8n will advance.",
        )
        return "Create Nexus Run"

    def _register_step_nodes(self) -> None:
        for index, step in enumerate(self.steps, start=1):
            step_id = _step_id(step, index)
            x = 560 + (index - 1) * 360
            y = _lane_for_step(step, index)
            start_name = self._add_step_start(step, step_id, index, x, y)
            self.step_start_names[step_id] = start_name

            if _is_router_step(step):
                exit_name = self._add_route_nodes(step, step_id, x + 220, y)
            else:
                previous_name = start_name
                if bool(step.get("require_human_approval")):
                    approval_name = self._add_approval_gate(step, step_id, x + 220, y - 120)
                    self._connect(previous_name, approval_name)
                    previous_name = approval_name

                execute_name = self._add_step_execution(step, step_id, x + 220, y)
                self._connect(previous_name, execute_name)
                exit_name = execute_name

            self.step_exit_names[step_id] = exit_name

    def _add_step_start(
        self, step: dict[str, Any], step_id: str, index: int, x: int, y: int
    ) -> str:
        name = f"{index:02d} Start {step_id}"
        payload = {
            "run_id": _run_id_expression(),
            "state": step_id,
            "message": f"Starting Nexus step {step_id}",
            "data": {"step": _public_step_payload(step, step_id, index)},
        }
        self._http_node(
            node_id=f"start-{_slug(step_id)}",
            name=name,
            method="POST",
            url=f"{self.bridge_url}/api/v1/n8n/runs/update",
            json_body=payload,
            position=(x, y),
            notes=step.get("description"),
        )
        return name

    def _add_approval_gate(self, step: dict[str, Any], step_id: str, x: int, y: int) -> str:
        name = f"Approval Gate {step_id}"
        payload = {
            "run_id": _run_id_expression(),
            "state": f"{step_id}_awaiting_approval",
            "message": f"Human approval required before {step_id}",
            "data": {"step_id": step_id, "agent_type": step.get("agent_type")},
        }
        self._http_node(
            node_id=f"approval-{_slug(step_id)}",
            name=name,
            method="POST",
            url=f"{self.bridge_url}/api/v1/n8n/runs/update",
            json_body=payload,
            position=(x, y),
            notes="Connect this node to an n8n approval/wait mechanism if the gate must pause.",
        )
        return name

    def _add_step_execution(self, step: dict[str, Any], step_id: str, x: int, y: int) -> str:
        agent_type = str(step.get("agent_type") or "agent")
        if agent_type == "developer":
            name = f"Execute OpenCode {step_id}"
            payload = {
                "run_id": _run_id_expression(),
                "task": (
                    "={{$json.task || $json.run?.task || "
                    + json.dumps(step.get("description") or f"Execute {step_id}")
                    + "}}"
                ),
                "project_key": "={{$json.project_key || $json.run?.project_key || 'nexus'}}",
                "worker": "opencode",
                "agent": "build",
                "base_branch": "develop",
                "metadata": {"step": _public_step_payload(step, step_id, 0)},
            }
            self._http_node(
                node_id=f"execute-opencode-{_slug(step_id)}",
                name=name,
                method="POST",
                url=f"{self.bridge_url}/api/v1/n8n/coding/execute",
                json_body=payload,
                position=(x, y),
                notes="Guarded Nexus endpoint: allowlist, isolated worktree, no push/merge/PR.",
            )
            return name

        name = f"Complete {step_id}"
        payload = {
            "run_id": _run_id_expression(),
            "state": f"{step_id}_completed",
            "message": f"Completed Nexus step {step_id}",
            "data": {"step": _public_step_payload(step, step_id, 0)},
        }
        self._http_node(
            node_id=f"complete-{_slug(step_id)}",
            name=name,
            method="POST",
            url=f"{self.bridge_url}/api/v1/n8n/runs/update",
            json_body=payload,
            position=(x, y),
            notes="Replace this state transition with a concrete agent/tool call when available.",
        )
        return name

    def _add_route_nodes(self, step: dict[str, Any], step_id: str, x: int, y: int) -> str:
        name = f"Route {step_id}"
        routes = step.get("routes") if isinstance(step.get("routes"), list) else []
        code = _route_code(routes)
        self._node(
            node_id=f"route-{_slug(step_id)}",
            name=name,
            node_type="n8n-nodes-base.code",
            type_version=2,
            parameters={"jsCode": code},
            position=(x, y),
            notes="Evaluates Nexus route rules and writes next_step.",
        )
        self._connect(self.step_start_names[step_id], name)
        return name

    def _wire_step_transitions(self) -> None:
        for index, step in enumerate(self.steps, start=1):
            step_id = _step_id(step, index)
            exit_name = self.step_exit_names[step_id]
            if _is_router_step(step):
                self._wire_router(step, exit_name)
                continue

            target = step.get("on_success")
            if not target and not step.get("final_step"):
                target = _next_step_id(self.steps, index)
            if isinstance(target, str) and target in self.step_start_names:
                self._connect(exit_name, self.step_start_names[target])

    def _wire_router(self, step: dict[str, Any], route_node_name: str) -> None:
        routes = step.get("routes") if isinstance(step.get("routes"), list) else []
        targets = _route_targets(routes)
        if not targets:
            return

        switch_name = f"Switch {route_node_name.removeprefix('Route ')}"
        switch_x, switch_y = _position_after(route_node_name, self.nodes, dx=220)
        self._node(
            node_id=f"switch-{_slug(switch_name)}",
            name=switch_name,
            node_type="n8n-nodes-base.switch",
            type_version=3,
            parameters={
                "mode": "rules",
                "rules": {
                    "values": [
                        {
                            "conditions": {
                                "conditions": [
                                    {
                                        "leftValue": "={{$json.next_step}}",
                                        "rightValue": target,
                                        "operator": {
                                            "type": "string",
                                            "operation": "equals",
                                        },
                                    }
                                ],
                                "combinator": "and",
                            }
                        }
                        for target in targets
                    ]
                },
                "options": {},
            },
            position=(switch_x, switch_y),
            notes="Routes to the Nexus step selected by the preceding Code node.",
        )
        self._connect(route_node_name, switch_name)
        for output_index, target in enumerate(targets):
            start_name = self.step_start_names.get(target)
            if start_name:
                self._connect(switch_name, start_name, output_index=output_index)

    def _http_node(
        self,
        *,
        node_id: str,
        name: str,
        method: str,
        url: str,
        json_body: dict[str, Any],
        position: tuple[int, int],
        notes: str | None = None,
    ) -> None:
        self._node(
            node_id=node_id,
            name=name,
            node_type="n8n-nodes-base.httpRequest",
            type_version=4.2,
            parameters={
                "method": method,
                "url": url,
                "authentication": "predefinedCredentialType",
                "nodeCredentialType": "httpBearerAuth",
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": json.dumps(json_body, indent=2),
            },
            position=position,
            notes=notes,
        )

    def _node(
        self,
        *,
        node_id: str,
        name: str,
        node_type: str,
        type_version: float,
        parameters: dict[str, Any],
        position: tuple[int, int],
        notes: str | None = None,
    ) -> None:
        node: dict[str, Any] = {
            "parameters": parameters,
            "id": node_id,
            "name": name,
            "type": node_type,
            "typeVersion": type_version,
            "position": [position[0], position[1]],
        }
        if notes:
            node["notesInFlow"] = True
            node["notes"] = str(notes)
        self.nodes.append(node)

    def _connect(self, source: str, target: str, *, output_index: int = 0) -> None:
        source_conn = self.connections.setdefault(source, {"main": []})
        while len(source_conn["main"]) <= output_index:
            source_conn["main"].append([])
        edge = {"node": target, "type": "main", "index": 0}
        if edge not in source_conn["main"][output_index]:
            source_conn["main"][output_index].append(edge)


def _step_id(step: dict[str, Any], index: int) -> str:
    return str(step.get("id") or step.get("name") or f"step_{index}").strip()


def _next_step_id(steps: list[dict[str, Any]], current_index: int) -> str | None:
    if current_index >= len(steps):
        return None
    return _step_id(steps[current_index], current_index + 1)


def _is_router_step(step: dict[str, Any]) -> bool:
    return str(step.get("agent_type") or "").strip() == "router" or isinstance(
        step.get("routes"), list
    )


def _route_targets(routes: list[Any]) -> list[str]:
    targets: list[str] = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        target = route.get("then") or route.get("goto") or route.get("default")
        if isinstance(target, str) and target and target not in targets:
            targets.append(target)
    return targets


def _route_code(routes: list[Any]) -> str:
    normalized_routes: list[dict[str, str]] = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        target = route.get("then") or route.get("goto") or route.get("default")
        if not isinstance(target, str) or not target:
            continue
        if "when" in route:
            normalized_routes.append({"when": str(route["when"]), "then": target})
        else:
            normalized_routes.append({"default": target})

    routes_json = json.dumps(normalized_routes, indent=2)
    return f"""const routes = {routes_json};
const state = $json;

function toJsExpression(expr) {{
  return String(expr)
    .replace(/\\band\\b/g, '&&')
    .replace(/\\bor\\b/g, '||')
    .replace(/\\bnot\\b/g, '!')
    .replace(/\\bTrue\\b/g, 'true')
    .replace(/\\bFalse\\b/g, 'false')
    .replace(/\\bNone\\b/g, 'null');
}}

function evaluate(expr) {{
  const keys = Object.keys(state);
  const values = Object.values(state);
  try {{
    return Boolean(Function(...keys, `return (${{toJsExpression(expr)}});`)(...values));
  }} catch (error) {{
    return false;
  }}
}}

let nextStep = null;
for (const route of routes) {{
  if (route.when && evaluate(route.when)) {{
    nextStep = route.then;
    break;
  }}
  if (!route.when && route.default && nextStep === null) {{
    nextStep = route.default;
  }}
}}

return [{{ json: {{ ...state, next_step: nextStep }} }}];
"""


def _public_step_payload(step: dict[str, Any], step_id: str, index: int) -> dict[str, Any]:
    keys = (
        "name",
        "description",
        "agent_type",
        "context_policy",
        "tools",
        "inputs",
        "outputs",
        "routes",
        "on_success",
        "final_step",
        "require_human_approval",
        "audit_limit",
        "include_audit_history",
        "parallel",
    )
    payload = {"id": step_id}
    if index:
        payload["index"] = index
    for key in keys:
        if key in step:
            payload[key] = step[key]
    return payload


def _run_id_expression() -> str:
    return "={{$json.run?.run_id || $json.run_id}}"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return slug or "node"


def _lane_for_step(step: dict[str, Any], index: int) -> int:
    agent_type = str(step.get("agent_type") or "")
    if agent_type == "router":
        return 260
    if bool(step.get("require_human_approval")):
        return -180
    return 0 if index % 2 else 120


def _position_after(
    node_name: str,
    nodes: list[dict[str, Any]],
    *,
    dx: int = 220,
    dy: int = 0,
) -> tuple[int, int]:
    for node in nodes:
        if node.get("name") == node_name:
            x, y = node.get("position", [0, 0])
            return int(x) + dx, int(y) + dy
    return dx, dy
