from nexus.core.git.direct_issue_plugin_service import get_direct_issue_plugin


class _PluginFactory:
    def __init__(self):
        self.calls = []

    def __call__(self, profile, overrides, cache_key):
        self.calls.append((profile, overrides, cache_key))
        return {"profile": profile, "cache_key": cache_key, "overrides": overrides}


def test_get_direct_issue_plugin_uses_transport_scoped_cache_key(monkeypatch):
    monkeypatch.setenv("NEXUS_GIT_PLATFORM_TRANSPORT", "cli")
    factory = _PluginFactory()
    plugin = get_direct_issue_plugin(repo="Ghabs95/nexus-arc", get_profiled_plugin=factory)

    assert plugin["profile"] == "git_agent_launcher"
    assert len(factory.calls) == 1
    assert factory.calls[0][0] == "git_agent_launcher"
    assert factory.calls[0][2] == "git:direct:cli:Ghabs95/nexus-arc"


def test_get_direct_issue_plugin_scopes_cache_key_by_requester(monkeypatch):
    monkeypatch.setenv("NEXUS_GIT_PLATFORM_TRANSPORT", "api")
    factory = _PluginFactory()
    plugin = get_direct_issue_plugin(
        repo="Ghabs95/nexus-arc",
        get_profiled_plugin=factory,
        requester_nexus_id="nx-123",
    )

    assert plugin["profile"] == "git_agent_launcher"
    assert len(factory.calls) == 1
    assert factory.calls[0][1]["requester_nexus_id"] == "nx-123"
    assert factory.calls[0][2] == "git:direct:api:Ghabs95/nexus-arc:nx-123"
