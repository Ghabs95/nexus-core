from types import SimpleNamespace

from nexus.core.workflow_runtime import workflow_control_service as svc


def test_prepare_continue_context_prefers_recovered_next_agent_over_stale_running_step(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(svc, "NEXUS_STORAGE_BACKEND", "filesystem", raising=False)

    completion_file = tmp_path / "completion_summary_106.json"
    completion_file.write_text("{}", encoding="utf-8")

    completion = SimpleNamespace(
        issue_number="106",
        file_path=str(completion_file),
        summary=SimpleNamespace(
            is_workflow_done=False,
            agent_type="designer",
            next_agent="developer",
        ),
    )

    ctx = svc.prepare_continue_context(
        issue_num="106",
        project_key="nexus",
        rest_tokens=[],
        base_dir=str(tmp_path),
        project_config={"nexus": {"agents_dir": "agents", "workspace": "."}},
        default_repo="Ghabs95/nexus-arc",
        find_task_file_by_issue=lambda _n: None,
        get_issue_details=lambda _n, _repo=None: {"state": "open", "title": "x", "body": "y"},
        resolve_project_config_from_task=lambda _p: (
            "nexus",
            {"agents_dir": "agents", "workspace": "."},
        ),
        get_runtime_ops_plugin=lambda **_k: SimpleNamespace(
            find_agent_pid_for_issue=lambda _n: None
        ),
        scan_for_completions=lambda _base: [completion],
        normalize_agent_reference=lambda ref: str(ref or "").strip().lower() or None,
        get_expected_running_agent_from_workflow=lambda _n: "designer",
        get_sop_tier_from_issue=lambda _n, _p: None,
        get_sop_tier=lambda _t: ("full", None, None),
    )

    assert ctx["status"] == "ready"
    assert ctx["resumed_from"] == "designer"
    assert ctx["agent_type"] == "developer"
    assert ctx["sync_workflow_to_agent"] is True


def test_prepare_continue_context_checks_other_project_repos_and_rebinds_project(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(svc, "NEXUS_STORAGE_BACKEND", "filesystem", raising=False)

    seen_repos: list[str | None] = []

    def _get_issue_details(_issue_num, repo=None, requester_nexus_id=None):
        seen_repos.append(repo)
        if repo == "mybiohackingdata/biome-literature":
            return {
                "state": "open",
                "title": "Biome issue",
                "body": "Investigate anomaly",
                "labels": [],
            }
        return None

    ctx = svc.prepare_continue_context(
        issue_num="119",
        project_key="nexus",
        rest_tokens=["from:developer"],
        base_dir=str(tmp_path),
        project_config={
            "nexus": {
                "agents_dir": "agents/nexus",
                "workspace": "nexus",
                "git_repo": "Ghabs95/nexus-arc",
                "git_repos": ["Ghabs95/nexus-arc", "Ghabs95/nexus"],
            },
            "biome": {
                "agents_dir": "agents/biome",
                "workspace": "biome",
                "git_repo": "mybiohackingdata/biome-literature",
                "git_repos": ["mybiohackingdata/biome-literature"],
            },
        },
        default_repo="Ghabs95/nexus-arc",
        find_task_file_by_issue=lambda _n: None,
        get_issue_details=_get_issue_details,
        resolve_project_config_from_task=lambda _p: (
            "nexus",
            {"agents_dir": "agents/nexus", "workspace": "nexus"},
        ),
        get_runtime_ops_plugin=lambda **_k: SimpleNamespace(
            find_agent_pid_for_issue=lambda _n: None
        ),
        scan_for_completions=lambda _base: [],
        normalize_agent_reference=lambda ref: str(ref or "").strip().lower() or None,
        get_expected_running_agent_from_workflow=lambda _n: None,
        get_sop_tier_from_issue=lambda _n, project: "full" if project == "biome" else None,
        get_sop_tier=lambda _t: ("fast", None, None),
    )

    assert ctx["status"] == "ready"
    assert ctx["repo"] == "mybiohackingdata/biome-literature"
    assert ctx["log_subdir"] == "biome"
    assert ctx["issue_url"].endswith("/mybiohackingdata/biome-literature/issues/119")
    assert seen_repos == [
        "Ghabs95/nexus-arc",
        "Ghabs95/nexus",
        "mybiohackingdata/biome-literature",
    ]
