from types import SimpleNamespace


def test_run_coro_sync_closes_coroutine_on_asyncio_run_failure(monkeypatch):
    from nexus.core.runtime import agent_launcher

    class _CoroLike:
        def __init__(self):
            self.closed = False

        def __await__(self):
            yield
            return None

        def close(self):
            self.closed = True

    leaked = _CoroLike()

    monkeypatch.setattr(agent_launcher.asyncio, "get_running_loop", lambda: (_ for _ in ()).throw(RuntimeError("no loop")))

    def _raise_runtime(_candidate):
        raise RuntimeError("boom")

    monkeypatch.setattr(agent_launcher.asyncio, "run", _raise_runtime)

    value = agent_launcher._run_coro_sync(lambda: leaked)

    assert value is None
    assert leaked.closed is True


def test_get_sop_tier_from_issue_uses_sync_bridge(monkeypatch):
    from nexus.core.runtime import agent_launcher

    class _Platform:
        async def get_issue(self, _issue_number):
            return SimpleNamespace(labels=["workflow:full"])

    monkeypatch.setattr(agent_launcher, "get_repo", lambda _project: "Ghabs95/nexus-arc")
    monkeypatch.setattr(
        "nexus.core.orchestration.nexus_core_helpers.get_git_platform",
        lambda *_args, **_kwargs: _Platform(),
    )
    monkeypatch.setattr(agent_launcher, "_resolve_requester_token_for_issue", lambda *_a, **_k: None)

    tier = agent_launcher.get_sop_tier_from_issue("110", project="nexus")
    assert tier == "full"


def test_get_sop_tier_from_issue_does_not_call_asyncio_run_directly(monkeypatch):
    from nexus.core.runtime import agent_launcher

    class _Platform:
        def get_issue(self, _issue_number):
            return SimpleNamespace(labels=["workflow:shortened"])

    bridge_calls = {"count": 0}

    def _fake_sync_bridge(coro_factory):
        bridge_calls["count"] += 1
        return coro_factory()

    monkeypatch.setattr(agent_launcher, "_run_coro_sync", _fake_sync_bridge)
    monkeypatch.setattr(agent_launcher, "get_repo", lambda _project: "Ghabs95/nexus-arc")
    monkeypatch.setattr(
        "nexus.core.orchestration.nexus_core_helpers.get_git_platform",
        lambda *_args, **_kwargs: _Platform(),
    )
    monkeypatch.setattr(agent_launcher, "_resolve_requester_token_for_issue", lambda *_a, **_k: None)

    tier = agent_launcher.get_sop_tier_from_issue("113", project="nexus")

    assert tier == "shortened"
    assert bridge_calls["count"] == 1


def test_resolve_requester_token_for_issue_falls_back_to_issue_url(monkeypatch):
    from nexus.core.runtime import agent_launcher

    monkeypatch.setattr(agent_launcher, "auth_enabled", lambda: True)
    monkeypatch.setattr(agent_launcher, "get_issue_requester", lambda *_a, **_k: None)
    monkeypatch.setattr(agent_launcher, "get_issue_requester_by_url", lambda _url: "user-123")
    monkeypatch.setattr(
        agent_launcher,
        "build_execution_env",
        lambda _nexus_id: ({"GITHUB_TOKEN": "gho_test_token"}, None),
    )
    monkeypatch.setattr(agent_launcher, "get_project_platform", lambda _project: "github")

    token = agent_launcher._resolve_requester_token_for_issue(
        issue_number="110",
        repo="Ghabs95/nexus-arc",
        project_name="nexus",
    )

    assert token == "gho_test_token"


def test_invoke_ai_agent_pauses_and_notifies_when_worktree_provision_fails(monkeypatch, tmp_path):
    from nexus.core.runtime import agent_launcher
    from nexus.core.workspace import WorktreeProvisionError

    pause_calls = {"count": 0}
    alerts: list[str] = []
    audits: list[tuple[int, str, str]] = []

    class _Orchestrator:
        def invoke_agent(self, **kwargs):  # noqa: ANN003
            raise AssertionError("invoke_agent should not be called when worktree setup fails")

    monkeypatch.setattr(agent_launcher, "_get_launch_policy_plugin", lambda: None)
    monkeypatch.setattr(agent_launcher, "_resolve_workflow_path", lambda _project: None)
    monkeypatch.setattr(agent_launcher, "_resolve_issue_step_context", lambda _url: ("", 0))
    monkeypatch.setattr(agent_launcher, "_ensure_agent_definition", lambda *_a, **_k: True)
    monkeypatch.setattr(agent_launcher, "get_orchestrator", lambda _cfg: _Orchestrator())
    monkeypatch.setattr(agent_launcher, "auth_enabled", lambda: False)
    monkeypatch.setattr(agent_launcher, "_resolve_worktree_base_repo", lambda *_a, **_k: str(tmp_path))
    monkeypatch.setattr(agent_launcher, "_is_git_repo", lambda _path: True)
    monkeypatch.setattr(agent_launcher, "_load_issue_body_from_project_repo", lambda *_a, **_k: ("", "", ""))
    monkeypatch.setattr(agent_launcher, "_derive_issue_branch_name", lambda **_k: "nexus/issue-42")
    monkeypatch.setattr(agent_launcher, "get_repo_branch", lambda *_a, **_k: "develop")
    monkeypatch.setattr(agent_launcher, "_run_coro_sync", lambda coro_factory: coro_factory())

    def _pause_workflow(issue_number, reason):  # noqa: ANN001
        pause_calls["count"] += 1
        assert str(issue_number) == "42"
        assert "worktree" in str(reason).lower()
        return True

    monkeypatch.setattr(
        "nexus.core.orchestration.nexus_core_helpers.pause_workflow",
        _pause_workflow,
    )

    monkeypatch.setattr(
        agent_launcher,
        "emit_alert",
        lambda message, **_kwargs: alerts.append(str(message)),
    )
    monkeypatch.setattr(
        agent_launcher.AuditStore,
        "audit_log",
        lambda issue, event, message: audits.append((issue, event, str(message))),
    )

    monkeypatch.setattr(
        "nexus.core.workspace.WorkspaceManager.provision_worktree",
        lambda *_a, **_k: (_ for _ in ()).throw(
            WorktreeProvisionError("no isolated worktree available")
        ),
    )

    pid, tool = agent_launcher.invoke_ai_agent(
        agents_dir=str(tmp_path / "agents"),
        workspace_dir=str(tmp_path),
        issue_url="https://github.com/Ghabs95/nexus-arc/issues/42",
        tier_name="full",
        task_content="test",
        continuation=False,
        agent_type="developer",
        project_name="nexus",
        log_subdir="nexus",
    )

    assert pid is None
    assert tool is None
    assert pause_calls["count"] == 1
    assert alerts and "Could not create isolated worktree" in alerts[0]
    assert audits and audits[0][1] == "WORKTREE_PROVISION_FAILED"


def test_invoke_ai_agent_sanitizes_deprecated_helper_scripts(monkeypatch, tmp_path):
    from nexus.core.runtime import agent_launcher

    sanitized: dict[str, str] = {}

    class _Orchestrator:
        def invoke_agent(self, **kwargs):  # noqa: ANN003
            return 1234, "gemini"

    monkeypatch.setattr(agent_launcher, "_get_launch_policy_plugin", lambda: None)
    monkeypatch.setattr(agent_launcher, "_resolve_workflow_path", lambda _project: None)
    monkeypatch.setattr(agent_launcher, "_resolve_issue_step_context", lambda _url: ("develop", 2))
    monkeypatch.setattr(agent_launcher, "_ensure_agent_definition", lambda *_a, **_k: True)
    monkeypatch.setattr(agent_launcher, "get_orchestrator", lambda _cfg: _Orchestrator())
    monkeypatch.setattr(agent_launcher, "auth_enabled", lambda: False)
    monkeypatch.setattr(agent_launcher, "_resolve_worktree_base_repo", lambda *_a, **_k: str(tmp_path))
    monkeypatch.setattr(agent_launcher, "_is_git_repo", lambda _path: True)
    monkeypatch.setattr(agent_launcher, "_load_issue_body_from_project_repo", lambda *_a, **_k: ("", "", ""))
    monkeypatch.setattr(agent_launcher, "_derive_issue_branch_name", lambda **_k: "nexus/issue-42")
    monkeypatch.setattr(agent_launcher, "get_repo_branch", lambda *_a, **_k: "develop")
    monkeypatch.setattr(agent_launcher, "_run_coro_sync", lambda coro_factory: coro_factory())

    monkeypatch.setattr(
        "nexus.core.workspace.WorkspaceManager.provision_worktree",
        lambda *_a, **_k: str(tmp_path),
    )
    monkeypatch.setattr(
        "nexus.core.workspace.WorkspaceManager.sanitize_worktree_helper_scripts",
        lambda path: sanitized.setdefault("path", str(path)) and [str(tmp_path / "post_comments.py")],
    )

    pid, tool = agent_launcher.invoke_ai_agent(
        agents_dir=str(tmp_path / "agents"),
        workspace_dir=str(tmp_path),
        issue_url="https://github.com/Ghabs95/nexus-arc/issues/42",
        tier_name="full",
        task_content="test",
        continuation=False,
        agent_type="developer",
        project_name="nexus",
        log_subdir="nexus",
    )

    assert pid == 1234
    assert tool == "gemini"
    assert sanitized.get("path") == str(tmp_path)


def test_launch_next_agent_merges_persisted_and_runtime_exclusions(monkeypatch, tmp_path):
    from nexus.core.runtime import agent_launcher

    task_file = tmp_path / "issue-113-task.md"
    task_file.write_text("Do work")
    issue_body = f"**Task File:** `{task_file}`\n\nTask details"
    captured: dict[str, object] = {}

    monkeypatch.setattr(agent_launcher, "is_recent_launch", lambda *_a, **_k: False)
    monkeypatch.setattr(
        agent_launcher,
        "_load_issue_body_from_project_repo",
        lambda *_a, **_k: (issue_body, "Ghabs95/nexus-arc", str(task_file)),
    )
    monkeypatch.setattr(agent_launcher, "is_postgres_backend", lambda *_a, **_k: False)
    monkeypatch.setattr(
        agent_launcher,
        "PROJECT_CONFIG",
        {
            "nexus": {
                "workspace": str(tmp_path),
                "agents_dir": "agents",
                "repo": "Ghabs95/nexus-arc",
            }
        },
    )
    monkeypatch.setattr(agent_launcher, "_project_repos", lambda *_a, **_k: ["Ghabs95/nexus-arc"])
    monkeypatch.setattr(agent_launcher, "get_repo", lambda _project: "Ghabs95/nexus-arc")
    monkeypatch.setattr(
        "nexus.core.state_manager.HostStateManager.get_last_tier_for_issue",
        lambda _issue: "full",
    )
    monkeypatch.setattr(
        "nexus.core.state_manager.HostStateManager.load_launched_agents",
        lambda recent_only=False: {"113": {"exclude_tools": ["codex", "copilot"]}},
    )
    monkeypatch.setattr(agent_launcher, "get_sop_tier_from_issue", lambda *_a, **_k: "full")
    monkeypatch.setattr(
        agent_launcher,
        "build_issue_url",
        lambda _repo, _issue, _cfg: "https://github.com/Ghabs95/nexus-arc/issues/113",
    )

    def _fake_invoke_ai_agent(**kwargs):
        captured.update(kwargs)
        return 4242, "claude"

    monkeypatch.setattr(agent_launcher, "invoke_ai_agent", _fake_invoke_ai_agent)

    pid, tool = agent_launcher.launch_next_agent(
        issue_number="113",
        next_agent="developer",
        trigger_source="dead-agent-retry",
        exclude_tools=["gemini"],
        repo_override="Ghabs95/nexus-arc",
    )

    assert pid == 4242
    assert tool == "claude"
    assert captured.get("exclude_tools") == ["codex", "copilot", "gemini"]
