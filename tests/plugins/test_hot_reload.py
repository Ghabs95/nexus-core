"""Tests for dynamic plugin hot-reload: registry changes and HotReloadWatcher."""

import threading
import time
from unittest.mock import MagicMock

import pytest

from nexus.plugins.base import PluginKind, make_plugin_spec
from nexus.plugins.registry import (
    PluginNotFoundError,
    PluginRegistrationError,
    PluginRegistry,
)

# ---------------------------------------------------------------------------
# Registry thread-safety / unregister / force tests
# ---------------------------------------------------------------------------


def _dummy_factory(config):
    return object()


def _make_spec(name="test-plugin", version="1.0.0"):
    return make_plugin_spec(PluginKind.STORAGE_BACKEND, name, version, _dummy_factory)


class TestRegistryUnregister:
    def test_unregister_removes_plugin(self):
        registry = PluginRegistry()
        spec = _make_spec()
        registry.register(spec)
        assert registry.has_plugin(PluginKind.STORAGE_BACKEND, "test-plugin")
        registry.unregister(PluginKind.STORAGE_BACKEND, "test-plugin")
        assert not registry.has_plugin(PluginKind.STORAGE_BACKEND, "test-plugin")

    def test_unregister_unknown_raises(self):
        registry = PluginRegistry()
        with pytest.raises(PluginNotFoundError):
            registry.unregister(PluginKind.STORAGE_BACKEND, "nonexistent")

    def test_unregister_normalises_name(self):
        registry = PluginRegistry()
        registry.register(_make_spec("My_Plugin"))
        # normalized key should match
        registry.unregister(PluginKind.STORAGE_BACKEND, "My_Plugin")
        assert not registry.has_plugin(PluginKind.STORAGE_BACKEND, "my-plugin")


class TestRegistryForce:
    def test_force_replaces_existing(self):
        registry = PluginRegistry()
        registry.register(_make_spec(version="1.0.0"))
        new_spec = _make_spec(version="2.0.0")
        registry.register(new_spec, force=True)
        assert registry.get_spec(PluginKind.STORAGE_BACKEND, "test-plugin").version == "2.0.0"

    def test_no_force_raises_on_duplicate(self):
        registry = PluginRegistry()
        registry.register(_make_spec())
        with pytest.raises(PluginRegistrationError):
            registry.register(_make_spec())

    def test_register_factory_force(self):
        registry = PluginRegistry()
        registry.register_factory(PluginKind.STORAGE_BACKEND, "p", "1.0", _dummy_factory)
        # should not raise with force=True
        registry.register_factory(
            PluginKind.STORAGE_BACKEND, "p", "2.0", _dummy_factory, force=True
        )
        assert registry.get_spec(PluginKind.STORAGE_BACKEND, "p").version == "2.0"


class TestRegistryThreadSafety:
    def test_concurrent_register_and_list(self):
        """Concurrent reads and writes must not raise or corrupt the registry."""
        registry = PluginRegistry()
        errors = []

        def writer():
            for i in range(50):
                try:
                    spec = make_plugin_spec(
                        PluginKind.STORAGE_BACKEND, f"plugin-{i}", "1.0", _dummy_factory
                    )
                    registry.register(spec)
                except PluginRegistrationError:
                    pass  # expected on duplicates
                except Exception as exc:
                    errors.append(exc)

        def reader():
            for _ in range(100):
                _ = registry.list_specs()
                time.sleep(0)

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread safety errors: {errors}"


# ---------------------------------------------------------------------------
# _load_module_from_path helper
# ---------------------------------------------------------------------------


class TestLoadModuleFromPath:
    def test_load_valid_module(self, tmp_path):
        from nexus.plugins.plugin_runtime import _load_module_from_path

        plugin_file = tmp_path / "sample_plugin.py"
        plugin_file.write_text("VALUE = 42\n")
        module = _load_module_from_path(plugin_file)
        assert module is not None
        assert module.VALUE == 42

    def test_load_nonexistent_returns_none(self, tmp_path):
        from nexus.plugins.plugin_runtime import _load_module_from_path

        result = _load_module_from_path(tmp_path / "missing.py")
        assert result is None

    def test_load_syntax_error_returns_none(self, tmp_path):
        from nexus.plugins.plugin_runtime import _load_module_from_path

        bad = tmp_path / "bad.py"
        bad.write_text("def broken(:\n")
        assert _load_module_from_path(bad) is None


# ---------------------------------------------------------------------------
# HotReloadWatcher (unit tests with watchdog mocked out)
# ---------------------------------------------------------------------------


class TestHotReloadWatcherInit:
    def test_raises_when_watchdog_missing(self):
        import nexus.plugins.plugin_runtime as rt

        original = rt._WATCHDOG_AVAILABLE
        try:
            rt._WATCHDOG_AVAILABLE = False
            with pytest.raises(ImportError, match="watchdog"):
                from nexus.plugins.plugin_runtime import HotReloadWatcher

                HotReloadWatcher(PluginRegistry(), "/tmp")
        finally:
            rt._WATCHDOG_AVAILABLE = original

    def test_raises_on_wrong_registry_type(self):
        pytest.importorskip("watchdog")
        from nexus.plugins.plugin_runtime import HotReloadWatcher

        with pytest.raises(TypeError):
            HotReloadWatcher("not-a-registry", "/tmp")


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("watchdog"),
    reason="watchdog not installed",
)
class TestHotReloadWatcherLifecycle:
    def test_start_and_stop(self, tmp_path):
        from nexus.plugins.plugin_runtime import HotReloadWatcher

        registry = PluginRegistry()
        watcher = HotReloadWatcher(registry, tmp_path)
        watcher.start()
        assert watcher.is_running()
        watcher.stop()
        assert not watcher.is_running()

    def test_double_start_does_not_raise(self, tmp_path):
        from nexus.plugins.plugin_runtime import HotReloadWatcher

        registry = PluginRegistry()
        watcher = HotReloadWatcher(registry, tmp_path)
        watcher.start()
        watcher.start()  # second call should be a no-op
        watcher.stop()

    def test_stop_without_start_does_not_raise(self, tmp_path):
        from nexus.plugins.plugin_runtime import HotReloadWatcher

        registry = PluginRegistry()
        watcher = HotReloadWatcher(registry, tmp_path)
        watcher.stop()  # should be silent


# ---------------------------------------------------------------------------
# _PluginFileEventHandler reload logic
# ---------------------------------------------------------------------------


class TestPluginFileEventHandler:
    def _make_event(self, path: str, is_directory: bool = False):
        event = MagicMock()
        event.src_path = path
        event.is_directory = is_directory
        return event

    def test_reload_via_register_plugins(self, tmp_path):
        pytest.importorskip("watchdog")
        from nexus.plugins.plugin_runtime import _PluginFileEventHandler

        plugin_file = tmp_path / "my_plugin.py"
        plugin_file.write_text(
            "from nexus.plugins.base import PluginKind, make_plugin_spec\n"
            "def _f(c): return object()\n"
            "def register_plugins(registry):\n"
            "    registry.register_factory(PluginKind.STORAGE_BACKEND, 'dyn', '1.0', _f, force=True)\n"
        )

        registry = PluginRegistry()
        handler = _PluginFileEventHandler(registry, tmp_path)
        handler.on_modified(self._make_event(str(plugin_file)))

        assert registry.has_plugin(PluginKind.STORAGE_BACKEND, "dyn")

    def test_ignores_directory_events(self, tmp_path):
        pytest.importorskip("watchdog")
        from nexus.plugins.plugin_runtime import _PluginFileEventHandler

        registry = PluginRegistry()
        handler = _PluginFileEventHandler(registry, tmp_path)
        handler.on_modified(self._make_event(str(tmp_path), is_directory=True))
        assert registry.list_specs() == []

    def test_ignores_non_py_files(self, tmp_path):
        pytest.importorskip("watchdog")
        from nexus.plugins.plugin_runtime import _PluginFileEventHandler

        registry = PluginRegistry()
        handler = _PluginFileEventHandler(registry, tmp_path)
        handler.on_modified(self._make_event(str(tmp_path / "config.yaml")))
        assert registry.list_specs() == []

    def test_bad_plugin_file_logs_warning_not_raises(self, tmp_path):
        pytest.importorskip("watchdog")
        from nexus.plugins.plugin_runtime import _PluginFileEventHandler

        bad_file = tmp_path / "bad.py"
        bad_file.write_text("raise RuntimeError('oops')\n")

        registry = PluginRegistry()
        handler = _PluginFileEventHandler(registry, tmp_path)
        # Must not raise
        handler.on_modified(self._make_event(str(bad_file)))
