"""Tests for built-in JSON state store plugin."""

from nexus.plugins.builtin.json_state_plugin import JsonStateStorePlugin


def test_json_state_plugin_roundtrip(tmp_path):
    plugin = JsonStateStorePlugin({})
    payload = {"a": 1, "b": {"x": True}}
    path = str(tmp_path / "state.json")

    assert plugin.save_json(path, payload) is True
    assert plugin.load_json(path, default={}) == payload


def test_json_state_plugin_line_helpers(tmp_path):
    plugin = JsonStateStorePlugin({})
    path = str(tmp_path / "audit.log")

    assert plugin.append_line(path, "one\n") is True
    assert plugin.append_line(path, "two\n") is True
    assert plugin.read_lines(path) == ["one\n", "two\n"]
