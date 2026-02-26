import logging
from typing import Any

logger = logging.getLogger(__name__)


def evaluate_condition(
    condition: str | None,
    context: dict[str, Any],
    *,
    default_on_error: bool = True,
) -> bool:
    """Evaluate a workflow condition expression against context."""
    if not condition:
        return True
    try:
        eval_locals = dict(context)
        eval_locals.setdefault("true", True)
        eval_locals.setdefault("false", False)
        eval_locals.setdefault("null", None)
        result = eval(condition, {"__builtins__": {}}, eval_locals)  # noqa: S307
        return bool(result)
    except Exception as exc:
        logger.warning(
            "Condition evaluation error for '%s': %s. Defaulting to %s.",
            condition,
            exc,
            default_on_error,
        )
        return default_on_error

