"""Tests for built-in agent launch policy plugin."""

from nexus.plugins.builtin.agent_launch_policy_plugin import AgentLaunchPolicyPlugin


def test_get_workflow_name_mapping():
    plugin = AgentLaunchPolicyPlugin()

    assert plugin.get_workflow_name("fast-track") == "fast_track"
    assert plugin.get_workflow_name("shortened") == "bug_fix"
    assert plugin.get_workflow_name("full") == "new_feature"


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
    assert "require_human_merge_approval" in prompt


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
