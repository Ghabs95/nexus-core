"""Tests for git issue plugin profile resolution by transport."""

from nexus.core.orchestration import plugin_runtime


def test_profiled_plugin_uses_api_names_by_default(monkeypatch):
    monkeypatch.delenv("NEXUS_GIT_PLATFORM_TRANSPORT", raising=False)

    captured: list[tuple[str, str, dict, str | None]] = []

    def _fake_get_builtin_plugin(*, kind, name, config, cache_key):  # noqa: ANN001
        captured.append((kind, name, config, cache_key))
        return {"name": name}

    monkeypatch.setattr(plugin_runtime, "get_builtin_plugin", _fake_get_builtin_plugin)

    plugin_runtime.get_profiled_plugin("git_agent_launcher")
    plugin_runtime.get_profiled_plugin("gitlab_agent_launcher")

    assert captured[0][1] == "github-issue-api"
    assert captured[1][1] == "gitlab-issue-api"


def test_profiled_plugin_uses_cli_names_when_transport_cli(monkeypatch):
    monkeypatch.setenv("NEXUS_GIT_PLATFORM_TRANSPORT", "cli")

    captured: list[str] = []

    def _fake_get_builtin_plugin(*, kind, name, config, cache_key):  # noqa: ANN001
        captured.append(name)
        return {"name": name}

    monkeypatch.setattr(plugin_runtime, "get_builtin_plugin", _fake_get_builtin_plugin)

    plugin_runtime.get_profiled_plugin("git_agent_launcher")
    plugin_runtime.get_profiled_plugin("gitlab_agent_launcher")

    assert captured == ["github-issue-cli", "gitlab-issue-cli"]


def test_builtin_register_modules_include_cli_issue_plugins():
    assert "nexus.plugins.builtin.github_issue_cli_plugin" in plugin_runtime._BUILTIN_REGISTER_MODULES
    assert "nexus.plugins.builtin.gitlab_issue_cli_plugin" in plugin_runtime._BUILTIN_REGISTER_MODULES
