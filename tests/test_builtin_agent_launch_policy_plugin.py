"""Tests for built-in agent launch policy plugin."""

from nexus.plugins.builtin.agent_launch_policy_plugin import AgentLaunchPolicyPlugin


def test_build_agent_prompt_uses_canonical_workflow_tier():
    plugin = AgentLaunchPolicyPlugin()
    prompt = plugin.build_agent_prompt(
        issue_url="https://github.com/org/repo/issues/123",
        tier_name="fast-track",
        task_content="Implement endpoint",
        agent_type="triage",
        continuation=False,
        workflow_path="",
        nexus_dir=".nexus",
    )

    assert "Workflow Tier: fast-track" in prompt


def test_build_agent_prompt_contains_issue_context():
    plugin = AgentLaunchPolicyPlugin()

    prompt = plugin.build_agent_prompt(
        issue_url="https://github.com/org/repo/issues/123",
        tier_name="full",
        task_content="Implement endpoint",
        agent_type="triage",
        continuation=False,
        workflow_path="",
        nexus_dir=".nexus",
    )

    assert "Issue: https://github.com/org/repo/issues/123" in prompt
    assert "Task details:" in prompt


def test_merge_policy_injected_for_deployer():
    plugin = AgentLaunchPolicyPlugin()
    prompt = plugin.build_agent_prompt(
        issue_url="https://github.com/org/repo/issues/10",
        tier_name="full",
        task_content="Deploy changes",
        agent_type="deployer",
        continuation=True,
        continuation_prompt="Previous step complete.",
        workflow_path="",
        nexus_dir=".nexus",
    )
    assert "MUST NOT run `gh pr merge`" in prompt
    assert "merge_queue.review_mode" in prompt


def test_merge_policy_injected_for_ops():
    plugin = AgentLaunchPolicyPlugin()
    prompt = plugin.build_agent_prompt(
        issue_url="https://github.com/org/repo/issues/10",
        tier_name="full",
        task_content="Deploy changes",
        agent_type="ops",
        continuation=True,
        continuation_prompt="Previous step complete.",
        workflow_path="",
        nexus_dir=".nexus",
    )
    assert "MUST NOT run `gh pr merge`" in prompt


def test_merge_policy_not_injected_for_developer():
    plugin = AgentLaunchPolicyPlugin()
    prompt = plugin.build_agent_prompt(
        issue_url="https://github.com/org/repo/issues/10",
        tier_name="full",
        task_content="Implement feature",
        agent_type="developer",
        continuation=True,
        continuation_prompt="Previous step complete.",
        workflow_path="",
        nexus_dir=".nexus",
    )
    assert "MUST NOT run `gh pr merge`" not in prompt


def test_build_agent_prompt_includes_alignment_report(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "ADR-042.md").write_text(
        "# Feature Alignment\n\nEvaluate feature alignment using repository docs.",
        encoding="utf-8",
    )

    plugin = AgentLaunchPolicyPlugin()
    prompt = plugin.build_agent_prompt(
        issue_url="https://github.com/org/repo/issues/11",
        tier_name="full",
        task_content="Evaluate feature alignment knowledge base",
        agent_type="developer",
        continuation=True,
        continuation_prompt="Previous step complete.",
        workflow_path="",
        nexus_dir=".nexus",
        repo_path=str(tmp_path),
    )
    assert "Feature Alignment Report" in prompt
    assert "Alignment score:" in prompt
    assert "docs/ADR-042.md" in prompt


def test_designer_prompt_includes_alignment_output_contract():
    plugin = AgentLaunchPolicyPlugin()
    prompt = plugin.build_agent_prompt(
        issue_url="https://github.com/org/repo/issues/12",
        tier_name="full",
        task_content="Design feature alignment",
        agent_type="designer",
        continuation=True,
        continuation_prompt="Previous step complete.",
        workflow_path="",
        nexus_dir=".nexus",
    )
    assert "Designer Output Contract (required for this feature)" in prompt
    assert "alignment_score" in prompt
