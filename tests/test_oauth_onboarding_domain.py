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
