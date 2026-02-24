from __future__ import annotations

import json
import re
from typing import Any


def normalize_task_name(value: str, max_len: int = 80) -> str:
    candidate = str(value or "").strip().lower()
    if not candidate:
        return ""
    candidate = re.sub(r"[^a-z0-9]+", "-", candidate).strip("-")
    if not candidate:
        return ""
    return candidate[:max_len]


def generate_task_name(
    orchestrator: Any,
    content: str,
    project_name: str,
    logger: Any | None = None,
) -> str:
    def _extract_candidate_name(payload: Any) -> str:
        if isinstance(payload, str):
            candidate = str(payload).strip().strip('"`\'')
            if candidate:
                return candidate
            return ""

        if not isinstance(payload, dict):
            return ""

        for key in ("task_name", "name", "title", "text"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return str(value).strip().strip('"`\'')

        for key in ("response", "output", "message", "content"):
            value = payload.get(key)
            if isinstance(value, dict):
                nested = _extract_candidate_name(value)
                if nested:
                    return nested
            if isinstance(value, str) and value.strip():
                nested_str = value.strip()
                try:
                    parsed = json.loads(nested_str)
                    nested = _extract_candidate_name(parsed)
                    if nested:
                        return nested
                except Exception:
                    if nested_str:
                        return nested_str.strip().strip('"`\'')

        return ""

    try:
        result = orchestrator.run_text_to_speech_analysis(
            text=str(content or "")[:300],
            task="generate_name",
            project_name=project_name,
        )
        return normalize_task_name(_extract_candidate_name(result))
    except Exception as exc:
        if logger is not None:
            log_warning = getattr(logger, "warning", None)
            if callable(log_warning):
                log_warning("Task name generation failed: %s", exc)
    return ""
