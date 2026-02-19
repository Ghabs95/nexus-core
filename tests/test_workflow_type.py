"""Tests for workflow_type normalization and issue label extraction."""
import json
import pytest
from unittest.mock import patch, MagicMock

from nexus.core.workflow import WorkflowDefinition
from nexus.adapters.git.github import GitHubPlatform


class TestNormalizeWorkflowType:
    """Verify WorkflowDefinition.normalize_workflow_type() mappings."""

    @pytest.mark.parametrize(
        "input_tier, expected",
        [
            # Canonical values pass through
            ("full", "full"),
            ("shortened", "shortened"),
            ("fast-track", "fast-track"),
            # Legacy numeric tiers
            ("tier-1-simple", "fast-track"),
            ("tier-2-standard", "shortened"),
            ("tier-3-complex", "full"),
            ("tier-4-critical", "full"),
            # Underscore variant
            ("fast_track", "fast-track"),
            # Workflow-name aliases
            ("new_feature", "full"),
            ("bug_fix", "shortened"),
            ("hotfix", "fast-track"),
            # Whitespace/case normalization
            (" Full ", "full"),
        ],
    )
    def test_known_tiers(self, input_tier: str, expected: str):
        assert WorkflowDefinition.normalize_workflow_type(input_tier) == expected

    def test_unknown_tier_returns_default(self):
        assert WorkflowDefinition.normalize_workflow_type("unknown") == "shortened"

    def test_unknown_tier_custom_default(self):
        assert WorkflowDefinition.normalize_workflow_type("unknown", default="full") == "full"

    def test_empty_string_returns_default(self):
        assert WorkflowDefinition.normalize_workflow_type("") == "shortened"


class TestGetWorkflowTypeFromIssue:
    """Verify GitHubPlatform.get_workflow_type_from_issue() label parsing."""

    @pytest.fixture
    def platform(self):
        with patch.object(GitHubPlatform, "_check_gh_cli"):
            return GitHubPlatform("owner/repo")

    def _mock_gh_labels(self, platform, labels: list[str]):
        """Set up _run_gh_command to return the given labels."""
        label_data = [{"name": l} for l in labels]
        platform._run_gh_command = MagicMock(
            return_value=json.dumps({"labels": label_data})
        )

    def test_full_label(self, platform):
        self._mock_gh_labels(platform, ["workflow:full", "bug"])
        assert platform.get_workflow_type_from_issue(42) == "full"

    def test_shortened_label(self, platform):
        self._mock_gh_labels(platform, ["workflow:shortened"])
        assert platform.get_workflow_type_from_issue(42) == "shortened"

    def test_fast_track_label(self, platform):
        self._mock_gh_labels(platform, ["workflow:fast-track"])
        assert platform.get_workflow_type_from_issue(42) == "fast-track"

    def test_no_matching_label_returns_default(self, platform):
        self._mock_gh_labels(platform, ["bug", "priority:high"])
        assert platform.get_workflow_type_from_issue(42) is None

    def test_no_matching_label_custom_default(self, platform):
        self._mock_gh_labels(platform, ["bug"])
        assert platform.get_workflow_type_from_issue(42, default="fast-track") == "fast-track"

    def test_custom_prefix(self, platform):
        self._mock_gh_labels(platform, ["tier:full"])
        assert platform.get_workflow_type_from_issue(42, label_prefix="tier:") == "full"

    def test_legacy_tier_label_normalized(self, platform):
        """A label like workflow:tier-2-standard gets normalized to shortened."""
        self._mock_gh_labels(platform, ["workflow:tier-2-standard"])
        assert platform.get_workflow_type_from_issue(42) == "shortened"

    def test_gh_command_failure_returns_default(self, platform):
        platform._run_gh_command = MagicMock(side_effect=RuntimeError("network error"))
        assert platform.get_workflow_type_from_issue(42) is None
        assert platform.get_workflow_type_from_issue(42, default="fast-track") == "fast-track"

    def test_first_matching_label_wins(self, platform):
        self._mock_gh_labels(platform, ["workflow:full", "workflow:shortened"])
        assert platform.get_workflow_type_from_issue(42) == "full"
