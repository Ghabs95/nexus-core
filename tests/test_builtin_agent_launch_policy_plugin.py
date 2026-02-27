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


def test_prompt_prefix_is_stable_for_different_issue_context():
    plugin = AgentLaunchPolicyPlugin()
    p1 = plugin.build_agent_prompt(
        issue_url="https://github.com/org/repo/issues/10",
        tier_name="full",
        task_content="Implement feature A",
        agent_type="triage",
        continuation=False,
        workflow_path="",
        nexus_dir=".nexus",
    )
    p2 = plugin.build_agent_prompt(
        issue_url="https://github.com/org/repo/issues/11",
        tier_name="full",
        task_content="Implement feature B",
        agent_type="triage",
        continuation=False,
        workflow_path="",
        nexus_dir=".nexus",
    )

    assert p1[:220] == p2[:220]
