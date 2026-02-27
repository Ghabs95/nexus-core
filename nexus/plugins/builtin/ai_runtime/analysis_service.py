import json
import re
from typing import Any, Callable


def run_analysis_with_provider(
    *,
    tool: Any,
    gemini_provider: Any,
    copilot_provider: Any,
    codex_provider: Any | None = None,
    run_gemini_cli_analysis: Callable[..., dict[str, Any]],
    run_copilot_analysis: Callable[..., dict[str, Any]],
    run_codex_analysis: Callable[..., dict[str, Any]] | None = None,
    text: str,
    task: str,
    kwargs: dict[str, Any],
    tool_unavailable_error: type[Exception],
) -> dict[str, Any]:
    """Dispatch an analysis task to the provider-specific implementation."""
    if tool == gemini_provider:
        return run_gemini_cli_analysis(text, task, **kwargs)
    if tool == copilot_provider:
        return run_copilot_analysis(text, task, **kwargs)
    if codex_provider is not None and tool == codex_provider and callable(run_codex_analysis):
        return run_codex_analysis(text, task, **kwargs)
    raise tool_unavailable_error(f"{getattr(tool, 'value', tool)} does not support analysis tasks")


def run_analysis_attempts(
    *,
    tool_order: list[Any],
    text: str,
    task: str,
    kwargs: dict[str, Any],
    invoke_provider: Callable[[Any, str, str, dict[str, Any]], dict[str, Any]],
    rate_limited_error_type: type[Exception],
    record_rate_limit_with_context: Callable[[Any, Exception, int, str], None],
    get_default_analysis_result: Callable[..., dict[str, Any]],
    logger: Any,
) -> dict[str, Any]:
    """Execute provider attempts for an analysis task with fallback and defaults."""
    last_error: Exception | None = None
    for index, tool in enumerate(tool_order):
        try:
            result = invoke_provider(tool, text, task, kwargs)
            if result:
                logger.info(
                    "🧠 Analysis reply provider: task=%s provider=%s",
                    task,
                    getattr(tool, "value", tool),
                )
                if index > 0:
                    logger.info("✅ Fallback analysis succeeded with %s", tool.value)
                return result
        except rate_limited_error_type as exc:
            last_error = exc
            record_rate_limit_with_context(tool, exc, 1, f"analysis:{task}")
        except Exception as exc:
            last_error = exc
            logger.warning("⚠️  %s analysis failed: %s", tool.value, exc)

    if last_error:
        logger.error("❌ All analysis providers failed for %s: %s", task, last_error)

    logger.warning("⚠️  All tools failed for %s, returning default", task)
    return get_default_analysis_result(task, text=text, **kwargs)


def strip_cli_tool_output(text: str) -> str:
    """Remove Copilot/Gemini CLI tool-use artifacts from analysis output."""
    lines = text.splitlines()
    cleaned: list[str] = []
    skip_until_blank = False
    for line in lines:
        stripped = line.lstrip()
        # Legacy tool transcript blocks (Copilot/Gemini) and newer Codex transcript
        # entries share the same "header + indented detail lines + blank separator"
        # shape, so strip them uniformly.
        if stripped.startswith("●") or stripped.startswith("✗ ") or stripped.startswith("✓ "):
            skip_until_blank = True
            continue
        if skip_until_blank and stripped.startswith("$"):
            continue
        if skip_until_blank and stripped.startswith("└"):
            continue
        if skip_until_blank and (line.startswith("  ") or line.startswith("\t")):
            continue
        if not stripped:
            skip_until_blank = False
            if cleaned:
                cleaned.append(line)
            continue
        skip_until_blank = False
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def parse_analysis_result(output: str, task: str, *, logger: Any) -> dict[str, Any]:
    """Parse analysis result, preferring JSON and falling back to raw text."""
    cleaned_output = strip_cli_tool_output(output)

    candidates: list[str] = [cleaned_output.strip()]
    fenced_blocks = re.findall(
        r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned_output, flags=re.IGNORECASE
    )
    candidates.extend(block.strip() for block in fenced_blocks if block.strip())

    first_brace = cleaned_output.find("{")
    last_brace = cleaned_output.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidates.append(cleaned_output[first_brace : last_brace + 1].strip())

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    looks_like_json = "{" in cleaned_output and "}" in cleaned_output
    if looks_like_json or "```" in cleaned_output:
        logger.warning("Failed to parse %s result as JSON: %s", task, cleaned_output[:100])
        return {"text": cleaned_output, "parse_error": True}

    return {"text": cleaned_output}


def build_analysis_prompt(text: str, task: str, **kwargs) -> str:
    """Build provider-agnostic analysis prompts for supported analysis tasks."""
    if task == "classify":
        projects = kwargs.get("projects", [])
        types = kwargs.get("types", [])
        return f"""Classify this task:
Text: {text[:500]}

1. Map to project (one of: {", ".join(projects)}). Use key format.
2. Classify type (one of: {", ".join(types)}).
3. Generate concise task name (3-6 words, kebab-case).
4. Return JSON: {{"project": "key", "type": "type_key", "task_name": "name"}}

Return ONLY valid JSON."""

    if task == "route":
        return f"""Route this task to the best agent:
{text[:500]}

1. Identify primary work type (coding, design, testing, ops, content).
2. Suggest best agent.
3. Rate confidence 0-100.
4. Return JSON: {{"agent": "name", "type": "work_type", "confidence": 85}}

Return ONLY valid JSON."""

    if task == "generate_name":
        project = kwargs.get("project_name", "")
        return f"""Generate a concise task name (3-6 words, kebab-case):
{text[:300]}
Project: {project}

Return ONLY the name, no quotes."""

    if task == "refine_description":
        return f"""Rewrite this task description to be clear, concise, and structured.
Preserve all concrete requirements, constraints, and details. Do not invent facts.

Return in plain text (no Markdown headers), using short paragraphs and bullet points if helpful.

Input:
{text.strip()}
"""

    if task == "detect_intent":
        return f"""Classify this user input for routing.

Input:
{text[:500]}

Return ONLY valid JSON with this shape:
{{
  "intent": "task" | "conversation",
  "intent_confidence": 0.0-1.0,
  "feature_ideation": true | false,
  "feature_ideation_confidence": 0.0-1.0,
  "feature_ideation_reason": "short reason"
}}

Rules:
- Set "intent" to "task" for concrete implementation requests (feature, bug, chore) that should go to developer inbox.
- Set "intent" to "conversation" for advisory questions, brainstorming, analysis, or discussion.
- Set "feature_ideation" to true only when the user explicitly asks for new feature ideas/proposals/roadmap suggestions.
- Set "feature_ideation" to false for implementation/status/history questions (including "what was already implemented").
- Support multilingual input; do not assume English-only phrasing.

Return ONLY valid JSON."""

    if task == "detect_feature_ideation":
        return f"""Decide whether the user is asking for feature ideation/brainstorming.

Input:
{text[:500]}

Return ONLY valid JSON:
{{"feature_ideation": true|false, "confidence": 0.0-1.0, "reason": "short reason"}}

Set feature_ideation=true only if the user is asking for new feature proposals/ideas/roadmap items.
Set feature_ideation=false for status/history/reporting questions (e.g., asking what was already implemented).
Support multilingual user text; do not assume English-only phrasing.
"""

    if task == "chat":
        history = kwargs.get("history", "")
        persona = kwargs.get("persona", "You are a helpful AI assistant.")
        return f"""{persona}

Recent Conversation History:
{history}

User Input:
{text.strip()}"""

    return text
