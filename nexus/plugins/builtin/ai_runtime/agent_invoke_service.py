import re
from typing import Any, Callable


def extract_issue_number(issue_url: str | None) -> str | None:
    if not issue_url:
        return None
    match = re.search(r"/issues/(\d+)", issue_url)
    return match.group(1) if match else None


def invoke_agent_with_fallback(
    *,
    issue_url: str | None,
    exclude_tools: list[Any] | None,
    get_tool_order: Callable[[], list[Any]],
    check_tool_available: Callable[[Any], bool],
    invoke_tool: Callable[[Any, str | None], int | None],
    record_rate_limit_with_context: Callable[[Any, Exception, str], None],
    record_failure: Callable[[Any], None],
    rate_limited_error_type: type[Exception],
    tool_unavailable_error_type: type[Exception],
    logger: Any,
) -> tuple[int | None, Any]:
    issue_num = extract_issue_number(issue_url)

    excluded = set(exclude_tools or [])
    ordered = get_tool_order()
    candidates = [t for t in ordered if getattr(t, "value", t) not in excluded]
    if not candidates:
        raise tool_unavailable_error_type(
            f"All tools excluded. Order: {[getattr(t, 'value', t) for t in ordered]}, "
            f"Excluded: {list(excluded)}"
        )

    tried: list[str] = []
    for tool in candidates:
        tool_value = getattr(tool, "value", str(tool))
        if not check_tool_available(tool):
            logger.warning("⏭️  Skipping unavailable tool: %s", tool_value)
            tried.append(f"{tool_value}(unavailable)")
            continue
        try:
            if tried:
                logger.info("🔄 Trying next tool %s (previously tried: %s)", tool_value, tried)
            pid = invoke_tool(tool, issue_num)
            if pid:
                if tried:
                    logger.info("✅ %s succeeded after: %s", tool_value, tried)
                return pid, tool
            tried.append(f"{tool_value}(no-pid)")
        except rate_limited_error_type as exc:
            remaining = [getattr(t, "value", str(t)) for t in candidates if t != tool]
            logger.warning(
                "⏸️  %s rate-limited/quota: %s. Falling back to: %s",
                tool_value,
                exc,
                ", ".join(remaining) if remaining else "none",
            )
            record_rate_limit_with_context(tool, exc, "invoke_agent")
            tried.append(f"{tool_value}(rate-limited)")
        except Exception as exc:
            logger.error("❌ %s invocation failed: %s", tool_value, exc)
            record_failure(tool)
            tried.append(f"{tool_value}(error)")

    raise tool_unavailable_error_type(
        f"All AI tools exhausted. Tried: {tried}, Excluded: {list(excluded)}"
    )
