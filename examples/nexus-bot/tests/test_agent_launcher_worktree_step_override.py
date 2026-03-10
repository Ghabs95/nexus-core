def test_example_workflow_step_can_disable_worktree(tmp_path):
    from nexus.core.runtime import agent_launcher

    workflow = tmp_path / "sample-workflow.yaml"
    workflow.write_text(
        "steps:\n"
        "  - id: triage-routing\n"
        "    agent_type: triage\n"
        "    requires_worktree: false\n",
        encoding="utf-8",
    )

    requires_worktree = agent_launcher._resolve_step_requires_worktree(
        workflow_path=str(workflow),
        tier_name="full",
        workflow_name="new_feature",
        step_id="triage-routing",
        step_num=1,
    )

    assert requires_worktree is False
