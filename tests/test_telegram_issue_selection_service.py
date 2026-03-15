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
        project_key="example-org",
        project_config={"example-org": {"git_repo": "example-org/example-project"}},
        get_repos=lambda _project_key: [],
        get_direct_issue_plugin=_get_direct_issue_plugin,
        logger=logging.getLogger("test"),
        requester_nexus_id="nx-42",
    )

    assert seen == [("example-org/example-project", "nx-42", "example-org")]
    assert rows[0]["number"] == 12


def test_list_project_issues_continue_checks_fallback_project_repos():
    seen: list[str] = []

    class _Plugin:
        def __init__(self, repo: str):
            self.repo = repo

        def list_issues(self, **_kwargs):
            if self.repo == "example-org/shared-repo":
                return [{"number": 77, "title": "Recovered issue", "state": "open"}]
            return []

    def _get_direct_issue_plugin(
        repo: str,
        requester_nexus_id: str | None = None,
        project_name: str | None = None,
    ):
        seen.append(repo)
        return _Plugin(repo)

    rows = list_project_issues(
        project_key="nexus",
        project_config={
            "nexus": {"git_repo": "Ghabs95/nexus-arc", "git_repos": ["Ghabs95/nexus-arc"]},
            "shared": {
                "git_repo": "example-org/shared-repo",
                "git_repos": ["example-org/shared-repo"],
            },
        },
        get_repos=lambda _project_key: [],
        get_direct_issue_plugin=_get_direct_issue_plugin,
        logger=logging.getLogger("test"),
        command="continue",
    )

    assert seen == ["Ghabs95/nexus-arc", "example-org/shared-repo"]
    assert rows == [{"number": 77, "title": "[shared-repo] Recovered issue", "state": "open"}]


def test_list_project_issues_non_continue_stays_scoped_to_project_repos():
    seen: list[str] = []

    class _Plugin:
        def __init__(self, repo: str):
            self.repo = repo

        def list_issues(self, **_kwargs):
            if self.repo == "example-org/shared-repo":
                return [{"number": 77, "title": "Recovered issue", "state": "open"}]
            return []

    def _get_direct_issue_plugin(
        repo: str,
        requester_nexus_id: str | None = None,
        project_name: str | None = None,
    ):
        seen.append(repo)
        return _Plugin(repo)

    rows = list_project_issues(
        project_key="nexus",
        project_config={
            "nexus": {"git_repo": "Ghabs95/nexus-arc", "git_repos": ["Ghabs95/nexus-arc"]},
            "shared": {
                "git_repo": "example-org/shared-repo",
                "git_repos": ["example-org/shared-repo"],
            },
        },
        get_repos=lambda _project_key: [],
        get_direct_issue_plugin=_get_direct_issue_plugin,
        logger=logging.getLogger("test"),
        command="status",
    )

    assert seen == ["Ghabs95/nexus-arc"]
    assert rows == []


def test_list_project_issues_continue_keeps_project_scope_when_primary_has_results():
    seen: list[str] = []

    class _Plugin:
        def __init__(self, repo: str):
            self.repo = repo

        def list_issues(self, **_kwargs):
            if self.repo == "Ghabs95/nexus-arc":
                return [{"number": 12, "title": "Project issue", "state": "open"}]
            if self.repo == "example-org/shared-repo":
                return [{"number": 77, "title": "Other project issue", "state": "open"}]
            return []

    def _get_direct_issue_plugin(
        repo: str,
        requester_nexus_id: str | None = None,
        project_name: str | None = None,
    ):
        seen.append(repo)
        return _Plugin(repo)

    rows = list_project_issues(
        project_key="nexus",
        project_config={
            "nexus": {"git_repo": "Ghabs95/nexus-arc", "git_repos": ["Ghabs95/nexus-arc"]},
            "shared": {
                "git_repo": "example-org/shared-repo",
                "git_repos": ["example-org/shared-repo"],
            },
        },
        get_repos=lambda _project_key: [],
        get_direct_issue_plugin=_get_direct_issue_plugin,
        logger=logging.getLogger("test"),
        command="continue",
    )

    assert seen == ["Ghabs95/nexus-arc"]
    assert rows == [{"number": 12, "title": "Project issue", "state": "open"}]
