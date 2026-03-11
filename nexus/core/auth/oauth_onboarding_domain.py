"""OAuth session orchestration and AI provider key onboarding helpers."""

from __future__ import annotations

import base64
import os
import re
import secrets
import subprocess
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import requests

from nexus.core.auth.access_domain import (
    get_setup_status,
    sync_user_gitlab_project_access,
    sync_user_project_access,
)
from nexus.core.auth.credential_crypto import encrypt_secret
from nexus.core.auth.credential_store import (
    cleanup_expired_auth_sessions,
    create_auth_session,
    find_user_credentials_by_github_identity,
    find_user_credentials_by_gitlab_identity,
    get_auth_session,
    get_auth_session_by_state,
    get_latest_auth_session_for_nexus,
    get_user_credentials,
    update_auth_session,
    upsert_ai_provider_keys,
    upsert_github_credentials,
    upsert_gitlab_credentials,
)


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _required_env(name: str) -> str:
    value = str(os.getenv(name, "")).strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _allowed_github_orgs() -> set[str]:
    raw = os.getenv("NEXUS_AUTH_ALLOWED_GITHUB_ORGS", "")
    return {value.strip().lower() for value in str(raw).split(",") if str(value).strip()}


def _allowed_gitlab_groups() -> set[str]:
    raw = os.getenv("NEXUS_AUTH_ALLOWED_GITLAB_GROUPS", "")
    return {value.strip().lower() for value in str(raw).split(",") if str(value).strip()}


def _session_ttl_seconds() -> int:
    raw = os.getenv("NEXUS_AUTH_SESSION_TTL_SECONDS", "900")
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        parsed = 900
    return max(120, parsed)


def _key_version() -> int:
    raw = os.getenv("NEXUS_CREDENTIALS_KEY_VERSION", "1")
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        parsed = 1
    return max(1, parsed)


def _normalize_provider(provider: str | None) -> str:
    value = str(provider or "github").strip().lower()
    if value not in {"github", "gitlab"}:
        raise ValueError("Unsupported auth provider. Use 'github' or 'gitlab'.")
    return value


def _gitlab_base_url() -> str:
    return str(
        os.getenv("NEXUS_GITLAB_BASE_URL", os.getenv("GITLAB_BASE_URL", "https://gitlab.com"))
    ).strip().rstrip("/")


_SESSION_REF_PREFIX = "lsr_"
_DEVICE_AUTH_JOBS: dict[str, dict[str, Any]] = {}
_DEVICE_AUTH_LOCK = threading.Lock()


def _device_job_key(*, session_id: str, provider: str) -> str:
    return f"{str(session_id).strip()}::{str(provider).strip().lower()}"


def _provider_runtime_home(*, provider: str, nexus_id: str) -> str:
    runtime_root = str(os.getenv("NEXUS_RUNTIME_DIR", "/var/lib/nexus")).strip() or "/var/lib/nexus"
    return os.path.join(runtime_root, "auth", str(provider).strip().lower(), str(nexus_id).strip())


def _ensure_private_dir(path: str) -> None:
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except Exception:
        pass
    getuid = getattr(os, "getuid", None)
    if callable(getuid):
        try:
            current_uid = int(getuid())
            owner_uid = int(os.stat(path).st_uid)
        except Exception as exc:
            raise RuntimeError(f"Unable to verify private directory owner for {path}: {exc}") from exc
        if owner_uid != current_uid:
            raise PermissionError(
                f"Refusing to use insecure auth directory '{path}' owned by uid={owner_uid}; "
                f"expected uid={current_uid}."
            )


def _ensure_private_file(path: str) -> None:
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    getuid = getattr(os, "getuid", None)
    if callable(getuid):
        try:
            current_uid = int(getuid())
            owner_uid = int(os.stat(path).st_uid)
        except Exception as exc:
            raise RuntimeError(f"Unable to verify private file owner for {path}: {exc}") from exc
        if owner_uid != current_uid:
            raise PermissionError(
                f"Refusing to use insecure auth file '{path}' owned by uid={owner_uid}; "
                f"expected uid={current_uid}."
            )


def _parse_device_auth_url_and_code(raw_text: str) -> tuple[str, str]:
    text = str(raw_text or "")
    url_match = re.search(r"https?://[^\s)]+", text, flags=re.IGNORECASE)
    code_match = re.search(r"\b[A-Z0-9]{4}(?:-[A-Z0-9]{4})+\b", text)
    url = str(url_match.group(0) if url_match else "").strip()
    code = str(code_match.group(0) if code_match else "").strip()
    return url, code


def _log_tail(path: str, *, max_chars: int = 3000) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            content = fh.read()
    except Exception:
        return ""
    if len(content) <= max_chars:
        return content
    return content[-max_chars:]


def format_login_session_ref(session_id: str) -> str:
    normalized = str(session_id or "").strip()
    if not normalized:
        return ""
    encoded = base64.urlsafe_b64encode(normalized.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{_SESSION_REF_PREFIX}{encoded}"


def resolve_login_session_id(session_ref_or_id: str) -> str:
    candidate = str(session_ref_or_id or "").strip()
    if not candidate:
        return ""
    if candidate.startswith(_SESSION_REF_PREFIX):
        encoded = candidate[len(_SESSION_REF_PREFIX) :]
        if not encoded:
            return ""
        padding = "=" * ((4 - (len(encoded) % 4)) % 4)
        try:
            decoded = base64.urlsafe_b64decode((encoded + padding).encode("ascii")).decode("utf-8")
        except Exception:
            return ""
        return str(decoded or "").strip()
    return candidate


def setup_status_command_for_platform(chat_platform: str | None) -> str:
    platform = str(chat_platform or "").strip().lower()
    if platform == "discord":
        return "/setup-status"
    if platform == "telegram":
        return "/setup_status"
    return "/setup-status (Discord) or /setup_status (Telegram)"


def build_setup_completed_chat_message(*, session_id: str, ready: bool) -> str:
    command_hint = setup_status_command_for_platform(None)
    resolved_session_id = resolve_login_session_id(session_id)
    if resolved_session_id:
        record = get_auth_session(resolved_session_id)
        command_hint = setup_status_command_for_platform(getattr(record, "chat_platform", None))
    prefix = "✅ Setup completed." if ready else "⚠️ Setup updated."
    return f"{prefix}\nRun {command_hint} to check setup status."


def create_login_session_for_user(
    *,
    nexus_id: str,
    discord_user_id: str,
    discord_username: str | None,
    chat_platform: str | None = None,
    chat_id: str | None = None,
    onboarding_message_id: str | None = None,
) -> str:
    cleanup_expired_auth_sessions()
    return create_auth_session(
        nexus_id=str(nexus_id),
        discord_user_id=str(discord_user_id),
        discord_username=discord_username,
        chat_platform=chat_platform,
        chat_id=chat_id,
        onboarding_message_id=onboarding_message_id,
        ttl_seconds=_session_ttl_seconds(),
    )


def register_onboarding_message(
    *,
    session_id: str,
    chat_platform: str,
    chat_id: str,
    message_id: str,
) -> None:
    update_auth_session(
        session_id=str(session_id),
        chat_platform=str(chat_platform or "").strip().lower(),
        chat_id=str(chat_id or "").strip(),
        onboarding_message_id=str(message_id or "").strip(),
    )


def start_oauth_flow(session_id: str, provider: str = "github") -> tuple[str, str]:
    """Return (auth_url, state) and persist one-time state hash for provider."""
    auth_provider = _normalize_provider(provider)
    base_url = _required_env("NEXUS_PUBLIC_BASE_URL").rstrip("/")
    state = secrets.token_urlsafe(32)

    resolved_session_id = resolve_login_session_id(session_id)
    record = get_auth_session(str(resolved_session_id))
    if not record:
        raise ValueError("Invalid session")
    if record.expires_at < _now_utc():
        update_auth_session(session_id=str(resolved_session_id), status="expired")
        raise ValueError("Session expired")

    from nexus.core.auth.credential_store import hash_oauth_state

    update_auth_session(
        session_id=str(resolved_session_id),
        oauth_provider=auth_provider,
        oauth_state_hash=hash_oauth_state(state),
        status="pending",
        last_error="",
    )

    if auth_provider == "github":
        client_id = _required_env("NEXUS_GITHUB_CLIENT_ID")
        callback_url = f"{base_url}/auth/github/callback"
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": callback_url,
                "state": state,
                "scope": "read:user read:org repo",
            }
        )
        return f"https://github.com/login/oauth/authorize?{query}", state

    client_id = _required_env("NEXUS_GITLAB_CLIENT_ID")
    callback_url = f"{base_url}/auth/gitlab/callback"
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": callback_url,
            "response_type": "code",
            "state": state,
            "scope": "read_user api",
        }
    )
    return f"{_gitlab_base_url()}/oauth/authorize?{query}", state


def _github_exchange_code_for_token(code: str) -> dict[str, Any]:
    client_id = _required_env("NEXUS_GITHUB_CLIENT_ID")
    client_secret = _required_env("NEXUS_GITHUB_CLIENT_SECRET")
    response = requests.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": str(code or "").strip(),
        },
        timeout=15,
    )
    if response.status_code != 200:
        detail = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                msg = str(payload.get("error_description") or payload.get("error") or "").strip()
                if msg:
                    detail = f": {msg}"
        except Exception:
            text = str(response.text or "").strip().replace("\n", " ")
            if text:
                detail = f": {text[:200]}"
        raise RuntimeError(f"OAuth exchange failed ({response.status_code}){detail}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("OAuth exchange returned invalid payload")
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError(f"OAuth access token missing ({payload.get('error') or 'unknown error'})")
    return payload


def _gitlab_exchange_code_for_token(code: str) -> dict[str, Any]:
    client_id = _required_env("NEXUS_GITLAB_CLIENT_ID")
    client_secret = _required_env("NEXUS_GITLAB_CLIENT_SECRET")
    callback_url = f"{_required_env('NEXUS_PUBLIC_BASE_URL').rstrip('/')}/auth/gitlab/callback"
    response = requests.post(
        f"{_gitlab_base_url()}/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": str(code or "").strip(),
            "grant_type": "authorization_code",
            "redirect_uri": callback_url,
        },
        timeout=15,
    )
    if response.status_code != 200:
        detail = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                msg = str(payload.get("error_description") or payload.get("error") or "").strip()
                if msg:
                    detail = f": {msg}"
        except Exception:
            text = str(response.text or "").strip().replace("\n", " ")
            if text:
                detail = f": {text[:200]}"
        raise RuntimeError(f"OAuth exchange failed ({response.status_code}){detail}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("OAuth exchange returned invalid payload")
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError(f"OAuth access token missing ({payload.get('error') or 'unknown error'})")
    return payload


def _github_get(path: str, token: str) -> requests.Response:
    return requests.get(
        f"https://api.github.com{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=15,
    )


def _gitlab_get(path: str, token: str) -> requests.Response:
    return requests.get(
        f"{_gitlab_base_url()}{path}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
        timeout=15,
    )


def _fetch_github_profile(token: str) -> dict[str, Any]:
    response = _github_get("/user", token)
    if response.status_code != 200:
        raise RuntimeError(f"GitHub /user failed ({response.status_code})")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub /user returned invalid payload")
    return payload


def _fetch_github_org_logins(token: str) -> set[str]:
    response = _github_get("/user/orgs?per_page=100", token)
    if response.status_code != 200:
        raise RuntimeError(f"GitHub /user/orgs failed ({response.status_code})")
    payload = response.json()
    if not isinstance(payload, list):
        return set()
    logins: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        login = str(item.get("login") or "").strip().lower()
        if login:
            logins.add(login)
    return logins


def _fetch_gitlab_profile(token: str) -> dict[str, Any]:
    response = _gitlab_get("/api/v4/user", token)
    if response.status_code != 200:
        raise RuntimeError(f"GitLab /user failed ({response.status_code})")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("GitLab /user returned invalid payload")
    return payload


def _fetch_gitlab_group_paths(token: str) -> set[str]:
    groups: set[str] = set()
    page = 1
    while page <= 10:
        response = _gitlab_get(f"/api/v4/groups?per_page=100&page={page}", token)
        if response.status_code != 200:
            raise RuntimeError(f"GitLab /groups failed ({response.status_code})")
        payload = response.json()
        if not isinstance(payload, list) or not payload:
            break
        for item in payload:
            if not isinstance(item, dict):
                continue
            full_path = str(item.get("full_path") or "").strip().lower()
            if full_path:
                groups.add(full_path)
        if len(payload) < 100:
            break
        page += 1
    return groups


def _assert_valid_callback_session(state: str, provider: str) -> Any:
    session_record = get_auth_session_by_state(str(state))
    if not session_record:
        raise ValueError("Invalid or expired OAuth state")
    if session_record.expires_at < _now_utc():
        update_auth_session(session_id=session_record.session_id, status="expired")
        raise ValueError("Session expired")
    if session_record.status != "pending":
        raise ValueError("OAuth callback already used or session is no longer valid")
    expected_provider = _normalize_provider(provider)
    recorded_provider = str(session_record.oauth_provider or expected_provider).strip().lower()
    if recorded_provider != expected_provider:
        raise ValueError("OAuth provider mismatch for this session")
    return session_record


def complete_github_oauth(*, code: str, state: str) -> dict[str, Any]:
    """Complete GitHub callback and persist credentials + grants."""
    session_record = _assert_valid_callback_session(state, "github")

    oauth_payload = _github_exchange_code_for_token(code)
    access_token = str(oauth_payload.get("access_token") or "").strip()
    refresh_token = str(oauth_payload.get("refresh_token") or "").strip()
    expires_in = oauth_payload.get("expires_in")
    expires_at = None
    if isinstance(expires_in, int) and expires_in > 0:
        expires_at = _now_utc() + timedelta(seconds=expires_in)

    profile = _fetch_github_profile(access_token)
    github_user_id = int(profile.get("id") or 0)
    github_login = str(profile.get("login") or "").strip()
    if github_user_id <= 0 or not github_login:
        raise RuntimeError("GitHub profile missing id/login")

    orgs = _fetch_github_org_logins(access_token)
    allowed_orgs = _allowed_github_orgs()
    if allowed_orgs and not (orgs & allowed_orgs):
        update_auth_session(
            session_id=session_record.session_id,
            status="pending",
            last_error="User is not part of an allowed GitHub organization",
        )
        raise PermissionError("Your GitHub account is not in the allowed organizations")

    source_nexus_id = str(session_record.nexus_id)
    target_nexus_id = source_nexus_id
    existing = find_user_credentials_by_github_identity(
        github_user_id=github_user_id,
        github_login=github_login,
    )
    if existing and str(existing.nexus_id) != source_nexus_id:
        target_nexus_id = str(existing.nexus_id)

    encrypted_token = encrypt_secret(access_token, key_version=_key_version())
    encrypted_refresh = encrypt_secret(refresh_token, key_version=_key_version()) if refresh_token else None
    upsert_github_credentials(
        nexus_id=target_nexus_id,
        github_user_id=github_user_id,
        github_login=github_login,
        github_token_enc=encrypted_token,
        github_refresh_token_enc=encrypted_refresh,
        github_token_expires_at=expires_at,
        org_verified=True,
        org_verified_at=_now_utc(),
        key_version=_key_version(),
    )

    grants_count = sync_user_project_access(
        nexus_id=target_nexus_id,
        github_token=access_token,
        github_login=github_login,
    )
    update_auth_session(
        session_id=session_record.session_id,
        nexus_id=target_nexus_id,
        oauth_provider="github",
        status="oauth_done",
        last_error="",
    )
    return {
        "session_id": session_record.session_id,
        "nexus_id": target_nexus_id,
        "source_nexus_id": source_nexus_id,
        "provider": "github",
        "github_login": github_login,
        "orgs": sorted(orgs),
        "grants_count": grants_count,
    }


def complete_gitlab_oauth(*, code: str, state: str) -> dict[str, Any]:
    """Complete GitLab callback and persist credentials + grants."""
    session_record = _assert_valid_callback_session(state, "gitlab")

    oauth_payload = _gitlab_exchange_code_for_token(code)
    access_token = str(oauth_payload.get("access_token") or "").strip()
    refresh_token = str(oauth_payload.get("refresh_token") or "").strip()
    expires_in = oauth_payload.get("expires_in")
    expires_at = None
    if isinstance(expires_in, int) and expires_in > 0:
        expires_at = _now_utc() + timedelta(seconds=expires_in)

    profile = _fetch_gitlab_profile(access_token)
    gitlab_user_id = int(profile.get("id") or 0)
    gitlab_username = str(profile.get("username") or "").strip()
    if gitlab_user_id <= 0 or not gitlab_username:
        raise RuntimeError("GitLab profile missing id/username")

    groups = _fetch_gitlab_group_paths(access_token)
    allowed_groups = _allowed_gitlab_groups()
    if allowed_groups and not (groups & allowed_groups):
        update_auth_session(
            session_id=session_record.session_id,
            status="pending",
            last_error="User is not part of an allowed GitLab group",
        )
        raise PermissionError("Your GitLab account is not in the allowed groups")

    source_nexus_id = str(session_record.nexus_id)
    target_nexus_id = source_nexus_id
    existing = find_user_credentials_by_gitlab_identity(
        gitlab_user_id=gitlab_user_id,
        gitlab_username=gitlab_username,
    )
    if existing and str(existing.nexus_id) != source_nexus_id:
        target_nexus_id = str(existing.nexus_id)

    encrypted_token = encrypt_secret(access_token, key_version=_key_version())
    encrypted_refresh = encrypt_secret(refresh_token, key_version=_key_version()) if refresh_token else None
    upsert_gitlab_credentials(
        nexus_id=target_nexus_id,
        gitlab_user_id=gitlab_user_id,
        gitlab_username=gitlab_username,
        gitlab_token_enc=encrypted_token,
        gitlab_refresh_token_enc=encrypted_refresh,
        gitlab_token_expires_at=expires_at,
        org_verified=True,
        org_verified_at=_now_utc(),
        key_version=_key_version(),
    )

    grants_count = sync_user_gitlab_project_access(
        nexus_id=target_nexus_id,
        gitlab_token=access_token,
        gitlab_username=gitlab_username,
    )
    update_auth_session(
        session_id=session_record.session_id,
        nexus_id=target_nexus_id,
        oauth_provider="gitlab",
        status="oauth_done",
        last_error="",
    )
    return {
        "session_id": session_record.session_id,
        "nexus_id": target_nexus_id,
        "source_nexus_id": source_nexus_id,
        "provider": "gitlab",
        "gitlab_username": gitlab_username,
        "groups": sorted(groups),
        "grants_count": grants_count,
    }


def _validate_codex_api_key_with_provider(api_key: str) -> tuple[bool, str]:
    should_validate = str(os.getenv("NEXUS_AUTH_VALIDATE_CODEX_KEY", "false")).strip().lower()
    if should_validate not in {"1", "true", "yes", "on"}:
        return True, ""
    response = requests.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10,
    )
    if response.status_code == 200:
        return True, ""
    return False, f"Codex/OpenAI key validation failed ({response.status_code})"


def _validate_codex_api_key_with_codex_cli_login(api_key: str) -> tuple[bool, str]:
    codex_cli_path = str(os.getenv("CODEX_CLI_PATH", "codex")).strip() or "codex"
    try:
        help_result = subprocess.run(
            [codex_cli_path, "login", "--help"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except FileNotFoundError:
        return False, "Codex CLI is not installed or not in PATH for auth validation."
    except Exception as exc:
        return False, f"Codex CLI login capability check failed: {exc}"

    output = f"{help_result.stdout}\n{help_result.stderr}".lower()
    if "--with-api-key" not in output:
        return (
            False,
            "Codex CLI does not support '--with-api-key'. Upgrade Codex CLI to continue.",
        )

    try:
        with tempfile.TemporaryDirectory(prefix="nexus-codex-auth-") as tmp_codex_home:
            login_env = {**os.environ, "CODEX_HOME": tmp_codex_home}
            login_result = subprocess.run(
                [codex_cli_path, "login", "--with-api-key"],
                env=login_env,
                input=f"{api_key}\n",
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
    except subprocess.TimeoutExpired:
        return False, "Codex CLI login validation timed out."
    except Exception as exc:
        return False, f"Codex CLI login validation failed: {exc}"

    if login_result.returncode == 0:
        return True, ""
    stderr_tail = (login_result.stderr or login_result.stdout or "").strip().splitlines()
    reason = stderr_tail[-1] if stderr_tail else "unknown error"
    return False, f"Codex CLI login validation failed: {reason}"


def _validate_gemini_api_key_with_provider(api_key: str) -> tuple[bool, str]:
    should_validate = str(os.getenv("NEXUS_AUTH_VALIDATE_GEMINI_KEY", "false")).strip().lower()
    if should_validate not in {"1", "true", "yes", "on"}:
        return True, ""
    response = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": api_key},
        timeout=10,
    )
    if response.status_code == 200:
        return True, ""
    return False, f"Gemini key validation failed ({response.status_code})"


def _validate_claude_api_key_with_provider(api_key: str) -> tuple[bool, str]:
    should_validate = str(os.getenv("NEXUS_AUTH_VALIDATE_CLAUDE_KEY", "false")).strip().lower()
    if should_validate not in {"1", "true", "yes", "on"}:
        return True, ""
    response = requests.get(
        "https://api.anthropic.com/v1/models",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        timeout=10,
    )
    if response.status_code == 200:
        return True, ""
    return False, f"Claude key validation failed ({response.status_code})"


def _cli_account_auth_mode_enabled() -> bool:
    mode = str(os.getenv("NEXUS_CLI_AUTH_MODE", "account")).strip().lower()
    return mode in {"account", "auto"}


def store_ai_provider_keys(
    *,
    session_id: str,
    codex_api_key: str | None = None,
    gemini_api_key: str | None = None,
    claude_api_key: str | None = None,
    copilot_github_token: str | None = None,
    allow_copilot: bool = False,
    use_codex_account: bool | None = None,
    use_gemini_account: bool | None = None,
    use_claude_account: bool | None = None,
    use_copilot_account: bool | None = None,
) -> dict[str, Any]:
    resolved_session_id = resolve_login_session_id(session_id)
    session_record = get_auth_session(str(resolved_session_id))
    if not session_record:
        raise ValueError("Invalid session")
    if session_record.expires_at < _now_utc():
        update_auth_session(session_id=str(resolved_session_id), status="expired")
        raise ValueError("Session expired")
    if session_record.status not in {"oauth_done", "completed"}:
        raise ValueError("OAuth step is not complete yet")

    codex_provided = codex_api_key is not None
    gemini_provided = gemini_api_key is not None
    claude_provided = claude_api_key is not None
    copilot_provided = copilot_github_token is not None
    codex_candidate = str(codex_api_key or "").strip()
    gemini_candidate = str(gemini_api_key or "").strip()
    claude_candidate = str(claude_api_key or "").strip()
    copilot_candidate = str(copilot_github_token or "").strip()
    record = get_user_credentials(session_record.nexus_id)
    existing_ai_key_set = bool(
        record
        and (
            record.codex_api_key_enc
            or record.gemini_api_key_enc
            or record.claude_api_key_enc
        )
    )
    has_github_for_copilot = bool(record and record.github_token_enc and record.github_login)
    has_stored_copilot_token = bool(record and record.copilot_github_token_enc)
    codex_account_enabled = bool(use_codex_account) if use_codex_account is not None else bool(
        record and getattr(record, "codex_account_enabled", False)
    )
    gemini_account_enabled = bool(use_gemini_account) if use_gemini_account is not None else bool(
        record and getattr(record, "gemini_account_enabled", False)
    )
    claude_account_enabled = bool(use_claude_account) if use_claude_account is not None else bool(
        record and getattr(record, "claude_account_enabled", False)
    )
    copilot_account_enabled = bool(use_copilot_account) if use_copilot_account is not None else bool(
        record and getattr(record, "copilot_account_enabled", False)
    )
    account_selected = bool(
        codex_account_enabled or gemini_account_enabled or claude_account_enabled or copilot_account_enabled
    )
    if account_selected and not _cli_account_auth_mode_enabled():
        raise ValueError(
            "CLI account login is selected, but NEXUS_CLI_AUTH_MODE is not account/auto. "
            "Set NEXUS_CLI_AUTH_MODE=account (recommended) or auto."
        )

    if copilot_account_enabled and not has_github_for_copilot:
        raise ValueError(
            "Copilot account mode requires linked GitHub OAuth. Run `/login github` first."
        )

    if not codex_candidate and not gemini_candidate and not claude_candidate and allow_copilot:
        copilot_available = bool(
            has_github_for_copilot
            or has_stored_copilot_token
            or (copilot_provided and copilot_candidate)
            or copilot_account_enabled
        )
        if not copilot_available:
            raise ValueError(
                "Copilot requires a linked GitHub account or Copilot Token. "
                "Run `/login github`, provide Copilot Token, or disable Copilot."
            )

    codex_encrypted: str | None = None
    gemini_encrypted: str | None = None
    claude_encrypted: str | None = None
    copilot_encrypted: str | None = None

    if codex_provided and not codex_candidate:
        codex_encrypted = ""
    elif codex_candidate:
        if len(codex_candidate) < 16:
            raise ValueError("Codex API key is too short")
        valid, error_message = _validate_codex_api_key_with_codex_cli_login(codex_candidate)
        if not valid:
            raise ValueError(error_message or "Invalid Codex API key")
        valid, error_message = _validate_codex_api_key_with_provider(codex_candidate)
        if not valid:
            raise ValueError(error_message or "Invalid Codex API key")
        codex_encrypted = encrypt_secret(codex_candidate, key_version=_key_version())

    if gemini_provided and not gemini_candidate:
        gemini_encrypted = ""
    elif gemini_candidate:
        if len(gemini_candidate) < 16:
            raise ValueError("Gemini API key is too short")
        valid, error_message = _validate_gemini_api_key_with_provider(gemini_candidate)
        if not valid:
            raise ValueError(error_message or "Invalid Gemini API key")
        gemini_encrypted = encrypt_secret(gemini_candidate, key_version=_key_version())

    if claude_provided and not claude_candidate:
        claude_encrypted = ""
    elif claude_candidate:
        if len(claude_candidate) < 16:
            raise ValueError("Claude API key is too short")
        valid, error_message = _validate_claude_api_key_with_provider(claude_candidate)
        if not valid:
            raise ValueError(error_message or "Invalid Claude API key")
        claude_encrypted = encrypt_secret(claude_candidate, key_version=_key_version())

    if copilot_provided and not copilot_candidate:
        copilot_encrypted = ""
    elif copilot_candidate:
        if len(copilot_candidate) < 16:
            raise ValueError("Copilot Token is too short")
        copilot_encrypted = encrypt_secret(copilot_candidate, key_version=_key_version())

    upsert_ai_provider_keys(
        nexus_id=session_record.nexus_id,
        codex_api_key_enc=codex_encrypted,
        gemini_api_key_enc=gemini_encrypted,
        claude_api_key_enc=claude_encrypted,
        copilot_github_token_enc=copilot_encrypted,
        codex_account_enabled=codex_account_enabled,
        gemini_account_enabled=gemini_account_enabled,
        claude_account_enabled=claude_account_enabled,
        copilot_account_enabled=copilot_account_enabled,
        key_version=_key_version(),
    )

    update_auth_session(
        session_id=str(resolved_session_id),
        status="completed",
        last_error="",
        used_at=_now_utc(),
    )

    status = get_setup_status(session_record.nexus_id)
    return {
        "session_id": session_record.session_id,
        "session_ref": format_login_session_ref(session_record.session_id),
        "nexus_id": session_record.nexus_id,
        "ready": bool(status.get("ready")),
        "project_access_count": int(status.get("project_access_count") or 0),
    }


def start_provider_account_login(*, session_id: str, provider: str) -> dict[str, Any]:
    resolved_session_id = resolve_login_session_id(session_id)
    session_record = get_auth_session(str(resolved_session_id))
    if not session_record:
        raise ValueError("Invalid session")
    if session_record.expires_at < _now_utc():
        update_auth_session(session_id=str(resolved_session_id), status="expired")
        raise ValueError("Session expired")
    if session_record.status not in {"oauth_done", "completed"}:
        raise ValueError("OAuth step is not complete yet")

    provider_name = str(provider or "").strip().lower()
    if provider_name != "codex":
        raise ValueError("Only Codex account-connect is supported right now")

    codex_cli_path = str(os.getenv("CODEX_CLI_PATH", "codex")).strip() or "codex"
    codex_home = _provider_runtime_home(provider=provider_name, nexus_id=session_record.nexus_id)
    _ensure_private_dir(codex_home)
    _ensure_private_dir(os.path.join(codex_home, "log"))
    _ensure_private_dir(os.path.join(codex_home, "memories"))
    log_dir = os.path.join(codex_home, "device-auth")
    _ensure_private_dir(log_dir)
    timestamp = _now_utc().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"{provider_name}_{timestamp}.log")

    job_key = _device_job_key(session_id=str(session_record.session_id), provider=provider_name)
    with _DEVICE_AUTH_LOCK:
        existing = _DEVICE_AUTH_JOBS.get(job_key)
        if isinstance(existing, dict):
            existing_proc = existing.get("process")
            if getattr(existing_proc, "poll", None) and existing_proc.poll() is None:
                log_text = _log_tail(str(existing.get("log_path") or ""))
                verify_url, user_code = _parse_device_auth_url_and_code(log_text)
                return {
                    "started": False,
                    "session_id": session_record.session_id,
                    "session_ref": format_login_session_ref(session_record.session_id),
                    "provider": provider_name,
                    "state": "pending",
                    "verify_url": verify_url,
                    "user_code": user_code,
                    "message": "Device-auth login is already running.",
                }
            existing_handle = existing.get("log_file")
            if existing_handle is not None:
                try:
                    existing_handle.close()
                except Exception:
                    pass
            _DEVICE_AUTH_JOBS.pop(job_key, None)

        try:
            log_file = open(log_path, "a", encoding="utf-8")
        except Exception as exc:
            raise RuntimeError(f"Unable to create device-auth log file: {exc}") from exc
        _ensure_private_file(log_path)

        env = {**os.environ, "CODEX_HOME": codex_home}
        try:
            process = subprocess.Popen(
                [codex_cli_path, "login", "--device-auth"],
                cwd=codex_home,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except Exception as exc:
            try:
                log_file.close()
            except Exception:
                pass
            raise RuntimeError(f"Failed to start Codex device-auth login: {exc}") from exc

        _DEVICE_AUTH_JOBS[job_key] = {
            "provider": provider_name,
            "session_id": session_record.session_id,
            "nexus_id": session_record.nexus_id,
            "process": process,
            "log_path": log_path,
            "log_file": log_file,
            "started_at": _now_utc().isoformat(),
            "codex_home": codex_home,
        }

    return {
        "started": True,
        "session_id": session_record.session_id,
        "session_ref": format_login_session_ref(session_record.session_id),
        "provider": provider_name,
        "state": "starting",
        "verify_url": "",
        "user_code": "",
        "message": "Codex account connection started. Poll status for device instructions.",
    }


def get_provider_account_login_status(*, session_id: str, provider: str) -> dict[str, Any]:
    resolved_session_id = resolve_login_session_id(session_id)
    session_record = get_auth_session(str(resolved_session_id))
    if not session_record:
        return {"exists": False, "state": "invalid_session", "message": "Invalid session"}

    provider_name = str(provider or "").strip().lower()
    if provider_name != "codex":
        return {
            "exists": False,
            "session_id": session_record.session_id,
            "session_ref": format_login_session_ref(session_record.session_id),
            "provider": provider_name,
            "state": "unsupported_provider",
            "message": "Only Codex account-connect is supported right now",
        }

    setup = get_setup_status(session_record.nexus_id)
    connected_flag = bool(setup.get("codex_account_enabled"))
    job_key = _device_job_key(session_id=session_record.session_id, provider=provider_name)

    with _DEVICE_AUTH_LOCK:
        job = _DEVICE_AUTH_JOBS.get(job_key)
        if not isinstance(job, dict):
            return {
                "exists": False,
                "session_id": session_record.session_id,
                "session_ref": format_login_session_ref(session_record.session_id),
                "provider": provider_name,
                "state": "idle",
                "connected": connected_flag,
                "verify_url": "",
                "user_code": "",
                "message": "No active account-connect job.",
            }

        process = job.get("process")
        log_path = str(job.get("log_path") or "")
        log_text = _log_tail(log_path)
        verify_url, user_code = _parse_device_auth_url_and_code(log_text)
        exit_code = process.poll() if getattr(process, "poll", None) else None

        if exit_code is None:
            return {
                "exists": True,
                "session_id": session_record.session_id,
                "session_ref": format_login_session_ref(session_record.session_id),
                "provider": provider_name,
                "state": "pending",
                "connected": connected_flag,
                "verify_url": verify_url,
                "user_code": user_code,
                "message": "Login is waiting for browser confirmation.",
            }

        log_file = job.get("log_file")
        if log_file is not None:
            try:
                log_file.close()
            except Exception:
                pass
        _DEVICE_AUTH_JOBS.pop(job_key, None)

    if int(exit_code) == 0:
        try:
            store_ai_provider_keys(
                session_id=session_record.session_id,
                use_codex_account=True,
            )
        except Exception as exc:
            return {
                "exists": True,
                "session_id": session_record.session_id,
                "session_ref": format_login_session_ref(session_record.session_id),
                "provider": provider_name,
                "state": "connected_but_not_saved",
                "connected": connected_flag,
                "verify_url": verify_url,
                "user_code": user_code,
                "message": f"Login succeeded but saving setup failed: {exc}",
            }

        refreshed = get_setup_status(session_record.nexus_id)
        return {
            "exists": True,
            "session_id": session_record.session_id,
            "session_ref": format_login_session_ref(session_record.session_id),
            "provider": provider_name,
            "state": "connected",
            "connected": bool(refreshed.get("codex_account_enabled")),
            "verify_url": verify_url,
            "user_code": user_code,
            "message": "Codex account connected successfully.",
        }

    error_tail = log_text.strip().splitlines()
    error_message = error_tail[-1] if error_tail else "Device-auth process failed"
    return {
        "exists": True,
        "session_id": session_record.session_id,
        "session_ref": format_login_session_ref(session_record.session_id),
        "provider": provider_name,
        "state": "failed",
        "connected": connected_flag,
        "verify_url": verify_url,
        "user_code": user_code,
        "message": error_message,
    }


def get_session_and_setup_status(session_id: str) -> dict[str, Any]:
    resolved_session_id = resolve_login_session_id(session_id)
    session_record = get_auth_session(str(resolved_session_id))
    if not session_record:
        return {"exists": False}
    setup = get_setup_status(session_record.nexus_id)
    return {
        "exists": True,
        "session_id": session_record.session_id,
        "session_ref": format_login_session_ref(session_record.session_id),
        "status": session_record.status,
        "provider": session_record.oauth_provider,
        "expires_at": session_record.expires_at.isoformat() if session_record.expires_at else None,
        "last_error": session_record.last_error,
        "nexus_id": session_record.nexus_id,
        "setup": setup,
    }


def get_latest_login_session_status(nexus_id: str) -> dict[str, Any]:
    record = get_latest_auth_session_for_nexus(str(nexus_id))
    if not record:
        return {"exists": False}
    return {
        "exists": True,
        "session_id": record.session_id,
        "session_ref": format_login_session_ref(record.session_id),
        "status": record.status,
        "provider": record.oauth_provider,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "last_error": record.last_error,
        "nexus_id": record.nexus_id,
    }
