"""Tests for plugin registry and plugin spec behavior."""

import pytest

from nexus.plugins import (
    PluginKind,
    PluginNotFoundError,
    PluginRegistrationError,
    PluginRegistry,
    make_plugin_spec,
)


def test_register_and_create_plugin():
    registry = PluginRegistry()

    def factory(config):
        return {"name": "demo-provider", "config": config}

    spec = make_plugin_spec(
        kind=PluginKind.AI_PROVIDER,
        name="Demo_Provider",
        version="0.1.0",
        factory=factory,
    )

    registry.register(spec)
    built = registry.create(PluginKind.AI_PROVIDER, "demo-provider", {"timeout": 30})

    assert built["name"] == "demo-provider"
    assert built["config"]["timeout"] == 30


def test_duplicate_registration_raises_error():
    registry = PluginRegistry()

    def factory(_config):
        return object()

    spec = make_plugin_spec(
        kind=PluginKind.GIT_PLATFORM,
        name="github",
        version="1.0.0",
        factory=factory,
    )

    registry.register(spec)

    with pytest.raises(PluginRegistrationError):
        registry.register(spec)


def test_create_missing_plugin_raises_error():
    registry = PluginRegistry()

    with pytest.raises(PluginNotFoundError):
        registry.create(PluginKind.STORAGE_BACKEND, "postgres")


def test_list_specs_can_filter_by_kind():
    registry = PluginRegistry()

    registry.register_factory(
        kind=PluginKind.AI_PROVIDER,
        name="copilot",
        version="1.0.0",
        factory=lambda _config: object(),
    )
    registry.register_factory(
        kind=PluginKind.GIT_PLATFORM,
        name="github",
        version="1.0.0",
        factory=lambda _config: object(),
    )

    ai_specs = registry.list_specs(PluginKind.AI_PROVIDER)

    assert len(ai_specs) == 1
    assert ai_specs[0].name == "copilot"
