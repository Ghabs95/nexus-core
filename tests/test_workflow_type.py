"""Tests for workflow_type normalization and issue label extraction."""

from unittest.mock import MagicMock, patch

import pytest

from nexus.adapters.git.github import GitHubPlatform
from nexus.core.workflow import WorkflowDefinition


class TestNormalizeWorkflowType:
    """Verify WorkflowDefinition.normalize_workflow_type() — passthrough with strip/lower."""

    @pytest.mark.parametrize(
        "input_tier, expected",
        [
            ("full", "full"),
            ("shortened", "shortened"),
            ("fast-track", "fast-track"),
            (" Full ", "full"),
            ("custom-pipeline", "custom-pipeline"),
            ("my_workflow", "my_workflow"),
        ],
    )
    def test_passthrough(self, input_tier: str, expected: str):
        assert WorkflowDefinition.normalize_workflow_type(input_tier) == expected

    def test_unknown_tier_passes_through(self):
        assert WorkflowDefinition.normalize_workflow_type("unknown") == "unknown"

    def test_empty_string_returns_default(self):
        assert WorkflowDefinition.normalize_workflow_type("") == "shortened"

    def test_empty_string_custom_default(self):
        assert WorkflowDefinition.normalize_workflow_type("", default="full") == "full"


class TestGetWorkflowTypeFromIssue:
    """Verify GitHubPlatform.get_workflow_type_from_issue() label parsing."""

    @pytest.fixture
    def platform(self):
        with patch.object(GitHubPlatform, "_check_gh_cli"):
            return GitHubPlatform("owner/repo")

    def _mock_issue_labels(self, platform, labels: list[str]):
        label_data = [{"name": label} for label in labels]
        platform._sync_request = MagicMock(return_value={"labels": label_data})

    def test_full_label(self, platform):
        self._mock_issue_labels(platform, ["workflow:full", "bug"])
        assert platform.get_workflow_type_from_issue(42) == "full"

    def test_shortened_label(self, platform):
        self._mock_issue_labels(platform, ["workflow:shortened"])
        assert platform.get_workflow_type_from_issue(42) == "shortened"

    def test_fast_track_label(self, platform):
        self._mock_issue_labels(platform, ["workflow:fast-track"])
        assert platform.get_workflow_type_from_issue(42) == "fast-track"

    def test_no_matching_label_returns_default(self, platform):
        self._mock_issue_labels(platform, ["bug", "priority:high"])
        assert platform.get_workflow_type_from_issue(42) is None

    def test_no_matching_label_custom_default(self, platform):
        self._mock_issue_labels(platform, ["bug"])
        assert platform.get_workflow_type_from_issue(42, default="fast-track") == "fast-track"

    def test_custom_prefix(self, platform):
        self._mock_issue_labels(platform, ["tier:full"])
        assert platform.get_workflow_type_from_issue(42, label_prefix="tier:") == "full"

    def test_userdefined_tier_label_passes_through(self, platform):
        self._mock_issue_labels(platform, ["workflow:tier-2-standard"])
        assert platform.get_workflow_type_from_issue(42) == "tier-2-standard"

    def test_request_failure_returns_default(self, platform):
        platform._sync_request = MagicMock(side_effect=RuntimeError("network error"))
        assert platform.get_workflow_type_from_issue(42) is None
        assert platform.get_workflow_type_from_issue(42, default="fast-track") == "fast-track"

    def test_first_matching_label_wins(self, platform):
        self._mock_issue_labels(platform, ["workflow:full", "workflow:shortened"])
        assert platform.get_workflow_type_from_issue(42) == "full"
