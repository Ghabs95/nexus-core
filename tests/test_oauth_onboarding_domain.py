from datetime import UTC, datetime, timedelta
from types import SimpleNamespace


def test_store_ai_provider_keys_validates_codex_with_cli_login(monkeypatch):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-1",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-user-1",
    )

    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _session_id: session)
    monkeypatch.setattr(auth_mod, "get_user_credentials", lambda _nexus_id: None)
    monkeypatch.setattr(auth_mod, "_now_utc", lambda: datetime.now(tz=UTC))

    validations: dict[str, int] = {"cli": 0, "provider": 0}

    def _fake_cli_validation(api_key: str):
        assert api_key == "sk-test-codex-key-123456"
        validations["cli"] += 1
        return True, ""

    def _fake_provider_validation(api_key: str):
        assert api_key == "sk-test-codex-key-123456"
        validations["provider"] += 1
        return True, ""

    captured_upsert: dict[str, str] = {}

    monkeypatch.setattr(
        auth_mod,
        "_validate_codex_api_key_with_codex_cli_login",
        _fake_cli_validation,
    )
    monkeypatch.setattr(
        auth_mod,
        "_validate_codex_api_key_with_provider",
        _fake_provider_validation,
    )
    monkeypatch.setattr(auth_mod, "encrypt_secret", lambda value, key_version=1: f"enc::{value}")
    monkeypatch.setattr(
        auth_mod,
        "upsert_ai_provider_keys",
        lambda **kwargs: captured_upsert.update(kwargs),
    )
    monkeypatch.setattr(auth_mod, "update_auth_session", lambda **kwargs: None)
    monkeypatch.setattr(
        auth_mod,
        "get_setup_status",
        lambda _nexus_id: {"ready": True, "project_access_count": 2},
    )

    result = auth_mod.store_ai_provider_keys(
        session_id="session-1",
        codex_api_key="sk-test-codex-key-123456",
    )

    assert validations == {"cli": 1, "provider": 1}
    assert captured_upsert["nexus_id"] == "nexus-user-1"
    assert captured_upsert["codex_api_key_enc"] == "enc::sk-test-codex-key-123456"
    assert result["ready"] is True
    assert result["project_access_count"] == 2


def test_store_ai_provider_keys_rejects_codex_when_cli_login_validation_fails(monkeypatch):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-2",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-user-2",
    )

    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _session_id: session)
    monkeypatch.setattr(auth_mod, "get_user_credentials", lambda _nexus_id: None)
    monkeypatch.setattr(auth_mod, "_now_utc", lambda: datetime.now(tz=UTC))
    monkeypatch.setattr(
        auth_mod,
        "_validate_codex_api_key_with_codex_cli_login",
        lambda _api_key: (False, "Codex CLI login validation failed: 401 Unauthorized"),
    )

    try:
        auth_mod.store_ai_provider_keys(
            session_id="session-2",
            codex_api_key="sk-test-codex-key-123456",
        )
        assert False, "expected codex login validation error"
    except ValueError as exc:
        assert "Codex CLI login validation failed" in str(exc)


def test_start_provider_account_login_launches_codex_device_auth(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-device-1",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-device-1",
    )
    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _sid: session)
    monkeypatch.setenv("NEXUS_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_CLI_PATH", "codex")

    class _Proc:
        pid = 4242

        @staticmethod
        def poll():
            return None

    def _fake_popen(cmd, cwd, env, stdin, stdout, stderr, text):
        assert cmd == ["codex", "login", "--device-auth"]
        assert str(env.get("CODEX_HOME", "")).endswith("/auth/codex/nexus-device-1")
        return _Proc()

    monkeypatch.setattr(auth_mod.subprocess, "Popen", _fake_popen)

    result = auth_mod.start_provider_account_login(session_id=session.session_id, provider="codex")

    assert result["started"] is True
    assert result["state"] == "starting"
    assert result["provider"] == "codex"


def test_start_provider_account_login_rejects_insecure_owner(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-device-owner",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-device-owner",
    )
    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _sid: session)
    monkeypatch.setenv("NEXUS_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(
        auth_mod,
        "_ensure_private_dir",
        lambda _path: (_ for _ in ()).throw(PermissionError("insecure auth directory")),
    )

    try:
        auth_mod.start_provider_account_login(session_id=session.session_id, provider="codex")
        assert False, "Expected insecure ownership check to fail"
    except PermissionError as exc:
        assert "insecure auth directory" in str(exc)


def test_get_provider_account_login_status_marks_connected_on_success(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-device-2",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-device-2",
    )
    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _sid: session)
    monkeypatch.setattr(auth_mod, "format_login_session_ref", lambda sid: f"lsr_{sid}")

    calls: dict[str, object] = {}
    monkeypatch.setattr(
        auth_mod,
        "store_ai_provider_keys",
        lambda **kwargs: calls.update(kwargs),
    )
    monkeypatch.setattr(
        auth_mod,
        "get_setup_status",
        lambda _nid: {"codex_account_enabled": True},
    )

    log_path = tmp_path / "codex_device.log"
    log_path.write_text("Open https://auth.openai.com/device and enter ABCD-EFGH\n", encoding="utf-8")

    class _DoneProc:
        @staticmethod
        def poll():
            return 0

    key = auth_mod._device_job_key(session_id=session.session_id, provider="codex")
    with auth_mod._DEVICE_AUTH_LOCK:
        auth_mod._DEVICE_AUTH_JOBS[key] = {
            "provider": "codex",
            "session_id": session.session_id,
            "nexus_id": session.nexus_id,
            "process": _DoneProc(),
            "log_path": str(log_path),
            "log_file": None,
        }

    result = auth_mod.get_provider_account_login_status(session_id=session.session_id, provider="codex")

    assert result["state"] == "connected"
    assert result["connected"] is True
    assert calls["session_id"] == session.session_id
    assert calls["use_codex_account"] is True
