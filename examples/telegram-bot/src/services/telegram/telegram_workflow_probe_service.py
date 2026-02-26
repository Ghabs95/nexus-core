import asyncio
import threading
from typing import Any


def get_expected_running_agent_from_workflow(
    *, issue_num: str, get_workflow_id, workflow_plugin
) -> str | None:
    workflow_id = get_workflow_id(str(issue_num))
    if not workflow_id:
        return None

    def _load_workflow() -> Any:
        async def _runner() -> Any:
            engine = workflow_plugin._get_engine()
            return await engine.get_workflow(workflow_id)

        try:
            asyncio.get_running_loop()
            in_running_loop = True
        except RuntimeError:
            in_running_loop = False

        if not in_running_loop:
            try:
                return asyncio.run(_runner())
            except Exception:
                return None

        holder: dict[str, Any] = {"value": None, "error": None}

        def _thread_target() -> None:
            try:
                holder["value"] = asyncio.run(_runner())
            except Exception as inner_exc:
                holder["error"] = inner_exc

        worker = threading.Thread(target=_thread_target, daemon=True)
        worker.start()
        worker.join(timeout=10)
        if worker.is_alive() or holder["error"] is not None:
            return None
        return holder["value"]

    workflow = _load_workflow()
    if not workflow:
        return None

    state_obj = getattr(workflow, "state", None)
    state = str(getattr(state_obj, "value", state_obj or "")).strip().lower()
    if state in {"completed", "failed", "cancelled"}:
        return None

    for step in list(getattr(workflow, "steps", []) or []):
        status_obj = getattr(step, "status", None)
        status = str(getattr(status_obj, "value", status_obj or "")).strip().lower()
        if status != "running":
            continue

        agent = getattr(step, "agent", None)
        name = str(getattr(agent, "name", "") or "").strip()
        display_name = str(getattr(agent, "display_name", "") or "").strip()
        if name:
            return name
        if display_name:
            return display_name
    return None
