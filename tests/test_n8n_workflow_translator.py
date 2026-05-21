import json
from pathlib import Path

from nexus.translators.to_n8n import convert_workflow_to_n8n, translate_workflow_to_n8n

ROOT = Path(__file__).resolve().parents[1]
ENTERPRISE_FULL = ROOT / "examples" / "workflows" / "enterprise_full_workflow.yaml"
ENTERPRISE_ROUTER = ROOT / "examples" / "workflows" / "enterprise_workflow.yaml"


def _node_by_name(workflow: dict, name: str) -> dict:
    for node in workflow["nodes"]:
        if node["name"] == name:
            return node
    raise AssertionError(f"missing node {name!r}")


def test_converts_enterprise_full_compliance_gate():
    workflow = convert_workflow_to_n8n(ENTERPRISE_FULL)

    assert workflow["name"] == "Enterprise Full Workflow"
    assert workflow["settings"]["executionOrder"] == "v1"
    assert workflow["meta"]["nexusArc"]["source"].endswith("enterprise_full_workflow.yaml")

    compliance = _node_by_name(workflow, "07 Start compliance")
    assert compliance["type"] == "n8n-nodes-base.httpRequest"
    assert compliance["parameters"]["url"].endswith("/api/v1/n8n/runs/update")
    assert '"state": "compliance"' in compliance["parameters"]["jsonBody"]

    approval = _node_by_name(workflow, "Approval Gate compliance")
    assert "Human approval required" in approval["parameters"]["jsonBody"]

    route = _node_by_name(workflow, "Route route_compliance")
    assert route["type"] == "n8n-nodes-base.code"
    assert "compliance_status == 'approved'" in route["parameters"]["jsCode"]


def test_developer_steps_use_guarded_opencode_endpoint():
    workflow = convert_workflow_to_n8n(ENTERPRISE_FULL)

    develop = _node_by_name(workflow, "Execute OpenCode develop")
    assert develop["parameters"]["url"].endswith("/api/v1/n8n/coding/execute")
    assert '"worker": "opencode"' in develop["parameters"]["jsonBody"]
    assert "isolated worktree" in develop["notes"]


def test_router_workflow_can_resolve_requested_tier():
    workflow = convert_workflow_to_n8n(ENTERPRISE_ROUTER, workflow_type="full")
    node_names = {node["name"] for node in workflow["nodes"]}

    assert workflow["name"] == "Enterprise Workflow Router [full]"
    assert "07 Start compliance" in node_names
    assert "Approval Gate deploy" in node_names


def test_translator_outputs_valid_json():
    rendered = translate_workflow_to_n8n(ENTERPRISE_FULL)
    parsed = json.loads(rendered)

    assert parsed["nodes"][0]["name"] == "Manual Trigger"
    assert parsed["connections"]["Manual Trigger"]["main"][0][0]["node"] == "Create Nexus Run"
