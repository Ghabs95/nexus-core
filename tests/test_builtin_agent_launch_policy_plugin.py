"""Tests for built-in agent launch policy plugin."""

from nexus.plugins.builtin.agent_launch_policy_plugin import AgentLaunchPolicyPlugin


def test_get_workflow_name_mapping():
    plugin = AgentLaunchPolicyPlugin()

    assert plugin.get_workflow_name("fast-track") == "bug_fix"
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
