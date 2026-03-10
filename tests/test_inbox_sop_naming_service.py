from nexus.core.inbox.inbox_sop_naming_service import (
    refine_issue_content_with_ai,
    render_checklist_from_workflow,
)


class _Logger:
    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None


def test_refine_issue_content_fallback_cleans_inbox_markdown():
    source = """# Feature
**Project:** Wallible
**Type:** feature
**Task Name:** cross-asset-correlation-analyzer
**Status:** Pending

Wallible: New feature proposal for Wallible

Title: Cross-Asset Correlation Analyzer
Summary: An advanced analytics view for cross-asset movement.
Why now: Supports analytics differentiation.

Implementation outline:
1. Develop correlation algorithms.
2. Build heatmap visualizations.

---
**Source:** inbox
**Requester Nexus ID:** `nexus-42`
---
**Raw Input:**
raw source text
"""

    result = refine_issue_content_with_ai(
        content=source,
        project_name="wallible",
        run_analysis=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
        logger=_Logger(),
        requester_context={"nexus_id": "nexus-42"},
    )

    assert "Task: Cross-Asset Correlation Analyzer" in result
    assert "**Project:**" not in result
    assert "**Raw Input:**" not in result
    assert "Implementation outline:" in result
    assert "1. Develop correlation algorithms." in result


def test_refine_issue_content_uses_ai_text_when_available():
    result = refine_issue_content_with_ai(
        content="raw content",
        project_name="wallible",
        run_analysis=lambda **_kwargs: {"text": "Cleaned task body"},
        logger=_Logger(),
    )

    assert result == "Cleaned task body"


def test_render_checklist_from_workflow_expands_orchestrator_routes(tmp_path):
    master = tmp_path / "master.yaml"
    subflow = tmp_path / "new_feature_workflow.yaml"

    master.write_text(
        "steps:\n"
        "  - id: triage\n"
        "    name: Triage\n"
        "    description: Pick route\n"
        "    agent_type: triage\n"
        "    on_success: dispatch\n"
        "  - id: dispatch\n"
        "    name: Dispatch\n"
        "    agent_type: router\n"
        "    routes:\n"
        "      - default: new_feature_workflow\n",
        encoding="utf-8",
    )
    subflow.write_text(
        "steps:\n"
        "  - id: implementation\n"
        "    name: Implementation\n"
        "    description: Build and validate\n"
        "    agent_type: developer\n"
        "    final_step: true\n",
        encoding="utf-8",
    )

    checklist = render_checklist_from_workflow(
        project_name="wallible",
        tier_name="full",
        get_workflow_definition_path=lambda _project: str(master),
    )

    assert "## SOP Checklist — Full Flow" in checklist
    assert "1. **Triage**" in checklist
    assert "2. **Implementation** — Build and validate" in checklist
