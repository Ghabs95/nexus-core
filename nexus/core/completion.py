"""Completion protocol for agent-to-framework communication.

Defines the contract between agents and the orchestration framework:
1. Schema for structured completion output (JSON)
2. Prompt instructions generator (tells agents what to produce)
3. Completion parser/validator (detects and reads agent output)
4. Comment builder (formats structured output for Git platform comments)
"""
import glob
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Values that indicate "no next agent" / workflow is done
_TERMINAL_VALUES = frozenset({
    "none", "n/a", "null", "no", "end", "done", "finish", "complete", "",
})


@dataclass
class CompletionSummary:
    """Parsed agent completion output.

    Attributes:
        status: Agent outcome, typically "complete" or "error".
        agent_type: The agent_type that produced this summary.
        summary: One-line human-readable summary.
        key_findings: List of notable findings / outputs.
        next_agent: The agent_type that should run next, or "" if workflow is done.
        verdict: Optional verdict / recommendation.
        effort_breakdown: Optional mapping of task → effort description.
        raw: The full raw dict for any extra fields.
    """

    status: str = "complete"
    agent_type: str = "unknown"
    summary: str = ""
    key_findings: List[str] = field(default_factory=list)
    next_agent: str = ""
    verdict: str = ""
    effort_breakdown: Dict[str, str] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_workflow_done(self) -> bool:
        """True when this completion signals no further agent should run."""
        return self.next_agent.strip().lower() in _TERMINAL_VALUES

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "CompletionSummary":
        """Build a CompletionSummary from a raw JSON dict."""
        return CompletionSummary(
            status=data.get("status", "complete"),
            agent_type=data.get("agent_type", "unknown"),
            summary=data.get("summary", ""),
            key_findings=data.get("key_findings", []),
            next_agent=data.get("next_agent", ""),
            verdict=data.get("verdict", ""),
            effort_breakdown=data.get("effort_breakdown", {}),
            raw=data,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize back to a plain dict."""
        d: Dict[str, Any] = {
            "status": self.status,
            "agent_type": self.agent_type,
            "summary": self.summary,
            "key_findings": self.key_findings,
            "next_agent": self.next_agent,
        }
        if self.verdict:
            d["verdict"] = self.verdict
        if self.effort_breakdown:
            d["effort_breakdown"] = self.effort_breakdown
        return d


# ---------------------------------------------------------------------------
# Comment builder — structured output → Git platform comment
# ---------------------------------------------------------------------------


def build_completion_comment(completion: CompletionSummary) -> str:
    """Build a Git-platform comment body from an agent completion summary.

    Returns a Markdown string suitable for ``GitPlatform.add_comment()``.
    """
    sections: List[str] = []
    sections.append("### ✅ Agent Completed")

    if completion.summary:
        sections.append(f"**Summary:** {completion.summary}")

    if completion.status and completion.status != "complete":
        sections.append(f"**Status:** {completion.status}")

    if completion.key_findings:
        items = "\n".join(f"- {f}" for f in completion.key_findings)
        sections.append(f"**Key Findings:**\n{items}")

    if completion.effort_breakdown:
        items = "\n".join(f"- {t}: {e}" for t, e in completion.effort_breakdown.items())
        sections.append(f"**Effort Breakdown:**\n{items}")

    if completion.verdict:
        sections.append(f"**Verdict:** {completion.verdict}")

    if completion.next_agent and not completion.is_workflow_done:
        display_name = completion.next_agent.title()
        sections.append(f"**Next:** Ready for `@{display_name}`")

    sections.append("_Automated comment from Nexus._")
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Prompt instructions generator
# ---------------------------------------------------------------------------


def generate_completion_instructions(
    issue_number: str,
    agent_type: str,
    workflow_steps_text: str = "",
    nexus_dir: str = ".nexus",
    project_name: str = "",
) -> str:
    """Generate prompt instructions that tell an agent how to produce output.

    This is the *writer* side of the completion protocol.  It tells the agent
    exactly what deliverables to produce so the framework can detect and parse
    them.

    Args:
        issue_number: GitHub issue number being worked on.
        agent_type: The agent_type running this step.
        workflow_steps_text: Pre-formatted workflow steps text for context.
        nexus_dir: Name of the .nexus directory (default ".nexus").
        project_name: Project subdirectory name (e.g. "nexus"). Completions are
            written to ``tasks/<project_name>/completions/``.

    Returns:
        Prompt text to append to the agent's instructions.
    """
    completions_script = (
        f"```bash\n"
        f'WORKSPACE_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)\n'
        f'COMPLETIONS_DIR=$(find "$WORKSPACE_ROOT" -maxdepth 4 -path \'*/{nexus_dir}/tasks/{project_name}/completions\' -type d 2>/dev/null | head -1)\n'
        f'if [ -z "$COMPLETIONS_DIR" ]; then COMPLETIONS_DIR="$WORKSPACE_ROOT/{nexus_dir}/tasks/{project_name}/completions"; mkdir -p "$COMPLETIONS_DIR"; fi\n'
    )

    return (
        f"**WHEN YOU FINISH — TWO MANDATORY DELIVERABLES:**\n\n"
        f"{workflow_steps_text}\n\n"
        f"## Deliverable 1: Post a structured GitHub comment\n\n"
        f"Use `gh issue comment {issue_number} --repo <REPO> --body '<comment>'` "
        f"to post a **structured** comment.\n"
        f"The comment MUST follow this format (adapt sections to your work):\n\n"
        f"```\n"
        f"## 🔍 <Step Name> Complete — <agent_type>\n\n"
        f"**Severity:** <Critical|High|Medium|Low>\n"
        f"**Target Sub-Repo:** `<repo-name>`\n"
        f"**Workflow:** <workflow type>\n\n"
        f"### Findings\n\n"
        f"<Describe what you analyzed/discovered. Use bullet points for key findings:>\n"
        f"- Finding 1\n"
        f"- Finding 2\n"
        f"- Finding 3\n\n"
        f"### SOP Checklist\n\n"
        f"Use the workflow steps above to build the checklist. Example:\n"
        f"- [x] 1. Initial Routing — `triage` : Severity + routing ✅\n"
        f"- [ ] 2. Create Design Proposal — `design`\n"
        f"- [ ] 3. Summarize & Close — `summarizer`\n\n"
        f"Ready for **@<Display Name>**\n"
        f"```\n\n"
        f"**IMPORTANT:** The comment must contain real findings from YOUR analysis, "
        f"not placeholder text.\n"
        f"Adapt the template to your role ({agent_type}). Include concrete details.\n"
        f"For the 'Ready for @...' line, use the **Display Names** mapping from "
        f"the workflow steps above (e.g., `Ready for **@Developer**`). "
        f"Do NOT use the raw agent_type.\n\n"
        f"## Deliverable 2: Write completion summary JSON\n\n"
        f"Write a JSON file with your structured results. Use this exact command:\n\n"
        f"**IMPORTANT:** The `{nexus_dir}/` directory lives at the **workspace root** "
        f"(the top-level directory you were launched in). "
        f"Do NOT create a new `{nexus_dir}/` folder inside sub-repos or subdirectories.\n\n"
        + completions_script +
        f"python3 -c 'import json,os; p=os.path.join(os.environ[\"COMPLETIONS_DIR\"], \"completion_summary_{issue_number}.json\"); d={{\"status\":\"complete\",\"agent_type\":\"{agent_type}\",\"summary\":\"<one-line summary of what you did>\",\"key_findings\":[\"<finding 1>\",\"<finding 2>\"],\"next_agent\":\"<agent_type from workflow steps — NOT the step id or display name>\"}}; open(p, \"w\", encoding=\"utf-8\").write(json.dumps(d, indent=2))'\n"
        f"```\n\n"
        f"Replace the `<placeholder>` values with real data from your analysis.\n\n"
        f"After posting the comment and writing the JSON, **EXIT immediately**.\n"
        f"DO NOT attempt to invoke or launch any other agent."
    )


# ---------------------------------------------------------------------------
# Completion detector — file-based scanning
# ---------------------------------------------------------------------------


@dataclass
class DetectedCompletion:
    """A completion summary detected on disk."""

    file_path: str
    issue_number: str
    summary: CompletionSummary

    @property
    def dedup_key(self) -> str:
        """Deduplication key: ``{issue}:{agent_type}:{filename}``."""
        basename = os.path.basename(self.file_path)
        return f"{self.issue_number}:{self.summary.agent_type}:{basename}"


def scan_for_completions(
    base_dir: str,
    nexus_dir: str = ".nexus",
) -> List[DetectedCompletion]:
    """Scan directories under *base_dir* for ``completion_summary_*.json`` files.

    Args:
        base_dir: Root directory to search recursively.
        nexus_dir: Name of the nexus state directory (default ".nexus").

    Returns:
        List of detected completion summaries, each parsed and validated.
    """
    results: List[DetectedCompletion] = []
    candidates_by_issue: Dict[str, List[str]] = {}
    seen_paths: set[str] = set()
    patterns = [
        os.path.join(
            base_dir, "**", nexus_dir, "tasks", "*", "completions",
            "completion_summary_*.json",
        ),
    ]

    for pattern in patterns:
        for path in glob.glob(pattern, recursive=True):
            normalized = os.path.abspath(path)
            if normalized in seen_paths:
                continue
            seen_paths.add(normalized)

            match = re.search(r"completion_summary_(\d+)\.json$", path)
            if not match:
                continue
            issue_number = match.group(1)
            candidates_by_issue.setdefault(issue_number, []).append(path)

    for issue_number, candidate_paths in sorted(
        candidates_by_issue.items(), key=lambda item: int(item[0])
    ):
        sorted_paths = sorted(candidate_paths, key=os.path.getmtime, reverse=True)
        parsed = False
        for path in sorted_paths:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                summary = CompletionSummary.from_dict(data)
                results.append(DetectedCompletion(
                    file_path=path,
                    issue_number=issue_number,
                    summary=summary,
                ))
                parsed = True
                break
            except json.JSONDecodeError as exc:
                logger.warning(f"Invalid JSON in {path}: {exc}")
            except Exception as exc:
                logger.warning(f"Error reading completion file {path}: {exc}")

        if not parsed:
            logger.warning(
                "No valid completion summary could be parsed for issue %s from %d file(s)",
                issue_number,
                len(sorted_paths),
            )

    return results
