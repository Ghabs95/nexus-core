from nexus.adapters.git.factory import get_git_platform_transport


def _resolve_project_key_from_repo(repo: str) -> str | None:
    target_repo = str(repo or "").strip()
    if not target_repo:
        return None

    try:
        from nexus.core.config import PROJECT_CONFIG, get_repos
        from nexus.core.project.repo_utils import iter_project_configs, project_repos_from_config
    except Exception:
        return None

    if not isinstance(PROJECT_CONFIG, dict):
        return None

    for project_key, project_cfg in iter_project_configs(PROJECT_CONFIG, get_repos):
        if target_repo in project_repos_from_config(project_key, project_cfg, get_repos):
            return str(project_key)
    return None


def _canonical_project_key(project_name: str | None) -> str | None:
    raw = str(project_name or "").strip()
    if not raw:
        return None

    try:
        from nexus.core.config import PROJECT_CONFIG, normalize_project_key
    except Exception:
        return raw

    normalized = normalize_project_key(raw)
    if normalized:
        return str(normalized)

    if isinstance(PROJECT_CONFIG, dict):
        if raw in PROJECT_CONFIG and isinstance(PROJECT_CONFIG.get(raw), dict):
            return raw
        lowered = raw.lower()
        if lowered in PROJECT_CONFIG and isinstance(PROJECT_CONFIG.get(lowered), dict):
            return lowered
    return raw


def _resolve_project_platform(project_key: str | None) -> tuple[str, dict]:
    if not project_key:
        return "github", {}

    try:
        from nexus.core.config import PROJECT_CONFIG, get_project_platform
    except Exception:
        return "github", {}

    cfg = PROJECT_CONFIG.get(project_key, {}) if isinstance(PROJECT_CONFIG, dict) else {}
    if not isinstance(cfg, dict):
        cfg = {}

    platform = str(get_project_platform(project_key) or "github").strip().lower()
    if platform != "gitlab":
        # Backward-compatibility inference: configs that only define gitlab_base_url
        # are still GitLab projects for issue operations.
        raw_platform = str(cfg.get("git_platform") or "").strip().lower()
        if not raw_platform and str(cfg.get("gitlab_base_url") or "").strip():
            platform = "gitlab"
    if platform not in {"github", "gitlab"}:
        platform = "github"
    return platform, cfg


def get_direct_issue_plugin(
    *,
    repo: str,
    get_profiled_plugin,
    requester_nexus_id: str | None = None,
    project_name: str | None = None,
):
    """Resolve direct issue plugin for the current transport policy."""
    requester = str(requester_nexus_id or "").strip() or None
    project_key = _canonical_project_key(project_name) or _resolve_project_key_from_repo(repo)
    platform, project_cfg = _resolve_project_platform(project_key)
    profile = "gitlab_agent_launcher" if platform == "gitlab" else "git_agent_launcher"

    overrides = {"repo": repo}
    if requester:
        overrides["requester_nexus_id"] = requester
    if platform == "gitlab":
        base_url = str(project_cfg.get("gitlab_base_url") or "").strip()
        if base_url:
            overrides["gitlab_base_url"] = base_url

    transport = get_git_platform_transport()
    cache_key = (
        f"git:direct:{profile}:{transport}:{repo}:{requester}"
        if requester
        else f"git:direct:{profile}:{transport}:{repo}"
    )
    return get_profiled_plugin(
        profile,
        overrides=overrides,
        cache_key=cache_key,
    )
