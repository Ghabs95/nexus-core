"""Tests for repository-native knowledge alignment service."""

from nexus.core.knowledge_alignment import KnowledgeAlignmentService


def test_evaluate_returns_ranked_artifacts_and_gaps(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "ADR-001-feature-alignment.md").write_text(
        "# Feature Alignment\n\n## Scope\nEvaluate feature requests against ADR history.\n",
        encoding="utf-8",
    )
    (docs_dir / "ARCHITECTURE.md").write_text(
        "# Architecture\n\nWorkflow engine and deterministic outputs.\n",
        encoding="utf-8",
    )

    service = KnowledgeAlignmentService()
    result = service.evaluate(
        request_text="Evaluate feature alignment against workflow ADR docs",
        workflow_type="full",
        repo_path=str(tmp_path),
        max_hits=2,
    )

    assert result.alignment_score > 0
    assert result.matched_artifacts
    assert result.matched_artifacts[0].path_or_url.startswith("docs/")
    assert isinstance(result.gaps, list)
    assert result.alignment_summary


def test_evaluate_fails_open_when_no_docs(tmp_path):
    service = KnowledgeAlignmentService()
    result = service.evaluate(
        request_text="Feature alignment knowledge base",
        workflow_type="full",
        repo_path=str(tmp_path),
        max_hits=3,
    )

    assert result.alignment_score == 0
    assert result.matched_artifacts == []
    assert result.recommended_next_actions
