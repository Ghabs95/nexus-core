"""Tests for automatic task archival on workflow finalization."""


def test_archive_closed_task_by_issue_url(tmp_path, monkeypatch):
    from inbox_processor import _archive_closed_task_files

    workspace = tmp_path / "workspace"
    active_dir = workspace / ".nexus" / "tasks" / "nexus" / "active"
    closed_dir = workspace / ".nexus" / "tasks" / "nexus" / "closed"
    active_dir.mkdir(parents=True)

    task = active_dir / "feature-simple_123.md"
    task.write_text("""# Task\n
**Issue:** https://github.com/acme/repo/issues/41
""")

    monkeypatch.setattr("inbox_processor.BASE_DIR", str(tmp_path))
    monkeypatch.setattr(
        "inbox_processor.PROJECT_CONFIG",
        {
            "nexus": {
                "workspace": "workspace",
                "git_repo": "acme/repo",
            }
        },
    )

    archived = _archive_closed_task_files("41", "nexus")

    assert archived == 1
    assert not task.exists()
    assert (closed_dir / "feature-simple_123.md").exists()


def test_archive_closed_task_by_issue_filename(tmp_path, monkeypatch):
    from inbox_processor import _archive_closed_task_files

    workspace = tmp_path / "workspace"
    active_dir = workspace / ".nexus" / "tasks" / "nexus" / "active"
    closed_dir = workspace / ".nexus" / "tasks" / "nexus" / "closed"
    active_dir.mkdir(parents=True)

    task = active_dir / "issue_41.md"
    task.write_text("# Webhook task")

    monkeypatch.setattr("inbox_processor.BASE_DIR", str(tmp_path))
    monkeypatch.setattr(
        "inbox_processor.PROJECT_CONFIG",
        {
            "nexus": {
                "workspace": "workspace",
                "git_repo": "acme/repo",
            }
        },
    )

    archived = _archive_closed_task_files("41", "nexus")

    assert archived == 1
    assert not task.exists()
    assert (closed_dir / "issue_41.md").exists()


def test_archive_closed_task_ignores_other_issues(tmp_path, monkeypatch):
    from inbox_processor import _archive_closed_task_files

    workspace = tmp_path / "workspace"
    active_dir = workspace / ".nexus" / "tasks" / "nexus" / "active"
    closed_dir = workspace / ".nexus" / "tasks" / "nexus" / "closed"
    active_dir.mkdir(parents=True)

    other_task = active_dir / "feature-simple_999.md"
    other_task.write_text("""# Task\n
**Issue:** https://github.com/acme/repo/issues/999
""")

    monkeypatch.setattr("inbox_processor.BASE_DIR", str(tmp_path))
    monkeypatch.setattr(
        "inbox_processor.PROJECT_CONFIG",
        {
            "nexus": {
                "workspace": "workspace",
                "git_repo": "acme/repo",
            }
        },
    )

    archived = _archive_closed_task_files("41", "nexus")

    assert archived == 0
    assert other_task.exists()
    assert not closed_dir.exists()
