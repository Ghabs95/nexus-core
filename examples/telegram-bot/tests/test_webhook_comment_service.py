from unittest.mock import MagicMock

from services.webhook_comment_service import handle_issue_comment_event


class _Policy:
    def determine_project_from_issue(self, issue):
        return "proj-a"


def test_comment_service_ignores_non_created():
    result = handle_issue_comment_event(
        event={
            "action": "edited",
            "comment_id": 1,
            "issue_number": "42",
            "comment_author": "copilot",
        },
        logger=MagicMock(),
        policy=_Policy(),
        processed_events=set(),
        launch_next_agent=lambda **kwargs: (None, None),
        check_and_notify_pr=lambda issue, project: None,
    )
    assert result["status"] == "ignored"


def test_comment_service_marks_workflow_completed_and_dedups():
    processed = set()
    called = []
    result = handle_issue_comment_event(
        event={
            "action": "created",
            "comment_id": 2,
            "comment_body": "Workflow complete. All steps completed.",
            "issue_number": "42",
            "comment_author": "copilot",
            "issue": {},
        },
        logger=MagicMock(),
        policy=_Policy(),
        processed_events=processed,
        launch_next_agent=lambda **kwargs: (None, None),
        check_and_notify_pr=lambda issue, project: called.append((issue, project)),
    )
    assert result["status"] == "workflow_completed"
    assert ("42", "proj-a") in called
    assert "comment_2" in processed


def test_comment_service_chains_next_agent():
    processed = set()
    result = handle_issue_comment_event(
        event={
            "action": "created",
            "comment_id": 3,
            "comment_body": "Ready for @reviewer",
            "issue_number": "77",
            "comment_author": "copilot",
            "issue": {},
        },
        logger=MagicMock(),
        policy=_Policy(),
        processed_events=processed,
        launch_next_agent=lambda **kwargs: (1234, "copilot"),
        check_and_notify_pr=lambda issue, project: None,
    )
    assert result["status"] == "agent_launched"
    assert result["next_agent"] == "reviewer"
    assert "comment_3" in processed
