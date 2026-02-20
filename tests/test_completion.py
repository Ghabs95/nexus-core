"""Tests for the completion protocol module."""
import json
import os
import tempfile

import pytest

from nexus.core.completion import (
    CompletionSummary,
    DetectedCompletion,
    build_completion_comment,
    generate_completion_instructions,
    scan_for_completions,
)


# ---------------------------------------------------------------------------
# CompletionSummary
# ---------------------------------------------------------------------------


class TestCompletionSummary:
    def test_from_dict_minimal(self):
        data = {"status": "complete", "agent_type": "triage"}
        s = CompletionSummary.from_dict(data)
        assert s.status == "complete"
        assert s.agent_type == "triage"
        assert s.summary == ""
        assert s.key_findings == []
        assert s.next_agent == ""

    def test_from_dict_full(self):
        data = {
            "status": "complete",
            "agent_type": "debug",
            "summary": "Fixed the bug",
            "key_findings": ["root cause found", "test added"],
            "next_agent": "summarizer",
            "verdict": "All clear",
            "effort_breakdown": {"analysis": "30min", "fix": "1h"},
        }
        s = CompletionSummary.from_dict(data)
        assert s.summary == "Fixed the bug"
        assert len(s.key_findings) == 2
        assert s.next_agent == "summarizer"
        assert s.verdict == "All clear"
        assert s.effort_breakdown["analysis"] == "30min"

    def test_is_workflow_done_true_variants(self):
        for val in ["none", "None", "N/A", "null", "end", "done", "finish", "complete", ""]:
            s = CompletionSummary(next_agent=val)
            assert s.is_workflow_done is True, f"Expected done for next_agent={val!r}"

    def test_is_workflow_done_false(self):
        s = CompletionSummary(next_agent="summarizer")
        assert s.is_workflow_done is False

    def test_to_dict_round_trip(self):
        original = CompletionSummary(
            status="complete",
            agent_type="design",
            summary="Created design doc",
            key_findings=["finding1"],
            next_agent="code_reviewer",
            verdict="Approved",
        )
        d = original.to_dict()
        restored = CompletionSummary.from_dict(d)
        assert restored.agent_type == original.agent_type
        assert restored.next_agent == original.next_agent
        assert restored.verdict == original.verdict


# ---------------------------------------------------------------------------
# build_completion_comment
# ---------------------------------------------------------------------------


class TestBuildCompletionComment:
    def test_basic_comment(self):
        s = CompletionSummary(summary="Analyzed the issue", agent_type="triage")
        comment = build_completion_comment(s)
        assert "### ✅ Agent Completed" in comment
        assert "Analyzed the issue" in comment

    def test_includes_key_findings(self):
        s = CompletionSummary(key_findings=["bug in auth", "missing test"])
        comment = build_completion_comment(s)
        assert "bug in auth" in comment
        assert "missing test" in comment

    def test_includes_next_agent(self):
        s = CompletionSummary(next_agent="summarizer")
        comment = build_completion_comment(s)
        assert "@Summarizer" in comment

    def test_omits_next_when_workflow_done(self):
        s = CompletionSummary(next_agent="none")
        comment = build_completion_comment(s)
        assert "@none" not in comment

    def test_includes_verdict(self):
        s = CompletionSummary(verdict="Ship it")
        comment = build_completion_comment(s)
        assert "Ship it" in comment

    def test_includes_effort_breakdown(self):
        s = CompletionSummary(effort_breakdown={"review": "2h"})
        comment = build_completion_comment(s)
        assert "review: 2h" in comment


# ---------------------------------------------------------------------------
# generate_completion_instructions
# ---------------------------------------------------------------------------


class TestGenerateCompletionInstructions:
    def test_contains_issue_number(self):
        text = generate_completion_instructions("42", "debug")
        assert "completion_summary_42.json" in text

    def test_contains_agent_type(self):
        text = generate_completion_instructions("1", "triage")
        assert '"agent_type": "triage"' in text

    def test_contains_workflow_steps(self):
        steps = "**Workflow Steps:**\n- 1. Triage — triage"
        text = generate_completion_instructions("1", "triage", workflow_steps_text=steps)
        assert "Workflow Steps" in text

    def test_custom_nexus_dir(self):
        text = generate_completion_instructions("1", "triage", project_name="myproject", nexus_dir=".custom")
        assert ".custom/tasks/myproject/completions" in text


# ---------------------------------------------------------------------------
# scan_for_completions
# ---------------------------------------------------------------------------


class TestScanForCompletions:
    def test_finds_completion_files(self, tmp_path):
        # Create valid structure: base/.nexus/tasks/myproject/completions/completion_summary_42.json
        completion_dir = tmp_path / ".nexus" / "tasks" / "myproject" / "completions"
        completion_dir.mkdir(parents=True)
        data = {"status": "complete", "agent_type": "debug", "summary": "done"}
        (completion_dir / "completion_summary_42.json").write_text(json.dumps(data))

        results = scan_for_completions(str(tmp_path))
        assert len(results) == 1
        assert results[0].issue_number == "42"
        assert results[0].summary.agent_type == "debug"

    def test_ignores_invalid_json(self, tmp_path):
        completion_dir = tmp_path / ".nexus" / "tasks" / "myproject" / "completions"
        completion_dir.mkdir(parents=True)
        (completion_dir / "completion_summary_99.json").write_text("not json{")

        results = scan_for_completions(str(tmp_path))
        assert len(results) == 0

    def test_custom_nexus_dir(self, tmp_path):
        completion_dir = tmp_path / ".custom" / "tasks" / "myproject" / "completions"
        completion_dir.mkdir(parents=True)
        data = {"status": "complete", "agent_type": "triage"}
        (completion_dir / "completion_summary_7.json").write_text(json.dumps(data))

        results = scan_for_completions(str(tmp_path), nexus_dir=".custom")
        assert len(results) == 1
        assert results[0].issue_number == "7"

    def test_dedup_key_includes_agent_type(self, tmp_path):
        completion_dir = tmp_path / ".nexus" / "tasks" / "myproject" / "completions"
        completion_dir.mkdir(parents=True)
        data = {"status": "complete", "agent_type": "debug"}
        (completion_dir / "completion_summary_10.json").write_text(json.dumps(data))

        results = scan_for_completions(str(tmp_path))
        assert results[0].dedup_key == "10:debug:completion_summary_10.json"


# ---------------------------------------------------------------------------
# DetectedCompletion
# ---------------------------------------------------------------------------


class TestDetectedCompletion:
    def test_dedup_key_format(self):
        s = CompletionSummary(agent_type="design")
        d = DetectedCompletion(
            file_path="/some/path/completion_summary_5.json",
            issue_number="5",
            summary=s,
        )
        assert d.dedup_key == "5:design:completion_summary_5.json"
