"""n8n-facing state machine bridge for controlled coding execution."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

_VALID_STATES = {
    "queued",
    "reasoning",
    "planning",
    "approved",
    "coding",
    "review",
    "test",
    "pr",
    "done",
    "blocked",
    "failed",
    "cancelled",
}
_TERMINAL_STATES = {"done", "blocked", "failed", "cancelled"}
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
_STATE_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")


def create_run(payload: dict[str, Any]) -> dict[str, Any]:
    """Create a durable n8n workflow run record."""
    task = _required_str(payload, "task")
    run_id = _clean_id(payload.get("run_id")) or f"n8n-{uuid.uuid4().hex[:12]}"
    initial_state = _normalize_state(payload.get("state") or "queued")

    now = _now()
    record = {
        "run_id": run_id,
        "state": initial_state,
        "task": task,
        "project_key": _optional_str(payload.get("project_key")),
        "issue_number": _optional_str(payload.get("issue_number")),
        "workflow_id": _optional_str(payload.get("workflow_id")),
        "requester": (
            dict(payload.get("requester") or {})
            if isinstance(payload.get("requester"), dict)
            else {}
        ),
        "metadata": (
            dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}
        ),
        "created_at": now,
        "updated_at": now,
        "events": [
            {
                "at": now,
                "state": initial_state,
                "event": "run_created",
                "message": "n8n workflow run created",
            }
        ],
        "coding_jobs": [],
    }
    _save_run(record)
    return {"ok": True, "run": _public_run(record)}


def update_run(payload: dict[str, Any]) -> dict[str, Any]:
    """Update state/metadata for an existing n8n workflow run."""
    run_id = _required_str(payload, "run_id")
    record = _load_run(run_id)
    state = payload.get("state")
    if state is not None:
        next_state = _normalize_state(state)
        current_state = str(record.get("state") or "")
        if current_state in _TERMINAL_STATES and next_state != current_state:
            raise ValueError(f"run {run_id} is terminal ({current_state})")
        record["state"] = next_state

    if isinstance(payload.get("metadata"), dict):
        metadata = dict(record.get("metadata") or {})
        metadata.update(dict(payload["metadata"]))
        record["metadata"] = metadata

    message = _optional_str(payload.get("message"))
    now = _now()
    record["updated_at"] = now
    record.setdefault("events", []).append(
        {
            "at": now,
            "state": str(record.get("state") or ""),
            "event": str(payload.get("event") or "state_updated"),
            "message": message,
        }
    )
    _save_run(record)
    return {"ok": True, "run": _public_run(record)}


def get_run(run_id: str) -> dict[str, Any]:
    """Return a single n8n workflow run record."""
    return {"ok": True, "run": _public_run(_load_run(run_id))}


def execute_coding_task(payload: dict[str, Any]) -> dict[str, Any]:
    """Launch an allowlisted OpenCode coding job for an n8n workflow run."""
    run_id = _required_str(payload, "run_id")
    task = _required_str(payload, "task")
    record = _load_run(run_id)
    if str(record.get("state") or "") in _TERMINAL_STATES:
        raise ValueError(f"run {run_id} is terminal ({record.get('state')})")

    worker = str(payload.get("worker") or "opencode").strip().lower()
    if worker != "opencode":
        raise ValueError("only the 'opencode' worker is supported by this bridge")

    repo_dir = _resolve_repo_dir(payload, record)
    base_branch = _safe_branch(str(payload.get("base_branch") or "develop").strip() or "develop")
    branch = _safe_branch(str(payload.get("branch") or f"n8n/{run_id}").strip() or f"n8n/{run_id}")
    worktree_dir = _worktree_root() / run_id / repo_dir.name
    log_file = _logs_root() / f"{run_id}-{int(time.time())}-opencode.log"
    dry_run = _bool(payload.get("dry_run"))
    agent = str(payload.get("agent") or "build").strip() or "build"
    model = _optional_str(payload.get("model"))

    if not dry_run:
        _prepare_worktree(
            repo_dir=repo_dir, worktree_dir=worktree_dir, branch=branch, base_branch=base_branch
        )

    prompt = _coding_prompt(
        task=task,
        run_id=run_id,
        project_key=str(record.get("project_key") or ""),
        issue_number=str(record.get("issue_number") or ""),
    )
    cmd = _opencode_command(
        prompt=prompt,
        worktree_dir=worktree_dir,
        agent=agent,
        model=model,
    )

    job = {
        "job_id": f"job-{uuid.uuid4().hex[:12]}",
        "worker": worker,
        "state": "dry_run" if dry_run else "running",
        "pid": None,
        "repo_dir": str(repo_dir),
        "worktree_dir": str(worktree_dir),
        "branch": branch,
        "base_branch": base_branch,
        "log_file": str(log_file),
        "created_at": _now(),
    }

    if not dry_run:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_file.open("ab")
        proc = subprocess.Popen(
            cmd,
            cwd=str(worktree_dir),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        log_handle.close()
        job["pid"] = int(proc.pid)

    record.setdefault("coding_jobs", []).append(job)
    record["state"] = "coding"
    record["updated_at"] = _now()
    record.setdefault("events", []).append(
        {
            "at": record["updated_at"],
            "state": "coding",
            "event": "coding_job_started" if not dry_run else "coding_job_dry_run",
            "message": f"OpenCode job {job['job_id']} prepared",
        }
    )
    _save_run(record)

    return {
        "ok": True,
        "run_id": run_id,
        "job": _public_job(job),
        "dry_run": dry_run,
        "command_preview": _command_preview(cmd),
    }


def _state_dir() -> Path:
    return Path(os.getenv("NEXUS_N8N_STATE_DIR", "/var/lib/nexus/n8n-state")).expanduser()


def _worktree_root() -> Path:
    return Path(os.getenv("NEXUS_N8N_WORKTREE_DIR", "/var/lib/nexus/n8n-worktrees")).expanduser()


def _logs_root() -> Path:
    return Path(os.getenv("NEXUS_N8N_LOG_DIR", "/var/lib/nexus/logs/n8n")).expanduser()


def _run_path(run_id: str) -> Path:
    clean = _clean_id(run_id)
    if not clean:
        raise ValueError("run_id is required")
    return _state_dir() / f"{clean}.json"


def _load_run(run_id: str) -> dict[str, Any]:
    path = _run_path(run_id)
    if not path.exists():
        raise ValueError(f"run not found: {run_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _save_run(record: dict[str, Any]) -> None:
    path = _run_path(str(record.get("run_id") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _resolve_repo_dir(payload: dict[str, Any], record: dict[str, Any]) -> Path:
    explicit = _optional_str(payload.get("repo_dir") or payload.get("repo_path"))
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
    else:
        project_key = _optional_str(payload.get("project_key")) or _optional_str(
            record.get("project_key")
        )
        if not project_key:
            raise ValueError("project_key or repo_dir is required")
        candidate = _repo_dir_for_project(project_key)

    if not (candidate / ".git").exists():
        raise ValueError(f"repo_dir is not a git repository: {candidate}")

    allowed_roots = _allowed_repo_roots()
    if not any(_is_relative_to(candidate, root) for root in allowed_roots):
        raise ValueError(f"repo_dir is not allowlisted: {candidate}")
    return candidate


def _repo_dir_for_project(project_key: str) -> Path:
    from nexus.core.config import PROJECT_CONFIG

    cfg = PROJECT_CONFIG.get(project_key)
    if not isinstance(cfg, dict):
        raise ValueError(f"unknown project_key: {project_key}")
    workspace = str(cfg.get("workspace") or "").strip()
    if not workspace:
        raise ValueError(f"project has no workspace configured: {project_key}")
    base_dir = Path(os.getenv("BASE_DIR", "/home/ubuntu/git")).expanduser()
    return (base_dir / workspace).resolve()


def _allowed_repo_roots() -> list[Path]:
    raw = os.getenv("NEXUS_N8N_REPO_ALLOWLIST", "/home/ubuntu/git")
    roots = [Path(item).expanduser().resolve() for item in raw.split(":") if item.strip()]
    return roots or [Path("/home/ubuntu/git").resolve()]


def _prepare_worktree(*, repo_dir: Path, worktree_dir: Path, branch: str, base_branch: str) -> None:
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(repo_dir), "fetch", "origin", base_branch], check=True)
    if worktree_dir.exists():
        return
    subprocess.run(
        [
            "git",
            "-C",
            str(repo_dir),
            "worktree",
            "add",
            "-B",
            branch,
            str(worktree_dir),
            f"origin/{base_branch}",
        ],
        check=True,
    )


def _opencode_command(
    *,
    prompt: str,
    worktree_dir: Path,
    agent: str,
    model: str | None,
) -> list[str]:
    opencode = os.getenv("NEXUS_OPENCODE_CLI", shutil.which("opencode") or "opencode")
    cmd = [
        opencode,
        "run",
        "--agent",
        agent,
        "--format",
        "json",
        "--dir",
        str(worktree_dir),
        "--title",
        "n8n coding task",
        "--dangerously-skip-permissions",
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt)
    return cmd


def _coding_prompt(*, task: str, run_id: str, project_key: str, issue_number: str) -> str:
    context = [f"n8n workflow run: {run_id}"]
    if project_key:
        context.append(f"project: {project_key}")
    if issue_number:
        context.append(f"issue: {issue_number}")
    return (
        "You are Atlas, the OpenCode coding worker for a Nexus-controlled n8n state machine.\n"
        "Work only inside the current git worktree. Make the smallest complete change for the task.\n"
        "Run relevant tests or checks when possible. Do not push, merge, or create external PRs.\n\n"
        + "\n".join(context)
        + "\n\nTask:\n"
        + task
    )


def _public_run(record: dict[str, Any]) -> dict[str, Any]:
    result = dict(record)
    result["coding_jobs"] = [
        _public_job(job) for job in result.get("coding_jobs", []) if isinstance(job, dict)
    ]
    return result


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    result = dict(job)
    pid = result.get("pid")
    if isinstance(pid, int) and pid > 0:
        result["running"] = _pid_running(pid)
    return result


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _command_preview(cmd: list[str]) -> list[str]:
    if not cmd:
        return []
    preview = list(cmd)
    if len(preview) > 1:
        preview[-1] = "<prompt>"
    return preview


def _normalize_state(value: Any) -> str:
    state = str(value or "").strip().lower().replace("-", "_")
    if state not in _VALID_STATES and not _STATE_RE.match(state):
        raise ValueError(f"invalid run state: {value}")
    return state


def _safe_branch(value: str) -> str:
    branch = value.strip().strip("/")
    if not branch or ".." in branch or branch.startswith("-") or not _BRANCH_RE.match(branch):
        raise ValueError(f"invalid branch name: {value}")
    return branch


def _clean_id(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", str(value or "").strip())[:80]


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
