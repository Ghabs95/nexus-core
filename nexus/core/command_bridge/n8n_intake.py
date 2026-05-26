"""n8n-facing intake bridge for Nexus inbox task capture."""

from __future__ import annotations

import uuid
import json
from typing import Any

from nexus.core.handlers.inbox_routing_handler import process_inbox_task
from nexus.core.orchestration.ai_orchestrator import get_orchestrator


async def create_intake_task(payload: dict[str, Any]) -> dict[str, Any]:
    """Capture an n8n request through the same inbox path used by chat surfaces."""
    payload = _normalize_payload(payload)
    text = _first_str(
        payload,
        "text",
        "task",
        "message",
        "title",
        "request",
        "chatInput",
        "input",
        "prompt",
        "idea",
        "feature",
    )
    if not text:
        raise ValueError("text or task is required")

    message_id = _first_str(payload, "message_id", "event_id", "run_id")
    if not message_id:
        message_id = f"n8n-{uuid.uuid4().hex[:12]}"

    requester = payload.get("requester")
    requester_context = dict(requester) if isinstance(requester, dict) else {}
    requester_context.setdefault("source", "n8n")

    project_hint = _first_str(payload, "project_key", "project")
    agent_type = _first_str(payload, "agent_type", "agent")
    execution_mode = _first_str(payload, "execution_mode")
    issue_labels = _labels(payload.get("issue_labels") or payload.get("labels"))
    if "source:n8n" not in issue_labels:
        issue_labels.append("source:n8n")

    result = await process_inbox_task(
        text,
        get_orchestrator(),
        message_id,
        project_hint=project_hint or None,
        requester_context=requester_context,
        agent_type=agent_type or None,
        issue_labels=issue_labels,
        execution_mode=execution_mode or None,
    )
    ok = bool(result.get("success")) if isinstance(result, dict) else False
    return {"ok": ok, "result": result}


def _first_str(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Flatten common n8n trigger envelopes into the intake contract.

    n8n Webhook/Form/Chat nodes often wrap user input under keys such as
    ``body`` or ``chatInput``. The bridge accepts those shapes so workflows can
    call the intake endpoint directly without fragile Code-node reshaping.
    """
    if not isinstance(payload, dict):
        return {}

    normalized: dict[str, Any] = {}
    nested_keys = ("body", "json", "data", "payload", "fields", "query", "params")

    for key in nested_keys:
        nested = _coerce_mapping(payload.get(key))
        if nested:
            normalized.update(nested)

    for key, value in payload.items():
        if key in nested_keys:
            if isinstance(value, str) and value.strip() and "text" not in normalized:
                normalized["text"] = value.strip()
            continue
        normalized[key] = value

    return normalized


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"text": value.strip()}
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, str) and parsed.strip():
            return {"text": parsed.strip()}
    return {}


def _labels(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return []
