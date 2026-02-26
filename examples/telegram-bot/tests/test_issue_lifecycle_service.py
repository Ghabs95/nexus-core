import types
from pathlib import Path
from unittest.mock import patch

from services import issue_lifecycle_service as svc


class _FakePlatform:
    def __init__(self):
        self.calls = []

    async def create_issue(self, title, body, labels=None):
        self.calls.append(("create_issue", title, body, labels))
        if labels is not None and any(lbl.startswith("workflow:") for lbl in labels):
            raise RuntimeError("label missing")
        return types.SimpleNamespace(number=12, url="https://github.com/acme/repo/issues/12")

    async def ensure_label(self, name, *, color, description=""):
        self.calls.append(("ensure_label", name, color, description))
        return True

    async def update_issue(self, issue_id, **kwargs):
        self.calls.append(("update_issue", issue_id, kwargs))
        return None


def test_create_issue_fallbacks_without_labels_and_reapplies_labels():
    platform = _FakePlatform()
    with patch.object(svc, "get_git_platform", return_value=platform):
        url = svc.create_issue(
            title="Title",
            body="Body",
            project="proj-a",
            workflow_label="workflow:full",
            task_type="feature",
            repo_key="acme/repo",
        )

    assert url.endswith("/12")
    create_calls = [c for c in platform.calls if c[0] == "create_issue"]
    assert len(create_calls) == 2
    assert create_calls[0][3] is not None
    assert create_calls[1][3] is None
    assert any(c[0] == "ensure_label" for c in platform.calls)
    assert any(c[0] == "update_issue" and c[1] == "12" for c in platform.calls)


def test_rename_task_file_and_sync_issue_body_updates_remote_body(tmp_path):
    task_file = tmp_path / "feature_123.md"
    task_file.write_text("x")

    platform = _FakePlatform()
    with patch.object(svc, "get_git_platform", return_value=platform):
        renamed = svc.rename_task_file_and_sync_issue_body(
            task_file_path=str(task_file),
            issue_url="https://github.com/acme/repo/issues/77",
            issue_body=f"Task file: `{task_file}`",
            project_name="proj-a",
            repo_key="acme/repo",
        )

    assert renamed.endswith("feature_77.md")
    assert Path(renamed).exists()
    update_calls = [c for c in platform.calls if c[0] == "update_issue"]
    assert update_calls
    assert update_calls[-1][1] == "77"
    assert "feature_77.md" in update_calls[-1][2]["body"]
