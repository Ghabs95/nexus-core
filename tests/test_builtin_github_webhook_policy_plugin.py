"""Tests for built-in GitHub webhook policy plugin."""

from nexus.plugins.builtin.github_webhook_policy_plugin import GithubWebhookPolicyPlugin


def test_resolve_project_key_matches_single_repo_field():
    plugin = GithubWebhookPolicyPlugin()
    config = {
        "nexus": {
            "git_repo": "Ghabs95/nexus-core",
        }
    }

    project = plugin.resolve_project_key("Ghabs95/nexus-core", config, default_project="fallback")

    assert project == "nexus"


def test_resolve_project_key_matches_repo_in_github_repos_list():
    plugin = GithubWebhookPolicyPlugin()
    config = {
        "wallible": {
            "git_repo": "acme/wallible-backend",
            "git_repos": ["acme/wallible-backend", "acme/wallible-mobile"],
        }
    }

    project = plugin.resolve_project_key("acme/wallible-mobile", config, default_project="fallback")

    assert project == "wallible"
