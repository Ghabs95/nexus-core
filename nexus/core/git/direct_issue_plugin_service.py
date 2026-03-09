from nexus.adapters.git.factory import get_git_platform_transport


def get_direct_issue_plugin(
    *,
    repo: str,
    get_profiled_plugin,
    requester_nexus_id: str | None = None,
):
    """Resolve direct issue plugin for the current transport policy."""
    requester = str(requester_nexus_id or "").strip() or None
    overrides = {"repo": repo}
    if requester:
        overrides["requester_nexus_id"] = requester
    transport = get_git_platform_transport()
    cache_key = (
        f"git:direct:{transport}:{repo}:{requester}"
        if requester
        else f"git:direct:{transport}:{repo}"
    )
    return get_profiled_plugin(
        "git_agent_launcher",
        overrides=overrides,
        cache_key=cache_key,
    )
