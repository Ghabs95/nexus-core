"""Tests for orchestration wrapper config passthrough."""

from nexus.core.orchestration import ai_orchestrator as wrapper


def test_get_orchestrator_passes_copilot_permissions_keys_from_get_config(monkeypatch):
    wrapper.reset_orchestrator()
    captured: dict[str, object] = {}

    class _Plugin:
        pass

    def _fake_get_profiled_plugin(name, overrides, cache_key):
        captured.update(overrides)
        return _Plugin()

    class _Config:
        values = {
            "copilot_permissions": {"allow_urls": ["http://webhook:8081"]},
            "copilot_permissions_resolver": lambda project: {"allow_all_urls": project == "nexus"},
        }

        def get(self, key):
            return self.values.get(key)

    monkeypatch.setattr(wrapper, "get_profiled_plugin", _fake_get_profiled_plugin)
    orchestrator = wrapper.get_orchestrator(_Config())

    assert isinstance(orchestrator, _Plugin)
    assert captured["copilot_permissions"] == {"allow_urls": ["http://webhook:8081"]}
    assert callable(captured["copilot_permissions_resolver"])
    wrapper.reset_orchestrator()
