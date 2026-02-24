"""Workflow visualization utilities — Mermaid.js diagram generation."""

from typing import Dict, Optional

from nexus.core.models import StepStatus, Workflow

# Maps StepStatus enum values to Mermaid CSS class names used in the diagram.
_STATUS_CLASS: Dict[StepStatus, str] = {
    StepStatus.PENDING: "pending",
    StepStatus.RUNNING: "running",
    StepStatus.COMPLETED: "completed",
    StepStatus.FAILED: "failed",
    StepStatus.SKIPPED: "skipped",
}

# Mermaid classDef declarations appended at the end of every diagram.
_CLASS_DEFS = """\
    classDef pending fill:#e0e0e0,stroke:#9e9e9e
    classDef running fill:#2196f3,stroke:#1565c0,color:#fff
    classDef completed fill:#4caf50,stroke:#2e7d32,color:#fff
    classDef failed fill:#f44336,stroke:#b71c1c,color:#fff
    classDef skipped fill:#ff9800,stroke:#e65100,color:#fff"""


def workflow_to_mermaid(workflow: Workflow, title: Optional[str] = None) -> str:
    """Convert a :class:`~nexus.core.models.Workflow` to a Mermaid flowchart string.

    The returned string is a valid ``flowchart TD`` diagram that can be
    embedded in a Telegram message as a fenced code block or rendered by any
    Mermaid-compatible viewer.

    Args:
        workflow: The workflow to visualise.
        title: Optional diagram title.  Defaults to ``workflow.name``.

    Returns:
        A Mermaid diagram string.
    """
    diagram_title = title or workflow.name
    lines = [f'---\ntitle: "{diagram_title}"\n---', "flowchart TD"]

    step_ids = []
    for step in workflow.steps:
        node_id = f"step{step.step_num}"
        step_ids.append(node_id)
        status_class = _STATUS_CLASS.get(step.status, "pending")
        status_label = step.status.value.upper()
        label = f"{step.step_num}. {step.name}\\n[{status_label}]"
        lines.append(f'    {node_id}["{label}"]:::{status_class}')

    # Sequential edges between consecutive steps.
    for i in range(len(step_ids) - 1):
        lines.append(f"    {step_ids[i]} --> {step_ids[i + 1]}")

    lines.append(_CLASS_DEFS)
    return "\n".join(lines)
