from nexus.adapters.git.utils import build_issue_url


def test_build_issue_url_defaults_to_github_when_config_missing():
    assert (
        build_issue_url("owner/repo", "42", None) == "https://github.com/owner/repo/issues/42"
    )


def test_build_issue_url_infers_gitlab_when_gitlab_base_url_present():
    config = {"gitlab_base_url": "https://gitlab.com"}
    assert (
        build_issue_url("wallible/wlbl-workflow-os", "42", config)
        == "https://gitlab.com/wallible/wlbl-workflow-os/-/issues/42"
    )
