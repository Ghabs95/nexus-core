from __future__ import annotations

from types import SimpleNamespace

from nexus.core.git_sync.workflow_start_sync_service import sync_project_repos_on_workflow_start


def test_sync_service_skips_when_disabled():
    result = sync_project_repos_on_workflow_start(
        issue_number="42",
        project_name="proj",
        project_cfg={},
        resolve_git_dirs=lambda _p: {},
        resolve_git_dir=lambda _p: None,
        get_repos=lambda _p: [],
        get_repo_branch=lambda _project, _repo: "main",
    )

    assert result["enabled"] is False
    assert result["skipped"] is True


def test_sync_service_fetches_all_resolved_repos(monkeypatch):
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("nexus.core.git_sync.workflow_start_sync_service.subprocess.run", _fake_run)

    result = sync_project_repos_on_workflow_start(
        issue_number="42",
        project_name="proj",
        project_cfg={"git_sync": {"on_workflow_start": True}},
        resolve_git_dirs=lambda _p: {"acme/backend": "/tmp/backend", "acme/mobile": "/tmp/mobile"},
        resolve_git_dir=lambda _p: None,
        get_repos=lambda _p: [],
        get_repo_branch=lambda _project, repo: "develop" if repo.endswith("backend") else "release",
    )

    assert result["blocked"] is False
    assert len(result["synced"]) == 2
    assert calls[0][-1] == "develop:refs/remotes/origin/develop"
    assert calls[1][-1] == "release:refs/remotes/origin/release"


def test_sync_service_retries_network_auth_then_continues(monkeypatch):
    attempts = {"count": 0}
    slept: list[float] = []
    alerts: list[dict] = []

    def _fake_run(_cmd, **_kwargs):
        attempts["count"] += 1
        return SimpleNamespace(returncode=1, stdout="", stderr="fatal: could not resolve host")

    monkeypatch.setattr("nexus.core.git_sync.workflow_start_sync_service.subprocess.run", _fake_run)

    result = sync_project_repos_on_workflow_start(
        issue_number="77",
        project_name="proj",
        project_cfg={
            "git_sync": {
                "on_workflow_start": True,
                "network_auth_retries": 2,
                "retry_backoff_seconds": 3,
                "decision_timeout_seconds": 5,
            }
        },
        resolve_git_dirs=lambda _p: {"acme/backend": "/tmp/backend"},
        resolve_git_dir=lambda _p: None,
        get_repos=lambda _p: [],
        get_repo_branch=lambda _project, _repo: "main",
        emit_alert=lambda *args, **kwargs: alerts.append({"args": args, "kwargs": kwargs}),
        should_block_launch=lambda _issue, _project: False,
        sleep_fn=lambda value: slept.append(value),
    )

    assert attempts["count"] == 3
    assert result["blocked"] is False
    assert result["failures"][0]["kind"] == "network_auth"
    assert len(alerts) == 1
    assert slept[:2] == [3.0, 3.0]


def test_sync_service_blocks_when_stop_decision_detected(monkeypatch):
    attempts = {"count": 0}

    def _fake_run(_cmd, **_kwargs):
        attempts["count"] += 1
        return SimpleNamespace(returncode=1, stdout="", stderr="authentication failed")

    monkeypatch.setattr("nexus.core.git_sync.workflow_start_sync_service.subprocess.run", _fake_run)

    result = sync_project_repos_on_workflow_start(
        issue_number="88",
        project_name="proj",
        project_cfg={
            "git_sync": {
                "on_workflow_start": True,
                "network_auth_retries": 1,
                "retry_backoff_seconds": 1,
                "decision_timeout_seconds": 10,
            }
        },
        resolve_git_dirs=lambda _p: {"acme/backend": "/tmp/backend"},
        resolve_git_dir=lambda _p: None,
        get_repos=lambda _p: [],
        get_repo_branch=lambda _project, _repo: "main",
        should_block_launch=lambda _issue, _project: True,
        sleep_fn=lambda _value: None,
    )

    assert attempts["count"] == 2
    assert result["blocked"] is True


def test_sync_service_non_network_failure_warns_and_continues(monkeypatch):
    attempts = {"count": 0}

    def _fake_run(_cmd, **_kwargs):
        attempts["count"] += 1
        return SimpleNamespace(returncode=1, stdout="", stderr="fatal: bad revision")

    monkeypatch.setattr("nexus.core.git_sync.workflow_start_sync_service.subprocess.run", _fake_run)

    result = sync_project_repos_on_workflow_start(
        issue_number="99",
        project_name="proj",
        project_cfg={"git_sync": {"on_workflow_start": True, "network_auth_retries": 5}},
        resolve_git_dirs=lambda _p: {"acme/backend": "/tmp/backend"},
        resolve_git_dir=lambda _p: None,
        get_repos=lambda _p: [],
        get_repo_branch=lambda _project, _repo: "main",
    )

    assert attempts["count"] == 1
    assert result["blocked"] is False
    assert result["failures"][0]["kind"] == "other"


def test_sync_service_bootstraps_missing_workspace_and_repo_then_fetches(monkeypatch):
    calls: list[tuple[list[str], str | None]] = []
    ensured: dict[str, int] = {"count": 0}

    def _fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("cwd")))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("nexus.core.git_sync.workflow_start_sync_service.subprocess.run", _fake_run)

    def _ensure_workspace(_project: str):
        ensured["count"] += 1
        return "/tmp/workspace"

    result = sync_project_repos_on_workflow_start(
        issue_number="100",
        project_name="proj",
        project_cfg={
            "git_platform": "github",
            "git_sync": {
                "on_workflow_start": True,
                "bootstrap_missing_workspace": True,
                "bootstrap_missing_repos": True,
            },
        },
        resolve_git_dirs=lambda _p: {},
        resolve_git_dir=lambda _p: None,
        resolve_git_dir_for_repo=lambda _project, _repo: "/tmp/workspace/backend",
        ensure_workspace_dir=_ensure_workspace,
        get_repos=lambda _p: ["acme/backend"],
        get_repo_branch=lambda _project, _repo: "develop",
    )

    assert ensured["count"] == 1
    assert len(result["bootstrapped"]) == 1
    assert result["bootstrapped"][0]["branch"] == "develop"
    assert calls[0][0][:5] == ["git", "clone", "--branch", "develop", "--single-branch"]
    assert calls[0][0][-2:] == ["https://github.com/acme/backend.git", "/tmp/workspace/backend"]
    assert calls[0][1] is None
    assert calls[1][0][:4] == ["git", "fetch", "--prune", "origin"]
    assert calls[1][1] == "/tmp/workspace/backend"


def test_sync_service_bootstrap_network_failure_can_block(monkeypatch):
    attempts: dict[str, int] = {"count": 0}

    def _fake_run(_cmd, **_kwargs):
        attempts["count"] += 1
        return SimpleNamespace(returncode=1, stdout="", stderr="could not resolve host")

    monkeypatch.setattr("nexus.core.git_sync.workflow_start_sync_service.subprocess.run", _fake_run)

    result = sync_project_repos_on_workflow_start(
        issue_number="101",
        project_name="proj",
        project_cfg={
            "git_platform": "github",
            "git_sync": {
                "on_workflow_start": True,
                "bootstrap_missing_repos": True,
                "network_auth_retries": 1,
                "decision_timeout_seconds": 5,
            },
        },
        resolve_git_dirs=lambda _p: {},
        resolve_git_dir=lambda _p: None,
        resolve_git_dir_for_repo=lambda _project, _repo: "/tmp/workspace/backend",
        get_repos=lambda _p: ["acme/backend"],
        get_repo_branch=lambda _project, _repo: "main",
        should_block_launch=lambda _issue, _project: True,
        sleep_fn=lambda _value: None,
    )

    assert attempts["count"] == 2
    assert result["blocked"] is True
    assert result["failures"][0]["kind"] == "network_auth"
