import logging

from nexus.core.telegram.telegram_issue_selection_service import list_project_issues


class _Plugin:
    def list_issues(self, **_kwargs):
        return [{"number": 12, "title": "Issue title", "state": "open"}]


def test_list_project_issues_passes_requester_nexus_id():
    seen: list[tuple[str, str | None]] = []

    def _get_direct_issue_plugin(repo: str, requester_nexus_id: str | None = None):
        seen.append((repo, requester_nexus_id))
        return _Plugin()

    rows = list_project_issues(
        project_key="nexus",
        project_config={"nexus": {"git_repo": "Ghabs95/nexus-arc"}},
        get_repos=lambda _project_key: [],
        get_direct_issue_plugin=_get_direct_issue_plugin,
        logger=logging.getLogger("test"),
        requester_nexus_id="nx-42",
    )

    assert seen == [("Ghabs95/nexus-arc", "nx-42")]
    assert rows[0]["number"] == 12


def test_list_project_issues_passes_project_name_when_supported():
    seen: list[tuple[str, str | None, str | None]] = []

    def _get_direct_issue_plugin(
        repo: str,
        requester_nexus_id: str | None = None,
        project_name: str | None = None,
    ):
        seen.append((repo, requester_nexus_id, project_name))
        return _Plugin()

    rows = list_project_issues(
        project_key="wallible",
        project_config={"wallible": {"git_repo": "wallible/wlbl-workflow-os"}},
        get_repos=lambda _project_key: [],
        get_direct_issue_plugin=_get_direct_issue_plugin,
        logger=logging.getLogger("test"),
        requester_nexus_id="nx-42",
    )

    assert seen == [("wallible/wlbl-workflow-os", "nx-42", "wallible")]
    assert rows[0]["number"] == 12
