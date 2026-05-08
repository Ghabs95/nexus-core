"""Lightweight schema validation helpers for Nexus workflow nodes."""

from __future__ import annotations

from dataclasses import is_dataclass
from typing import Any


class WorkflowSchemaError(TypeError):
    """Raised when a workflow payload does not satisfy a node schema."""


def validate_payload(schema: type | None, value: Any, *, node_name: str, direction: str) -> Any:
    """Validate or coerce a payload against a lightweight Python schema.

    Supported schemas:
    - normal Python classes: pass when ``isinstance(value, schema)``
    - dataclasses: accept existing instance, or construct from dict
    - Pydantic v2 models: use ``model_validate`` when available
    - Pydantic v1 models / similar: use ``parse_obj`` when available
    """
    if schema is None:
        return value

    if isinstance(value, schema):
        return value

    try:
        if hasattr(schema, "model_validate"):
            return schema.model_validate(value)
        if hasattr(schema, "parse_obj"):
            return schema.parse_obj(value)
        if is_dataclass(schema) and isinstance(value, dict):
            return schema(**value)
    except Exception as exc:  # pragma: no cover - exact validator errors vary
        raise WorkflowSchemaError(
            f"{node_name} {direction} does not match schema {schema!r}: {exc}"
        ) from exc

    raise WorkflowSchemaError(
        f"{node_name} {direction} expected {schema!r}, got {type(value).__name__}"
    )
