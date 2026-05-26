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
    assert compliance["parameters"]["jsonBody"].startswith("={{")
    assert '"run_id": $json.run?.run_id || $json.run_id' in compliance["parameters"]["jsonBody"]
    assert '"run_id": "={{' not in compliance["parameters"]["jsonBody"]

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
    assert '"repo_dir": $json.repo_dir || $json.run?.metadata?.repo_dir || ' in develop[
        "parameters"
    ]["jsonBody"]
    assert "isolated worktree" in develop["notes"]


def test_create_run_body_uses_prepared_input_and_preserves_repo_dir():
    workflow = convert_workflow_to_n8n(ENTERPRISE_FULL)

    create = _node_by_name(workflow, "Create Nexus Run")
    assert create["parameters"]["jsonBody"].startswith("={{")
    assert '"task": $json.task || $json.run?.task' in create["parameters"]["jsonBody"]
    assert '"project_key": $json.project_key || $json.run?.project_key' in create["parameters"][
        "jsonBody"
    ]
    assert (
        '"repo_dir": $json.repo_dir || $json.repo_path || $json.run?.metadata?.repo_dir || '
        in create["parameters"]["jsonBody"]
    )
    assert '"task": "={{' not in create["parameters"]["jsonBody"]


def test_generated_workflow_makes_manual_input_contract_explicit():
    workflow = convert_workflow_to_n8n(ENTERPRISE_FULL)

    prepare = _node_by_name(workflow, "Prepare Nexus Input")
    assert prepare["type"] == "n8n-nodes-base.code"
    assert "const manualInput = {" in prepare["parameters"]["jsCode"]
    assert "task: ''" in prepare["parameters"]["jsCode"]
    assert "project_key: ''" in prepare["parameters"]["jsCode"]
    assert "repo_dir: ''" in prepare["parameters"]["jsCode"]
    assert "Set task in Prepare Nexus Input" in prepare["parameters"]["jsCode"]


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
    assert parsed["connections"]["Manual Trigger"]["main"][0][0]["node"] == "Prepare Nexus Input"
    assert parsed["connections"]["Prepare Nexus Input"]["main"][0][0]["node"] == "Create Nexus Run"
