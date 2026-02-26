import logging
import sys
from unittest.mock import MagicMock


def test_process_file_returns_after_webhook_dispatch(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("LOGS_DIR", str(log_dir))
    monkeypatch.setattr(logging, "FileHandler", lambda *args, **kwargs: logging.NullHandler())
    sys.modules.pop("inbox_processor", None)
    import inbox_processor

    inbox_file = tmp_path / "task.md"
    inbox_file.write_text("x")

    calls = {"webhook": 0, "new": 0}

    monkeypatch.setattr(
        inbox_processor,
        "_load_task_context",
        lambda **kwargs: {
            "content": "body",
            "task_type": "feature",
            "project_name": "proj-a",
            "project_root": str(tmp_path),
            "config": {"workspace": "ws"},
        },
    )

    def _webhook(**kwargs):
        calls["webhook"] += 1
        return True

    def _new(**kwargs):
        calls["new"] += 1

    monkeypatch.setattr(inbox_processor, "_handle_webhook_task", _webhook)
    monkeypatch.setattr(inbox_processor, "_handle_new_task", _new)
    monkeypatch.setattr(inbox_processor, "logger", MagicMock())

    inbox_processor.process_file(str(inbox_file))

    assert calls["webhook"] == 1
    assert calls["new"] == 0


def test_process_file_routes_to_new_task_when_not_webhook(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("LOGS_DIR", str(log_dir))
    monkeypatch.setattr(logging, "FileHandler", lambda *args, **kwargs: logging.NullHandler())
    sys.modules.pop("inbox_processor", None)
    import inbox_processor

    inbox_file = tmp_path / "task.md"
    inbox_file.write_text("x")

    calls = {"webhook": 0, "new": 0}

    monkeypatch.setattr(
        inbox_processor,
        "_load_task_context",
        lambda **kwargs: {
            "content": "body",
            "task_type": "feature",
            "project_name": "proj-a",
            "project_root": str(tmp_path),
            "config": {"workspace": "ws"},
        },
    )

    def _webhook(**kwargs):
        calls["webhook"] += 1
        return False

    def _new(**kwargs):
        calls["new"] += 1

    monkeypatch.setattr(inbox_processor, "_handle_webhook_task", _webhook)
    monkeypatch.setattr(inbox_processor, "_handle_new_task", _new)
    monkeypatch.setattr(inbox_processor, "logger", MagicMock())

    inbox_processor.process_file(str(inbox_file))

    assert calls["webhook"] == 1
    assert calls["new"] == 1
