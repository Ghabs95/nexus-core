from pathlib import Path
from unittest.mock import MagicMock

from services.webhook_issue_service import handle_issue_opened_event


class _Policy:
    def build_issue_closed_message(self, event):
        return f"closed:{event.get('number')}"

    def build_issue_created_message(self, event, agent_type):
        return f"created:{event.get('number')}:{agent_type}"


def test_handle_issue_opened_event_blocks_unmapped_repository(tmp_path):
    alerts = []
    result = handle_issue_opened_event(
        event={
            "action": "opened",
            "number": 55,
            "title": "Boundary test",
            "body": "Body",
            "author": "alice",
            "url": "https://github.com/unknown/repo/issues/55",
            "labels": [],
            "repo": "unknown/repo",
        },
        logger=MagicMock(),
        policy=_Policy(),
        notify_lifecycle=lambda _m: True,
        emit_alert=lambda msg, **kwargs: alerts.append((msg, kwargs)),
        project_config={"proj-a": {"workspace": "ws-a", "git_repo": "acme/repo"}},
        base_dir=str(tmp_path),
        project_repos=lambda key, cfg, get_repos: ["acme/repo"],
        get_repos=lambda _key: ["acme/repo"],
        get_tasks_active_dir=lambda root, project: str(tmp_path / "active"),
        get_inbox_dir=lambda root, project: str(tmp_path / "inbox"),
    )

    assert result["status"] == "ignored"
    assert result["reason"] == "unmapped_repository"
    assert len(alerts) == 1


def test_handle_issue_opened_event_creates_task_file(tmp_path):
    notifications = []
    inbox_dir = tmp_path / "workspace-a" / ".nexus" / "inbox" / "proj-a"

    result = handle_issue_opened_event(
        event={
            "action": "opened",
            "number": 77,
            "title": "Cross-repo feature",
            "body": "Implement backend + mobile",
            "author": "alice",
            "url": "https://github.com/acme/sampleco-mobile/issues/77",
            "labels": [],
            "repo": "acme/sampleco-mobile",
        },
        logger=MagicMock(),
        policy=_Policy(),
        notify_lifecycle=lambda m: notifications.append(m) or True,
        emit_alert=lambda *args, **kwargs: True,
        project_config={
            "sampleco": {
                "workspace": "workspace-a",
                "git_repo": "acme/sampleco-backend",
                "git_repos": ["acme/sampleco-backend", "acme/sampleco-mobile"],
            },
            "issue_triage": {"default_agent_type": "triage"},
        },
        base_dir=str(tmp_path),
        project_repos=lambda key, cfg, get_repos: cfg.get("git_repos", [cfg.get("git_repo")]),
        get_repos=lambda _key: [],
        get_tasks_active_dir=lambda root, project: str(
            tmp_path / "workspace-a" / ".nexus" / "tasks" / project / "active"
        ),
        get_inbox_dir=lambda root, project: str(inbox_dir),
    )

    assert result["status"] == "task_created"
    task_path = Path(result["task_file"])
    assert task_path.exists()
    assert "Source:** webhook" in task_path.read_text()
    assert notifications
