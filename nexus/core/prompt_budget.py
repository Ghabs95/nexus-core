"""Utilities for prompt budgeting, deterministic truncation, and compact summaries."""

from __future__ import annotations

import hashlib
import re
from typing import Any


def prompt_prefix_fingerprint(text: str, prefix_chars: int = 1024) -> str:
    """Return a stable SHA-256 fingerprint of the leading prompt prefix."""
    prefix = str(text or "")[: max(0, int(prefix_chars))]
    return hashlib.sha256(prefix.encode("utf-8", errors="ignore")).hexdigest()[:16]


def truncate_text(text: str, max_chars: int, suffix: str = "\n\n[truncated]") -> str:
    """Deterministically truncate *text* to at most *max_chars* characters."""
    value = str(text or "")
    limit = max(0, int(max_chars))
    if len(value) <= limit:
        return value
    if limit <= len(suffix):
        return value[:limit]
    return value[: limit - len(suffix)] + suffix


def summarize_text(text: str, max_chars: int = 1200, max_items: int = 10) -> str:
    """Build a compact bullet summary from plain text.

    This summarizer is deterministic and heuristic-based to avoid additional LLM calls.
    """
    value = str(text or "").strip()
    if not value:
        return ""

    lines = [line.strip() for line in re.split(r"\r?\n+", value) if line.strip()]
    if not lines:
        return truncate_text(value, max_chars=max_chars)

    bullets: list[str] = []
    seen: set[str] = set()
    for line in lines:
        normalized = re.sub(r"\s+", " ", line).strip(" -\t")
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        if len(normalized) > 220:
            normalized = normalized[:217].rstrip() + "..."
        bullets.append(f"- {normalized}")
        if len(bullets) >= max_items:
            break

    if not bullets:
        return truncate_text(value, max_chars=max_chars)

    summary = "Summary:\n" + "\n".join(bullets)
    return truncate_text(summary, max_chars=max_chars)


def apply_prompt_budget(
    text: str,
    *,
    max_chars: int,
    summary_max_chars: int = 1200,
) -> dict[str, Any]:
    """Return prompt-safe text plus metadata about summarization/truncation."""
    value = str(text or "")
    original_chars = len(value)
    if original_chars <= max_chars:
        return {
            "text": value,
            "original_chars": original_chars,
            "final_chars": original_chars,
            "summarized": False,
            "truncated": False,
        }

    summarized_text = summarize_text(value, max_chars=summary_max_chars)
    summarized_chars = len(summarized_text)
    if summarized_chars <= max_chars:
        return {
            "text": summarized_text,
            "original_chars": original_chars,
            "final_chars": summarized_chars,
            "summarized": True,
            "truncated": summarized_chars < original_chars,
        }

    truncated_text = truncate_text(summarized_text, max_chars=max_chars)
    return {
        "text": truncated_text,
        "original_chars": original_chars,
        "final_chars": len(truncated_text),
        "summarized": True,
        "truncated": True,
    }
