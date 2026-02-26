import asyncio
import contextlib
import glob
import logging
import os
import re
import threading
import time
from functools import partial
from types import SimpleNamespace
from typing import Any

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from alerting import init_alerting_system
from analytics import get_stats_report
from audit_store import AuditStore
from commands.workflow import (
    pause_handler as workflow_pause_handler,
)
from commands.workflow import (
    resume_handler as workflow_resume_handler,
)
from commands.workflow import (
    stop_handler as workflow_stop_handler,
)

# Import configuration from centralized config module
from config import (
    AI_PERSONA,
    BASE_DIR,
    LOGS_DIR,
    NEXUS_CORE_STORAGE_DIR,
    NEXUS_STORAGE_DSN,
    NEXUS_WORKFLOW_BACKEND,
    ORCHESTRATOR_CONFIG,
    PROJECT_CONFIG,
    TELEGRAM_ALLOWED_USER_IDS,
    TELEGRAM_BOT_LOG_FILE,
    TELEGRAM_CHAT_ID,
    TELEGRAM_TOKEN,
    get_default_repo,
    get_repo,
    get_repos,
    get_inbox_storage_backend,
    get_inbox_dir,
    get_nexus_dir_name,
    get_tasks_active_dir,
    get_tasks_closed_dir,
    get_tasks_logs_dir,
    get_track_short_projects,
)
from error_handling import format_error_for_user
from handlers.audio_transcription_handler import (
    AudioTranscriptionDeps,
    transcribe_telegram_voice,
)
from handlers.bug_report_handler import handle_report_bug
from handlers.callback_command_handlers import (
    CallbackHandlerDeps,
)
from handlers.callback_command_handlers import (
    close_flow_handler as callback_close_flow_handler,
)
from handlers.callback_command_handlers import (
    flow_close_handler as callback_flow_close_handler,
)
from handlers.callback_command_handlers import (
    inline_keyboard_handler as callback_inline_keyboard_handler,
)
from handlers.callback_command_handlers import (
    issue_picker_handler as callback_issue_picker_handler,
)
from handlers.callback_command_handlers import (
    menu_callback_handler as callback_menu_callback_handler,
)
from handlers.callback_command_handlers import (
    monitor_project_picker_handler as callback_monitor_project_picker_handler,
)
from handlers.callback_command_handlers import (
    project_picker_handler as callback_project_picker_handler,
)
from handlers.chat_command_handlers import (
    chat_agents_handler as core_chat_agents_handler,
    chat_callback_handler as core_chat_callback_handler,
    chat_menu_handler as core_chat_menu_handler,
)
from handlers.common_routing import extract_json_dict, route_task_with_context
from handlers.feature_ideation_handlers import (
    FeatureIdeationHandlerDeps,
    handle_feature_ideation_request,
    is_feature_ideation_request,
)
from handlers.feature_ideation_handlers import (
    feature_callback_handler as core_feature_callback_handler,
)
from handlers.hands_free_routing_handler import (
    HandsFreeRoutingDeps,
    resolve_pending_project_selection,
    route_hands_free_text,
)
from handlers.inbox_routing_handler import (
    PROJECTS,
    TYPES,
    process_inbox_task,
    save_resolved_task,
)
from handlers.issue_command_handlers import (
    IssueHandlerDeps,
)
from handlers.issue_command_handlers import (
    assign_handler as issue_assign_handler,
)
from handlers.issue_command_handlers import (
    comments_handler as issue_comments_handler,
)
from handlers.issue_command_handlers import (
    implement_handler as issue_implement_handler,
)
from handlers.issue_command_handlers import (
    myissues_handler as issue_myissues_handler,
)
from handlers.issue_command_handlers import (
    prepare_handler as issue_prepare_handler,
)
from handlers.issue_command_handlers import (
    respond_handler as issue_respond_handler,
)
from handlers.issue_command_handlers import (
    track_handler as issue_track_handler,
)
from handlers.issue_command_handlers import (
    tracked_handler as issue_tracked_handler,
)
from handlers.issue_command_handlers import (
    untrack_handler as issue_untrack_handler,
)
from handlers.monitoring_command_handlers import (
    MonitoringHandlersDeps,
)
from handlers.monitoring_command_handlers import (
    active_handler as monitoring_active_handler,
)
from handlers.monitoring_command_handlers import (
    fuse_handler as monitoring_fuse_handler,
)
from handlers.monitoring_command_handlers import (
    logs_handler as monitoring_logs_handler,
)
from handlers.monitoring_command_handlers import (
    logsfull_handler as monitoring_logsfull_handler,
)
from handlers.monitoring_command_handlers import (
    status_handler as monitoring_status_handler,
)
from handlers.monitoring_command_handlers import (
    tail_handler as monitoring_tail_handler,
)
from handlers.monitoring_command_handlers import (
    tailstop_handler as monitoring_tailstop_handler,
)
from handlers.ops_command_handlers import (
    OpsHandlerDeps,
)
from handlers.ops_command_handlers import (
    agents_handler as ops_agents_handler,
)
from handlers.ops_command_handlers import (
    audit_handler as ops_audit_handler,
)
from handlers.ops_command_handlers import (
    direct_handler as ops_direct_handler,
)
from handlers.ops_command_handlers import (
    inboxq_handler as ops_inboxq_handler,
)
from handlers.ops_command_handlers import (
    stats_handler as ops_stats_handler,
)
from handlers.visualize_command_handlers import (
    VisualizeHandlerDeps,
)
from handlers.visualize_command_handlers import (
    visualize_handler as workflow_visualize_handler,
)
from handlers.workflow_command_handlers import (
    WorkflowHandlerDeps,
)
from handlers.workflow_command_handlers import (
    continue_handler as workflow_continue_handler,
)
from handlers.workflow_command_handlers import (
    forget_handler as workflow_forget_handler,
)
from handlers.workflow_command_handlers import (
    kill_handler as workflow_kill_handler,
)
from handlers.workflow_command_handlers import (
    pause_handler as workflow_pause_picker_handler,
)
from handlers.workflow_command_handlers import (
    reconcile_handler as workflow_reconcile_handler,
)
from handlers.workflow_command_handlers import (
    reprocess_handler as workflow_reprocess_handler,
)
from handlers.workflow_command_handlers import (
    resume_handler as workflow_resume_picker_handler,
)
from handlers.workflow_command_handlers import (
    stop_handler as workflow_stop_picker_handler,
)
from handlers.workflow_command_handlers import (
    wfstate_handler as workflow_wfstate_handler,
)
from inbox_processor import _normalize_agent_reference, get_sop_tier
from nexus.adapters.git.utils import build_issue_url, resolve_repo
from nexus.core.completion import scan_for_completions
from nexus.core.utils.logging_filters import install_secret_redaction
from nexus.plugins.builtin.ai_runtime_plugin import AIProvider
from orchestration.ai_orchestrator import get_orchestrator
from orchestration.plugin_runtime import (
    get_profiled_plugin,
    get_runtime_ops_plugin,
    get_workflow_state_plugin,
)
from project_key_utils import normalize_project_key_optional as _normalize_project_key
from rate_limiter import RateLimit, get_rate_limiter
from report_scheduler import ReportScheduler
from runtime.agent_launcher import get_sop_tier_from_issue, invoke_copilot_agent
from services.command_contract import (
    validate_command_parity,
    validate_required_command_interface,
)
from services.memory_service import (
    append_message,
    create_chat,
    get_active_chat,
    get_chat,
    get_chat_history,
    rename_chat,
)
from services.workflow_control_service import (
    kill_issue_agent,
    prepare_continue_context,
)
from services.workflow_ops_service import (
    build_workflow_snapshot,
    fetch_workflow_state_snapshot,
    reconcile_issue_from_signals,
)
from services.workflow_signal_sync import (
    extract_structured_completion_signals,
    read_latest_local_completion,
    write_local_completion_from_signal,
)
from state_manager import HostStateManager
from user_manager import get_user_manager
from utils.task_utils import find_task_file_by_issue

# --- LOGGING ---
logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    force=True,
    handlers=[logging.StreamHandler(), logging.FileHandler(TELEGRAM_BOT_LOG_FILE)],
)


install_secret_redaction([TELEGRAM_TOKEN or ""], logging.getLogger())

# Long-polling calls Telegram getUpdates repeatedly by design.
# Keep these transport logs at WARNING to avoid noisy INFO output.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Initialize AI Orchestrator (CLI-only: gemini-cli + copilot-cli)
orchestrator = get_orchestrator(ORCHESTRATOR_CONFIG)

# Initialize rate limiter
rate_limiter = get_rate_limiter()

# Initialize user manager
user_manager = get_user_manager()

DEFAULT_REPO = get_default_repo()
from integrations.workflow_state_factory import get_workflow_state as _get_wf_state

_WORKFLOW_STATE_PLUGIN_KWARGS = {
    "storage_dir": NEXUS_CORE_STORAGE_DIR,
    "storage_type": "postgres" if NEXUS_WORKFLOW_BACKEND == "postgres" else "file",
    "storage_config": (
        {"connection_string": NEXUS_STORAGE_DSN}
        if NEXUS_WORKFLOW_BACKEND == "postgres" and NEXUS_STORAGE_DSN
        else {}
    ),
    "issue_to_workflow_id": lambda n: _get_wf_state().get_workflow_id(n),
    "clear_pending_approval": lambda n: _get_wf_state().clear_pending_approval(n),
    "audit_log": AuditStore.audit_log,
}


def _workflow_handler_deps() -> WorkflowHandlerDeps:
    return WorkflowHandlerDeps(
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        base_dir=BASE_DIR,
        default_repo=DEFAULT_REPO,
        project_config=PROJECT_CONFIG,
        workflow_state_plugin_kwargs=_WORKFLOW_STATE_PLUGIN_KWARGS,
        prompt_project_selection=_ctx_prompt_project_selection,
        ensure_project_issue=_ctx_ensure_project_issue,
        find_task_file_by_issue=find_task_file_by_issue,
        project_repo=_project_repo,
        get_issue_details=get_issue_details,
        resolve_project_config_from_task=resolve_project_config_from_task,
        invoke_copilot_agent=invoke_copilot_agent,
        get_sop_tier_from_issue=get_sop_tier_from_issue,
        get_sop_tier=get_sop_tier,
        get_last_tier_for_issue=HostStateManager.get_last_tier_for_issue,
        prepare_continue_context=prepare_continue_context,
        kill_issue_agent=kill_issue_agent,
        get_runtime_ops_plugin=get_runtime_ops_plugin,
        get_workflow_state_plugin=get_workflow_state_plugin,
        fetch_workflow_state_snapshot=fetch_workflow_state_snapshot,
        scan_for_completions=scan_for_completions,
        normalize_agent_reference=_normalize_agent_reference,
        get_expected_running_agent_from_workflow=_get_expected_running_agent_from_workflow,
        reconcile_issue_from_signals=reconcile_issue_from_signals,
        get_direct_issue_plugin=_get_direct_issue_plugin,
        extract_structured_completion_signals=_extract_structured_completion_signals,
        write_local_completion_from_signal=_write_local_completion_from_signal,
        build_workflow_snapshot=build_workflow_snapshot,
        read_latest_local_completion=_read_latest_local_completion,
        workflow_pause_handler=workflow_pause_handler,
        workflow_resume_handler=workflow_resume_handler,
        workflow_stop_handler=workflow_stop_handler,
    )


def _visualize_handler_deps() -> VisualizeHandlerDeps:
    return VisualizeHandlerDeps(
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        prompt_project_selection=_ctx_prompt_project_selection,
        ensure_project_issue=_ctx_ensure_project_issue,
    )


def _monitoring_handler_deps() -> MonitoringHandlersDeps:
    from runtime.nexus_agent_runtime import get_retry_fuse_status

    return MonitoringHandlersDeps(
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        base_dir=BASE_DIR,
        project_config=PROJECT_CONFIG,
        types_map=TYPES,
        ensure_project=_ctx_ensure_project,
        ensure_project_issue=_ctx_ensure_project_issue,
        normalize_project_key=_normalize_project_key,
        iter_project_keys=_iter_project_keys,
        get_project_label=_get_project_label,
        get_project_root=_get_project_root,
        get_project_logs_dir=_get_project_logs_dir,
        project_repo=_project_repo,
        get_issue_details=get_issue_details,
        get_inbox_dir=get_inbox_dir,
        get_tasks_active_dir=get_tasks_active_dir,
        get_tasks_closed_dir=get_tasks_closed_dir,
        extract_issue_number_from_file=extract_issue_number_from_file,
        build_issue_url=build_issue_url,
        find_task_file_by_issue=find_task_file_by_issue,
        find_issue_log_files=find_issue_log_files,
        read_latest_log_tail=read_latest_log_tail,
        search_logs_for_issue=search_logs_for_issue,
        read_latest_log_full=read_latest_log_full,
        read_log_matches=read_log_matches,
        active_tail_sessions=active_tail_sessions,
        active_tail_tasks=active_tail_tasks,
        get_retry_fuse_status=get_retry_fuse_status,
        normalize_agent_reference=_normalize_agent_reference,
        get_expected_running_agent_from_workflow=_get_expected_running_agent_from_workflow,
        get_direct_issue_plugin=_get_direct_issue_plugin,
        extract_structured_completion_signals=_extract_structured_completion_signals,
        read_latest_local_completion=_read_latest_local_completion,
        build_workflow_snapshot=build_workflow_snapshot,
    )


def _issue_handler_deps() -> IssueHandlerDeps:
    return IssueHandlerDeps(
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        base_dir=BASE_DIR,
        default_repo=DEFAULT_REPO,
        prompt_project_selection=_ctx_prompt_project_selection,
        ensure_project_issue=_ctx_ensure_project_issue,
        project_repo=_project_repo,
        project_issue_url=_project_issue_url,
        get_issue_details=get_issue_details,
        get_direct_issue_plugin=_get_direct_issue_plugin,
        resolve_project_config_from_task=resolve_project_config_from_task,
        invoke_copilot_agent=invoke_copilot_agent,
        get_sop_tier=get_sop_tier,
        find_task_file_by_issue=find_task_file_by_issue,
        resolve_repo=resolve_repo,
        build_issue_url=build_issue_url,
        user_manager=user_manager,
        save_tracked_issues=save_tracked_issues,
        tracked_issues_ref=tracked_issues,
        default_issue_url=_default_issue_url,
        get_project_label=_get_project_label,
        track_short_projects=get_track_short_projects(),
    )


def _get_inbox_queue_overview(limit: int) -> dict[str, Any]:
    from integrations.inbox_queue import get_queue_overview

    return get_queue_overview(limit=limit)


def _ops_handler_deps() -> OpsHandlerDeps:
    return OpsHandlerDeps(
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        base_dir=BASE_DIR,
        nexus_dir_name=get_nexus_dir_name(),
        project_config=PROJECT_CONFIG,
        prompt_project_selection=_ctx_prompt_project_selection,
        ensure_project_issue=_ctx_ensure_project_issue,
        get_project_label=_get_project_label,
        get_stats_report=get_stats_report,
        get_inbox_storage_backend=get_inbox_storage_backend,
        get_inbox_queue_overview=_get_inbox_queue_overview,
        format_error_for_user=format_error_for_user,
        get_audit_history=AuditStore.get_audit_history,
        get_repo=get_repo,
        get_direct_issue_plugin=_get_direct_issue_plugin,
        orchestrator=orchestrator,
        ai_persona=AI_PERSONA,
        get_chat_history=get_chat_history,
        append_message=append_message,
        create_chat=create_chat,
    )


def _callback_handler_deps() -> CallbackHandlerDeps:
    return CallbackHandlerDeps(
        logger=logger,
        prompt_issue_selection=_ctx_prompt_issue_selection,
        dispatch_command=_ctx_dispatch_command,
        get_project_label=_get_project_label,
        get_repo=get_repo,
        get_direct_issue_plugin=_get_direct_issue_plugin,
        get_workflow_state_plugin=get_workflow_state_plugin,
        workflow_state_plugin_kwargs=_WORKFLOW_STATE_PLUGIN_KWARGS,
        action_handlers={
            "logs": partial(_ctx_call_telegram_handler, handler=logs_handler),
            "logsfull": partial(_ctx_call_telegram_handler, handler=logsfull_handler),
            "status": partial(_ctx_call_telegram_handler, handler=status_handler),
            "pause": partial(_ctx_call_telegram_handler, handler=pause_handler),
            "resume": partial(_ctx_call_telegram_handler, handler=resume_handler),
            "stop": partial(_ctx_call_telegram_handler, handler=stop_handler),
            "audit": partial(_ctx_call_telegram_handler, handler=audit_handler),
            "active": partial(_ctx_call_telegram_handler, handler=active_handler),
            "reprocess": partial(_ctx_call_telegram_handler, handler=reprocess_handler),
        },
        report_bug_action=_report_bug_action_wrapper,
    )


def _feature_ideation_handler_deps() -> FeatureIdeationHandlerDeps:
    async def _create_feature_task(text: str, message_id: str, project_key: str) -> dict[str, Any]:
        return await process_inbox_task(
            text,
            orchestrator,
            message_id,
            project_hint=project_key,
        )

    return FeatureIdeationHandlerDeps(
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        projects=PROJECTS,
        get_project_label=_get_project_label,
        orchestrator=orchestrator,
        base_dir=BASE_DIR,
        project_config=PROJECT_CONFIG,
        create_feature_task=_create_feature_task,
    )


def _audio_transcription_handler_deps() -> AudioTranscriptionDeps:
    return AudioTranscriptionDeps(
        logger=logger,
        transcribe_audio=orchestrator.transcribe_audio,
    )


def _hands_free_routing_handler_deps() -> HandsFreeRoutingDeps:
    return HandsFreeRoutingDeps(
        logger=logger,
        orchestrator=orchestrator,
        ai_persona=AI_PERSONA,
        projects=PROJECTS,
        extract_json_dict=extract_json_dict,
        get_chat_history=get_chat_history,
        append_message=append_message,
        get_chat=get_chat,
        process_inbox_task=process_inbox_task,
        feature_ideation_deps=_feature_ideation_handler_deps(),
        normalize_project_key=_normalize_project_key,
        save_resolved_task=save_resolved_task,
        task_confirmation_mode=TASK_CONFIRMATION_MODE,
    )


def _get_direct_issue_plugin(repo: str):
    """Return issue plugin for direct Telegram operations."""
    return get_profiled_plugin(
        "git_telegram",
        overrides={
            "repo": repo,
        },
        cache_key=f"git:telegram:{repo}",
    )


# --- RATE LIMITING DECORATOR ---
def rate_limited(action: str, limit: RateLimit = None):
    """
    Decorator to add rate limiting to Telegram command handlers.

    Args:
        action: Rate limit action name (e.g., "logs", "stats", "implement")
        limit: Optional custom rate limit (uses default if not provided)

    Usage:
        @rate_limited("logs")
        async def logs_handler(update, context):
            ...
    """

    def decorator(func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user_id = update.effective_user.id

            # Check rate limit
            allowed, error_msg = rate_limiter.check_limit(user_id, action, limit)

            if not allowed:
                # Rate limit exceeded
                await update.message.reply_text(error_msg)
                logger.warning(f"Rate limit blocked: user={user_id}, action={action}")
                return

            # Record the request
            rate_limiter.record_request(user_id, action)

            # Call the actual handler
            return await func(update, context)

        return wrapper

    return decorator


def load_tracked_issues():
    """Load tracked issues from file."""
    return HostStateManager.load_tracked_issues()


def save_tracked_issues(data):
    """Save tracked issues to file."""
    HostStateManager.save_tracked_issues(data)


# Moved `_refine_task_description` to inbox_routing_handler.py


def get_issue_details(issue_num, repo: str = None):
    """Query GitHub API for issue details."""
    try:
        repo = repo or DEFAULT_REPO
        plugin = _get_direct_issue_plugin(repo)
        if not plugin:
            return None
        return plugin.get_issue(
            str(issue_num),
            ["number", "title", "state", "labels", "body", "updatedAt"],
        )
    except Exception as e:
        logger.error(f"Failed to fetch issue {issue_num}: {e}")
        return None


def _get_expected_running_agent_from_workflow(issue_num: str) -> str | None:
    """Return the current RUNNING workflow agent for an issue, if available."""
    workflow_id = _get_wf_state().get_workflow_id(str(issue_num))
    if not workflow_id:
        return None

    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_KWARGS,
        cache_key="workflow:state-engine:expected-agent:telegram",
    )

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
            if worker.is_alive():
                return None
            if holder["error"] is not None:
                return None
            return holder["value"]

        holder: dict[str, Any] = {"value": None, "error": None}

        def _thread_target() -> None:
            try:
                holder["value"] = asyncio.run(_runner())
            except Exception as inner_exc:
                holder["error"] = inner_exc

        worker = threading.Thread(target=_thread_target, daemon=True)
        worker.start()
        worker.join(timeout=10)
        if worker.is_alive():
            return None
        if holder["error"] is not None:
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


def _extract_structured_completion_signals(comments: list[dict]) -> list[dict[str, str]]:
    return extract_structured_completion_signals(comments)


def _write_local_completion_from_signal(
    project_key: str, issue_num: str, signal: dict[str, str]
) -> str:
    return write_local_completion_from_signal(
        BASE_DIR,
        get_nexus_dir_name(),
        project_key,
        issue_num,
        signal,
        key_findings=[
            "Workflow/comment/local completion drift reconciled via /reconcile",
            f"Source comment id: {signal.get('comment_id', 'n/a')}",
        ],
    )


def _read_latest_local_completion(issue_num: str) -> dict[str, Any] | None:
    return read_latest_local_completion(BASE_DIR, get_nexus_dir_name(), issue_num)


def _resolve_project_root_from_task_path(task_file: str) -> str:
    """Resolve project/worktree root for a task path under `.nexus/tasks/...`."""
    normalized = os.path.abspath(task_file).replace("\\", "/")
    match = re.search(r"^(.*)/\.nexus/tasks/[^/]+/", normalized)
    if match:
        return os.path.normpath(match.group(1))
    if "/.nexus/" in normalized:
        return os.path.normpath(normalized.split("/.nexus/", 1)[0])
    return os.path.dirname(os.path.dirname(os.path.dirname(normalized)))


def find_task_logs(task_file):
    """Find task log files for the task file's project."""
    if not task_file:
        return []

    try:
        project_root = _resolve_project_root_from_task_path(task_file)

        project_key = _extract_project_from_nexus_path(task_file)
        if not project_key:
            return []
        logs_dir = get_tasks_logs_dir(project_root, project_key)
        if not os.path.isdir(logs_dir):
            return []

        pattern = os.path.join(logs_dir, "**", "*.log")
        return glob.glob(pattern, recursive=True)
    except Exception as e:
        logger.warning(f"Failed to list task logs: {e}")
        return []


def read_log_matches(log_path, issue_num, issue_url=None, max_lines=20):
    """Return lines from a log file that reference an issue."""
    if not log_path or not os.path.exists(log_path):
        return []

    matches = []
    needle = f"#{issue_num}"
    try:
        with open(log_path) as f:
            for line in f:
                if needle in line or (issue_url and issue_url in line):
                    matches.append(line.rstrip())
    except Exception as e:
        logger.warning(f"Failed to read log file {log_path}: {e}")
        return []

    return matches[-max_lines:] if max_lines else matches


def search_logs_for_issue(issue_num):
    """Search bot/processor logs for an issue number."""
    log_paths = []
    if TELEGRAM_BOT_LOG_FILE:
        log_paths.append(TELEGRAM_BOT_LOG_FILE)
    if LOGS_DIR and os.path.isdir(LOGS_DIR):
        log_paths.extend(
            os.path.join(LOGS_DIR, f) for f in os.listdir(LOGS_DIR) if f.endswith(".log")
        )

    seen = set()
    results = []
    for path in log_paths:
        if path in seen:
            continue
        seen.add(path)
        results.extend(read_log_matches(path, issue_num, max_lines=10))
    return results


def read_latest_log_tail(task_file, max_lines=20):
    """Return tail of the newest task log file, if present."""
    log_files = find_task_logs(task_file)
    if not log_files:
        return []
    log_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    latest = log_files[0]
    try:
        with open(latest) as f:
            lines = f.readlines()
        return [f"[{os.path.basename(latest)}] {line.rstrip()}" for line in lines[-max_lines:]]
    except Exception as e:
        logger.warning(f"Failed to read latest log file {latest}: {e}")
        return []


def find_issue_log_files(issue_num, task_file=None):
    """Find task log files that match the issue number."""
    matches = []

    # If task file is known, search its project logs dir first
    if task_file:
        project_root = _resolve_project_root_from_task_path(task_file)
        project_key = _extract_project_from_nexus_path(task_file)
        if project_key:
            logs_dir = get_tasks_logs_dir(project_root, project_key)
            if os.path.isdir(logs_dir):
                pattern = os.path.join(logs_dir, "**", f"*_{issue_num}_*.log")
                matches.extend(glob.glob(pattern, recursive=True))

    if matches:
        return matches

    # Fallback: scan all logs dirs
    nexus_dir_name = get_nexus_dir_name()
    pattern = os.path.join(
        BASE_DIR, "**", nexus_dir_name, "tasks", "*", "logs", "**", f"*_{issue_num}_*.log"
    )
    matches.extend(glob.glob(pattern, recursive=True))

    worktree_pattern = os.path.join(
        BASE_DIR,
        "**",
        nexus_dir_name,
        "worktrees",
        "*",
        nexus_dir_name,
        "tasks",
        "*",
        "logs",
        "**",
        f"*_{issue_num}_*.log",
    )
    matches.extend(glob.glob(worktree_pattern, recursive=True))

    unique = []
    seen = set()
    for path in matches:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def read_latest_log_full(task_file):
    """Return full contents of the newest task log file, if present."""
    log_files = find_task_logs(task_file)
    if not log_files:
        return []
    log_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    latest = log_files[0]
    try:
        with open(latest) as f:
            lines = f.readlines()
        return [f"[{os.path.basename(latest)}] {line.rstrip()}" for line in lines]
    except Exception as e:
        logger.warning(f"Failed to read latest log file {latest}: {e}")
        return []


def resolve_project_config_from_task(task_file):
    """Resolve project config based on task file path."""
    if not task_file:
        return None, None

    task_path = os.path.abspath(task_file)

    # If task is inside a workspace repo (.nexus/...), derive project root
    if "/.nexus/" in task_path:
        project_root = task_path.split("/.nexus/")[0]
        # Match by configured workspace path instead of basename
        for key, cfg in PROJECT_CONFIG.items():
            if not isinstance(cfg, dict):
                continue
            workspace = cfg.get("workspace")
            if not workspace:
                continue
            workspace_abs = os.path.abspath(os.path.join(BASE_DIR, workspace))
            if project_root == workspace_abs or project_root.startswith(workspace_abs + os.sep):
                return key, cfg

    # If task is inside an agents repo, map by agents_dir
    for key, cfg in PROJECT_CONFIG.items():
        # Skip non-project config entries (global settings)
        if not isinstance(cfg, dict):
            continue

        agents_dir = cfg.get("agents_dir")
        if not agents_dir:
            continue
        agents_abs = os.path.abspath(os.path.join(BASE_DIR, agents_dir))
        if task_path.startswith(agents_abs + os.sep):
            return key, cfg

    return None, None


def _iter_project_keys() -> list[str]:
    keys = []
    for key, cfg in PROJECT_CONFIG.items():
        if not isinstance(cfg, dict):
            continue
        repo = cfg.get("git_repo")
        repo_list = cfg.get("git_repos")
        has_primary = isinstance(repo, str) and bool(repo.strip())
        has_multi = isinstance(repo_list, list) and any(
            isinstance(item, str) and item.strip() for item in repo_list
        )
        if has_primary or has_multi:
            keys.append(key)
    return keys


def _get_single_project_key() -> str | None:
    keys = _iter_project_keys()
    if len(keys) == 1:
        return keys[0]
    return None


def _get_project_label(project_key: str) -> str:
    return PROJECTS.get(project_key, project_key)


def _get_project_root(project_key: str) -> str | None:
    cfg = PROJECT_CONFIG.get(project_key)
    if not isinstance(cfg, dict):
        return None
    workspace = cfg.get("workspace")
    if not workspace:
        return None
    return os.path.join(BASE_DIR, workspace)


def _get_project_logs_dir(project_key: str) -> str | None:
    project_root = _get_project_root(project_key)
    if not project_root:
        return None
    logs_dir = get_tasks_logs_dir(project_root, project_key)
    return logs_dir if os.path.isdir(logs_dir) else None


def _project_repo(project_key: str) -> str:
    config = PROJECT_CONFIG.get(project_key)
    return resolve_repo(config if isinstance(config, dict) else None, DEFAULT_REPO)


def _project_issue_url(project_key: str, issue_num: str) -> str:
    config = PROJECT_CONFIG.get(project_key)
    cfg = config if isinstance(config, dict) else None
    return build_issue_url(_project_repo(project_key), issue_num, cfg)


def _default_issue_url(issue_num: str) -> str:
    try:
        project_key = get_default_project()
        return _project_issue_url(project_key, issue_num)
    except Exception:
        # This is strictly for the /link command, we should ideally resolve this from the repo platform
        return f"https://github.com/{DEFAULT_REPO}/issues/{issue_num}"


def _extract_project_from_nexus_path(path: str) -> str | None:
    if not path or "/.nexus/" not in path:
        return None

    normalized = path.replace("\\", "/")
    match = re.search(r"/\.nexus/(?:tasks|inbox)/([^/]+)/", normalized)
    if not match:
        return None

    project_key = _normalize_project_key(match.group(1))
    if project_key and project_key in _iter_project_keys():
        return project_key
    return None


async def _prompt_monitor_project_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    command: str,
) -> None:
    keyboard = [[InlineKeyboardButton("All Projects", callback_data=f"pickmonitor:{command}:all")]]
    keyboard.extend(
        [
            InlineKeyboardButton(
                _get_project_label(key), callback_data=f"pickmonitor:{command}:{key}"
            )
        ]
        for key in _iter_project_keys()
    )
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="flow:close")])

    text = f"Select a project for /{command}:"
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.effective_message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


def _list_project_issues(project_key: str, state: str = "open", limit: int = 10) -> list[dict]:
    """Fetch recent issues from a project's GitHub repo.

    Returns a list of dicts with 'number', 'title', and 'state' keys.
    """
    config = PROJECT_CONFIG.get(project_key, {})
    if not isinstance(config, dict):
        return []
    repo = config.get("git_repo")
    if (not isinstance(repo, str) or not repo.strip()) and isinstance(
        config.get("git_repos"), list
    ):
        repos = [r for r in config.get("git_repos", []) if isinstance(r, str) and r.strip()]
        repo = repos[0] if repos else None
    if not repo:
        repos = get_repos(project_key)
        repo = repos[0] if repos else None
    if not repo:
        return []
    try:
        plugin = _get_direct_issue_plugin(repo)
        if not plugin:
            return []
        return plugin.list_issues(state=state, limit=limit, fields=["number", "title", "state"])
    except Exception as e:
        logger.error(f"Failed to list issues for {project_key}: {e}")
        return []


async def _prompt_issue_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    command: str,
    project_key: str,
    *,
    edit_message: bool = False,
    issue_state: str = "open",
) -> None:
    """Show a list of issues for the user to pick from."""
    issues = _list_project_issues(project_key, state=issue_state)
    state_label = "open" if issue_state == "open" else "closed"

    if not issues:
        # No issues in current state — still offer toggle + manual entry
        keyboard = []
        if issue_state == "open":
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "📦 Closed issues",
                        callback_data=f"pickissue_state:closed:{command}:{project_key}",
                    )
                ]
            )
        else:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "🔓 Open issues",
                        callback_data=f"pickissue_state:open:{command}:{project_key}",
                    )
                ]
            )
        keyboard.append(
            [
                InlineKeyboardButton(
                    "✏️ Enter manually", callback_data=f"pickissue_manual:{command}:{project_key}"
                )
            ]
        )
        keyboard.append([InlineKeyboardButton("❌ Close", callback_data="flow:close")])

        text = f"No {state_label} issues found for {_get_project_label(project_key)}."
        if edit_message and update.callback_query:
            await update.callback_query.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.effective_message.reply_text(
                text, reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return

    keyboard = []
    for issue in issues:
        num = issue["number"]
        title = issue["title"]
        label = f"#{num} — {title}"
        if len(label) > 60:
            label = label[:57] + "..."
        keyboard.append(
            [InlineKeyboardButton(label, callback_data=f"pickissue:{command}:{project_key}:{num}")]
        )

    # Toggle button: show closed when viewing open, and vice versa
    if issue_state == "open":
        keyboard.append(
            [
                InlineKeyboardButton(
                    "📦 Closed issues",
                    callback_data=f"pickissue_state:closed:{command}:{project_key}",
                )
            ]
        )
    else:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "🔓 Open issues",
                    callback_data=f"pickissue_state:open:{command}:{project_key}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "✏️ Enter manually", callback_data=f"pickissue_manual:{command}:{project_key}"
            )
        ]
    )
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="flow:close")])

    emoji = "📋" if issue_state == "open" else "📦"
    text = f"{emoji} {state_label.capitalize()} issues for /{command} ({_get_project_label(project_key)}):"
    if edit_message and update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.effective_message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def _prompt_project_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE, command: str
) -> None:
    single_project = _get_single_project_key()
    if single_project:
        context.user_data["pending_command"] = command
        context.user_data["pending_project"] = single_project
        if command == "agents":
            await _dispatch_command(update, context, command, single_project, "")
            return
        await _prompt_issue_selection(update, context, command, single_project)
        return

    keyboard = [
        [InlineKeyboardButton(_get_project_label(key), callback_data=f"pickcmd:{command}:{key}")]
        for key in _iter_project_keys()
    ]
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="flow:close")])
    await update.effective_message.reply_text(
        f"Select a project for /{command}:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["pending_command"] = command


def _parse_project_issue_args(args: list[str]) -> tuple[str | None, str | None, list[str]]:
    sanitized_args: list[str] = []
    for token in args:
        value = str(token or "").strip()
        if not value:
            continue
        if all(ch in {"=", ">", "-", "→"} for ch in value):
            continue
        sanitized_args.append(value)

    if len(sanitized_args) < 2:
        return None, None, []
    project_key = _normalize_project_key(sanitized_args[0])
    issue_num = sanitized_args[1].lstrip("#")
    rest = sanitized_args[2:]
    return project_key, issue_num, rest


async def _ensure_project_issue(
    update: Update, context: ContextTypes.DEFAULT_TYPE, command: str
) -> tuple[str | None, str | None, list[str]]:
    project_keys = _iter_project_keys()
    single_project = project_keys[0] if len(project_keys) == 1 else None

    sanitized_args: list[str] = []
    for token in list(context.args or []):
        value = str(token or "").strip()
        if not value:
            continue
        if all(ch in {"=", ">", "-", "→"} for ch in value):
            continue
        sanitized_args.append(value)

    project_key, issue_num, rest = _parse_project_issue_args(sanitized_args)
    if not project_key or not issue_num:
        if len(sanitized_args) == 1:
            arg = sanitized_args[0]
            maybe_issue = arg.lstrip("#")
            if maybe_issue.isdigit():
                if single_project:
                    return single_project, maybe_issue, []
                # Just an issue number — still need project selection
                context.user_data["pending_issue"] = maybe_issue
                await _prompt_project_selection(update, context, command)
            else:
                # Might be a project key — show issue list for that project
                normalized = _normalize_project_key(arg)
                if normalized and normalized in project_keys:
                    context.user_data["pending_command"] = command
                    context.user_data["pending_project"] = normalized
                    await _prompt_issue_selection(update, context, command, normalized)
                else:
                    await _prompt_project_selection(update, context, command)
        else:
            if single_project:
                context.user_data["pending_command"] = command
                context.user_data["pending_project"] = single_project
                await _prompt_issue_selection(update, context, command, single_project)
                return None, None, []
            await _prompt_project_selection(update, context, command)
        return None, None, []
    if project_key not in project_keys:
        await update.effective_message.reply_text(f"❌ Unknown project '{project_key}'.")
        return None, None, []
    if not issue_num.isdigit():
        await update.effective_message.reply_text("❌ Invalid issue number.")
        return None, None, []
    return project_key, issue_num, rest


async def _handle_pending_issue_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    pending_command = context.user_data.get("pending_command")
    pending_project = context.user_data.get("pending_project")
    pending_issue = context.user_data.get("pending_issue")
    if not pending_command or not pending_project:
        return False

    text = (update.message.text or "").strip()
    if pending_issue is None:
        # If it looks like a feature ideation request or a long descriptive message,
        # don't treat it as an issue number input.
        if is_feature_ideation_request(text) or (len(text) > 15 and " " in text):
            return False

        issue_num = text.lstrip("#")
        if not issue_num.isdigit():
            await update.effective_message.reply_text(
                "Please enter a valid issue number (e.g., 1)."
            )
            return True
        context.user_data["pending_issue"] = issue_num
        if pending_command == "respond":
            await update.effective_message.reply_text(
                "Now send the response message for this issue."
            )
            return True
    else:
        issue_num = pending_issue

    project_key = pending_project
    rest = []
    if pending_command == "respond":
        rest = [text]

    context.user_data.pop("pending_command", None)
    context.user_data.pop("pending_project", None)
    context.user_data.pop("pending_issue", None)

    await _dispatch_command(update, context, pending_command, project_key, issue_num, rest)
    return True


def _command_handler_map():
    return {
        "status": status_handler,
        "active": active_handler,
        "inboxq": inboxq_handler,
        "stats": stats_handler,
        "logs": logs_handler,
        "logsfull": logsfull_handler,
        "tail": tail_handler,
        "fuse": fuse_handler,
        "audit": audit_handler,
        "comments": comments_handler,
        "wfstate": wfstate_handler,
        "visualize": visualize_handler,
        "reprocess": reprocess_handler,
        "reconcile": reconcile_handler,
        "continue": continue_handler,
        "forget": forget_handler,
        "respond": respond_handler,
        "kill": kill_handler,
        "assign": assign_handler,
        "implement": implement_handler,
        "prepare": prepare_handler,
        "pause": pause_handler,
        "resume": resume_handler,
        "stop": stop_handler,
        "track": track_handler,
        "tracked": tracked_handler,
        "untrack": untrack_handler,
        "agents": agents_handler,
    }


def _buttons_to_reply_markup(buttons):
    if not buttons:
        return None

    keyboard: list[list[InlineKeyboardButton]] = []
    for row in buttons:
        keyboard_row: list[InlineKeyboardButton] = []
        for btn in row:
            label = getattr(btn, "label", "")
            callback_data = getattr(btn, "callback_data", None)
            url = getattr(btn, "url", None)
            if url:
                keyboard_row.append(InlineKeyboardButton(label, url=url))
            else:
                keyboard_row.append(InlineKeyboardButton(label, callback_data=callback_data or ""))
        if keyboard_row:
            keyboard.append(keyboard_row)
    return InlineKeyboardMarkup(keyboard) if keyboard else None


def _build_telegram_interactive_ctx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_obj = update.callback_query
    effective_message = update.effective_message

    class _TelegramInteractiveCtx:
        def __init__(self):
            self.user_id = str(getattr(getattr(update, "effective_user", None), "id", ""))
            self.chat_id = int(getattr(getattr(update, "effective_chat", None), "id", 0) or 0)
            self.text = str(getattr(effective_message, "text", "") or "")
            self.args = list(getattr(context, "args", []) or [])
            self.raw_event = update
            self.telegram_context = context
            self.user_state = getattr(context, "user_data", {})
            self.client = SimpleNamespace(name="telegram")
            self.query = (
                SimpleNamespace(
                    data=str(getattr(query_obj, "data", "") or ""),
                    action_data=str(getattr(query_obj, "data", "") or ""),
                    message_id=str(getattr(getattr(query_obj, "message", None), "message_id", "")),
                )
                if query_obj is not None
                else None
            )

        async def reply_text(
            self,
            text: str,
            buttons=None,
            parse_mode: str | None = "Markdown",
            disable_web_page_preview: bool = True,
        ) -> str:
            reply_markup = _buttons_to_reply_markup(buttons)
            msg = await effective_message.reply_text(
                text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )
            return str(getattr(msg, "message_id", ""))

        async def edit_message_text(
            self,
            text: str,
            message_id: str | None = None,
            buttons=None,
            parse_mode: str | None = "Markdown",
            disable_web_page_preview: bool = True,
        ) -> None:
            reply_markup = _buttons_to_reply_markup(buttons)
            if query_obj is not None and hasattr(query_obj, "edit_message_text"):
                await query_obj.edit_message_text(
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_web_page_preview,
                )
                return

            target_message_id = message_id
            if target_message_id is None and effective_message is not None:
                target_message_id = str(getattr(effective_message, "message_id", ""))

            await context.bot.edit_message_text(
                chat_id=getattr(getattr(update, "effective_chat", None), "id", None),
                message_id=target_message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )

        async def answer_callback_query(self, text: str | None = None) -> None:
            if query_obj is not None and hasattr(query_obj, "answer"):
                await query_obj.answer(text=text)

    return _TelegramInteractiveCtx()


def _ctx_telegram_runtime(ctx) -> tuple[Update, ContextTypes.DEFAULT_TYPE]:
    update = getattr(ctx, "raw_event", None)
    context = getattr(ctx, "telegram_context", None)
    if update is None or context is None:
        raise RuntimeError("Missing Telegram runtime in interactive context")
    return update, context


async def _ctx_call_telegram_handler(ctx, handler) -> None:
    update, context = _ctx_telegram_runtime(ctx)
    context.args = list(ctx.args or [])
    await handler(update, context)


async def _ctx_prompt_issue_selection(
    ctx,
    command: str,
    project_key: str,
    *,
    edit_message: bool = False,
    issue_state: str = "open",
) -> None:
    update, context = _ctx_telegram_runtime(ctx)
    await _prompt_issue_selection(
        update,
        context,
        command,
        project_key,
        edit_message=edit_message,
        issue_state=issue_state,
    )


async def _ctx_prompt_project_selection(ctx, command: str) -> None:
    update, context = _ctx_telegram_runtime(ctx)
    await _prompt_project_selection(update, context, command)


async def _ctx_ensure_project_issue(
    ctx,
    command: str,
) -> tuple[str | None, str | None, list[str]]:
    update, context = _ctx_telegram_runtime(ctx)
    context.args = list(getattr(ctx, "args", []) or [])
    return await _ensure_project_issue(update, context, command)


async def _ctx_ensure_project(ctx, command: str) -> str | None:
    args = list(getattr(ctx, "args", []) or [])
    if not args:
        single_project = _get_single_project_key()
        if single_project:
            return single_project
        await _ctx_prompt_project_selection(ctx, command)
        return None
    candidate = _normalize_project_key(str(args[0]))
    if candidate in _iter_project_keys():
        return candidate
    await ctx.reply_text(f"❌ Unknown project '{args[0]}'.")
    return None


async def _ctx_dispatch_command(
    ctx,
    command: str,
    project_key: str,
    issue_num: str,
    rest: list[str] | None = None,
) -> None:
    update, context = _ctx_telegram_runtime(ctx)
    await _dispatch_command(update, context, command, project_key, issue_num, rest)


async def _call_core_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE, handler
) -> None:
    await handler(_build_telegram_interactive_ctx(update, context), _callback_handler_deps())


async def _call_core_chat_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE, handler
) -> None:
    await handler(_build_telegram_interactive_ctx(update, context))


async def _dispatch_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    command: str,
    project_key: str,
    issue_num: str,
    rest: list[str] | None = None,
) -> None:
    project_only_commands = {"agents"}
    if command in project_only_commands:
        context.args = [project_key] + (rest or [])
    else:
        context.args = [project_key, issue_num] + (rest or [])
    handler = _command_handler_map().get(command)
    if handler:
        await handler(update, context)
    else:
        await update.effective_message.reply_text("Unsupported command.")


async def project_picker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _call_core_callback_handler(update, context, callback_project_picker_handler)


async def issue_picker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _call_core_callback_handler(update, context, callback_issue_picker_handler)


async def monitor_project_picker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _call_core_callback_handler(update, context, callback_monitor_project_picker_handler)


async def close_flow_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _call_core_callback_handler(update, context, callback_close_flow_handler)


# --- STATES ---
SELECT_PROJECT, SELECT_TYPE, INPUT_TASK = range(3)

tracked_issues = load_tracked_issues()  # Load on startup
active_tail_sessions: dict[tuple[int, int], str] = {}
active_tail_tasks: dict[tuple[int, int], asyncio.Task] = {}
TASK_CONFIRMATION_MODE = os.getenv("TASK_CONFIRMATION_MODE", "smart").strip().lower()


# --- 0. HELP & INFO ---
async def rename_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rename the active chat."""
    if TELEGRAM_ALLOWED_USER_IDS and update.effective_user.id not in TELEGRAM_ALLOWED_USER_IDS:
        return

    user_id = update.effective_user.id
    active_chat_id = get_active_chat(user_id)

    if not active_chat_id:
        await update.message.reply_text(
            "⚠️ No active chat found. Use /chat to create or select one."
        )
        return

    new_name = " ".join(context.args).strip()
    if not new_name:
        await update.message.reply_text("⚠️ Usage: `/rename <new name>`", parse_mode="Markdown")
        return

    rename_chat(user_id, active_chat_id, new_name)
    await update.message.reply_text(
        f"✅ Active chat renamed to: *{new_name}*", parse_mode="Markdown"
    )


async def chat_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _call_core_chat_handler(update, context, core_chat_menu_handler)


async def chat_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _call_core_chat_handler(update, context, core_chat_callback_handler)


async def chat_agents_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _call_core_chat_handler(update, context, core_chat_agents_handler)


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists available commands and usage info."""
    logger.info(f"Help triggered by user: {update.effective_user.id}")
    if TELEGRAM_ALLOWED_USER_IDS and update.effective_user.id not in TELEGRAM_ALLOWED_USER_IDS:
        logger.warning(f"Unauthorized access attempt by ID: {update.effective_user.id}")
        return

    help_text = (
        "🤖 **Nexus Commands**\n\n"
        "Use /menu for a categorized, button-driven view.\n\n"
        "🗣️ **Chat & Strategy:**\n"
        "/rename <name> - Rename the active chat\n"
        "/chat - Open chat threads and context controls\n\n"
        "/chatagents [project] - Show effective ordered chat agent types (first is primary)\n\n"
        "✨ **Task Creation:**\n"
        "/menu - Open command menu\n"
        "/new - Start a menu-driven task creation\n"
        "/cancel - Abort the current guided process\n\n"
        "⚡ **Hands-Free Mode:**\n"
        "Send a **Voice Note** or **Text Message** directly. "
        "The bot will transcribe, route, and save the task.\n"
        "Task safety guard: confirmation may be required before creation (mode: off|smart|always via `TASK_CONFIRMATION_MODE`).\n\n"
        "📋 **Workflow Tiers:**\n"
        "• 🔥 Hotfix/Chore → fast-track (triage → implement → verify → deploy)\n"
        "• 🩹 Bug → shortened (triage → debug → fix → verify → deploy → close)\n"
        "• ✨ Feature → full (triage → design → develop → review → compliance → deploy → close)\n"
        "• ✨ Simple Feature → fast-track (skip design)\n\n"
        "📊 **Monitoring & Tracking:**\n"
        "/status [project|all] - View pending tasks in inbox\n"
        "/inboxq [limit] - Inspect inbox queue status (postgres mode)\n"
        "/active [project|all] [cleanup] - View tasks currently being worked on\n"
        "/track <project> <issue#> - Track issue per-project\n"
        "/tracked - View active globally tracked issues\n"
        "/untrack <project> <issue#> - Stop tracking per-project\n"
        "/myissues - View all your tracked issues\n"
        "/logs <project> <issue#> - View task logs\n"
        "/logsfull <project> <issue#> - Full log lines (no truncation)\n"
        "/tail <project> <issue#> [lines] [seconds] - Follow live log tail\n"
        "/tailstop - Stop current live tail session\n"
        "/fuse <project> <issue#> - View retry fuse state\n"
        "/audit <project> <issue#> - View workflow audit trail\n"
        "/stats [days] - View system analytics (default: 30 days)\n"
        "/comments <project> <issue#> - View issue comments\n\n"
        "🔁 **Recovery & Control:**\n"
        "/reprocess <project> <issue#> - Re-run agent processing\n"
        "/wfstate <project> <issue#> - Show workflow state and drift snapshot\n"
        "/visualize <project> <issue#> - Show Mermaid workflow diagram for an issue\n"
        "/reconcile <project> <issue#> - Reconcile workflow/comment/local state\n"
        "/continue <project> <issue#> - Check stuck agent status\n"
        "/forget <project> <issue#> - Permanently clear local state for an issue\n"
        "/kill <project> <issue#> - Stop running agent process\n"
        "/pause <project> <issue#> - Pause auto-chaining (agents work but no auto-launch)\n"
        "/resume <project> <issue#> - Resume auto-chaining\n"
        "/stop <project> <issue#> - Stop workflow completely (closes issue, kills agent)\n"
        "/respond <project> <issue#> <text> - Respond to agent questions\n\n"
        "🤝 **Agent Management:**\n"
        "/agents <project> - List all agents for a project\n"
        "/direct <project> <@agent> <message> - Send direct request to an agent\n"
        "/direct <project> <@agent> --new-chat <message> - Strategic direct reply in a new chat thread\n\n"
        "🔧 **Git Platform Management:**\n"
        "/assign <project> <issue#> - Assign issue to yourself\n"
        "/implement <project> <issue#> - Request Copilot agent implementation\n"
        "/prepare <project> <issue#> - Add Copilot-friendly instructions\n\n"
        "ℹ️ /help - Show this list"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


def build_menu_keyboard(button_rows, include_back=True):
    """Build a menu keyboard with optional back button."""
    keyboard = button_rows[:]
    if include_back:
        keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="menu:root")])
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="menu:close")])
    return InlineKeyboardMarkup(keyboard)


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the main menu with submenus."""
    if TELEGRAM_ALLOWED_USER_IDS and update.effective_user.id not in TELEGRAM_ALLOWED_USER_IDS:
        logger.warning(f"Unauthorized access attempt by ID: {update.effective_user.id}")
        return

    keyboard = [
        [InlineKeyboardButton("🗣️ Chat", callback_data="menu:chat")],
        [InlineKeyboardButton("✨ Task Creation", callback_data="menu:tasks")],
        [InlineKeyboardButton("📊 Monitoring", callback_data="menu:monitor")],
        [InlineKeyboardButton("🔁 Workflow Control", callback_data="menu:workflow")],
        [InlineKeyboardButton("🤝 Agents", callback_data="menu:agents")],
        [InlineKeyboardButton("🔧 Git Platform", callback_data="menu:github")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="menu:help")],
        [InlineKeyboardButton("❌ Close", callback_data="menu:close")],
    ]
    await update.effective_message.reply_text(
        "📍 **Nexus Menu**\nChoose a category:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _call_core_callback_handler(update, context, callback_menu_callback_handler)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message and persistent reply keyboard."""
    logger.info(f"Start triggered by user: {update.effective_user.id}")
    if TELEGRAM_ALLOWED_USER_IDS and update.effective_user.id not in TELEGRAM_ALLOWED_USER_IDS:
        logger.warning(f"Unauthorized access attempt by ID: {update.effective_user.id}")
        return

    welcome = (
        "👋 Welcome to Nexus!\n\n"
        "Use the menu buttons to create tasks or monitor queues.\n"
        "Use /chat for project-scoped conversational threads.\n"
        "Send voice or text to create a task automatically.\n\n"
        "💡 **Workflow Tiers:**\n"
        "• 🔥 Hotfix/Chore/Simple Feature → 4 steps (fast)\n"
        "• 🩹 Bug → 6 steps (moderate)\n"
        "• ✨ Feature/Improvement → 9 steps (full)\n\n"
        "Type /help for all commands."
    )

    keyboard = [["/menu"], ["/chat"], ["/new"], ["/status"], ["/active"], ["/help"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

    await update.message.reply_text(welcome, reply_markup=reply_markup)


async def on_startup(application):
    """Register bot commands so they appear in the Telegram client menu."""
    try:
        validate_required_command_interface()
        parity = validate_command_parity()
        telegram_only = sorted(parity.get("telegram_only", set()))
        discord_only = sorted(parity.get("discord_only", set()))
        if telegram_only or discord_only:
            logger.warning(
                "Command parity drift detected: telegram_only=%s discord_only=%s",
                telegram_only,
                discord_only,
            )
    except Exception:
        logger.exception("Command parity strict check failed")
        raise

    cmds = [
        BotCommand("menu", "Open command menu"),
        BotCommand("chat", "Open chat menu"),
        # BotCommand("chatagents", "Show chat agent order"),
        # BotCommand("rename", "Rename active chat"),
        BotCommand("new", "Start task creation"),
        # BotCommand("cancel", "Cancel current process"),
        BotCommand("status", "Show pending tasks"),
        BotCommand("active", "Show active tasks"),
        # BotCommand("track", "Subscribe to issue updates"),
        # BotCommand("untrack", "Stop tracking an issue"),
        # BotCommand("myissues", "View your tracked issues"),
        # BotCommand("logs", "View task execution logs"),
        # BotCommand("logsfull", "Full issue logs"),
        # BotCommand("audit", "View workflow audit trail"),
        # BotCommand("stats", "View system analytics"),
        # BotCommand("comments", "View issue comments"),
        # BotCommand("reprocess", "Re-run agent processing"),
        # BotCommand("continue", "Check stuck agent status"),
        # BotCommand("kill", "Stop running agent"),
        # BotCommand("pause", "Pause auto-chaining"),
        # BotCommand("resume", "Resume auto-chaining"),
        # BotCommand("stop", "Stop workflow completely"),
        # BotCommand("agents", "List project agents"),
        # BotCommand("direct", "Send direct agent request"),
        # BotCommand("respond", "Respond to agent questions"),
        # BotCommand("assign", "Assign an issue"),
        # BotCommand("implement", "Request implementation"),
        # BotCommand("prepare", "Prepare for Copilot"),
        BotCommand("help", "Show help"),
    ]
    try:
        await application.bot.set_my_commands(cmds)
        logger.info("Registered bot commands for Telegram client menu")
    except Exception:
        logger.exception("Failed to set bot commands on startup")

    # Tool availability health check
    await _check_tool_health(application)


async def _check_tool_health(application):
    """Probe Copilot and Gemini availability and broadcast alerts on failure."""
    tools_to_check = [AIProvider.COPILOT, AIProvider.GEMINI]
    unavailable = []
    for tool in tools_to_check:
        try:
            available = orchestrator.check_tool_available(tool)
            if not available:
                unavailable.append(tool.value)
        except Exception as exc:
            logger.warning(f"Health check error for {tool.value}: {exc}")
            unavailable.append(tool.value)

    if unavailable:
        alert = (
            f"⚠️ *Nexus Startup Alert*\n"
            f"The following AI tools are unavailable: `{', '.join(unavailable)}`\n"
            f"Agents using these tools will fail until they recover."
        )
        logger.warning(f"Tool health check failed: {unavailable}")
        if TELEGRAM_CHAT_ID:
            try:
                await application.bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=alert,
                    parse_mode="Markdown",
                )
            except Exception as exc:
                logger.warning(f"Failed to send health alert to Telegram: {exc}")
    else:
        logger.info("✅ Tool health check passed: Copilot and Gemini are available")


def _refine_task_description(text: str, project_key: str | None = None) -> str:
    """Refine task description using orchestrator with graceful fallback."""
    candidate_text = (text or "").strip()
    if not candidate_text:
        return ""

    try:
        logger.info("Refining description with orchestrator (len=%s)", len(candidate_text))
        refine_result = orchestrator.run_text_to_speech_analysis(
            text=candidate_text,
            task="refine_description",
            project_name=PROJECTS.get(project_key) if project_key else None,
        )
        refined = str(refine_result.get("text", "")).strip()
        if refined:
            return refined
    except Exception as exc:
        logger.warning("Failed to refine description: %s", exc)

    return candidate_text


async def feature_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await core_feature_callback_handler(
        _build_telegram_interactive_ctx(update, context),
        _feature_ideation_handler_deps(),
    )


async def task_confirmation_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()

    if TELEGRAM_ALLOWED_USER_IDS and update.effective_user.id not in TELEGRAM_ALLOWED_USER_IDS:
        logger.warning(f"Unauthorized callback access attempt by ID: {update.effective_user.id}")
        return

    data = query.data or ""
    pending = context.user_data.get("pending_task_confirmation")
    if not pending:
        await query.edit_message_text("⚠️ Task confirmation expired. Send the request again.")
        return

    if data == "taskconfirm:cancel":
        context.user_data.pop("pending_task_confirmation", None)
        context.user_data.pop("pending_task_edit", None)
        await query.edit_message_text("❎ Task creation canceled.")
        return

    if data == "taskconfirm:edit":
        context.user_data["pending_task_edit"] = True
        await query.edit_message_text(
            "✏️ Send the updated task text now.\n\n"
            "I will show the confirmation preview again before creating anything.\n"
            "Type `cancel` to abort."
        )
        return

    if data != "taskconfirm:confirm":
        await query.edit_message_text("⚠️ Unknown confirmation action.")
        return

    text = str(pending.get("text") or "").strip()
    message_id = str(pending.get("message_id") or query.message.message_id)
    context.user_data.pop("pending_task_confirmation", None)

    result = await route_task_with_context(
        user_id=update.effective_user.id,
        text=text,
        orchestrator=orchestrator,
        message_id=message_id,
        get_chat=get_chat,
        process_inbox_task=process_inbox_task,
    )
    if not result.get("success") and "pending_resolution" in result:
        context.user_data["pending_task_project_resolution"] = result["pending_resolution"]

    await query.edit_message_text(result.get("message", "⚠️ Task processing completed."))


async def _transcribe_voice_message(
    voice_file_id: str, context: ContextTypes.DEFAULT_TYPE
) -> str | None:
    return await transcribe_telegram_voice(
        voice_file_id, context, _audio_transcription_handler_deps()
    )


# --- 1. HANDS-FREE MODE (Auto-Router) ---
async def hands_free_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logger.info(
            "Hands-free task received: user=%s message_id=%s has_voice=%s has_text=%s",
            update.effective_user.id,
            update.message.message_id if update.message else None,
            bool(update.message and update.message.voice),
            bool(update.message and update.message.text),
        )
        if TELEGRAM_ALLOWED_USER_IDS and update.effective_user.id not in TELEGRAM_ALLOWED_USER_IDS:
            logger.warning(f"Unauthorized access attempt by ID: {update.effective_user.id}")
            return

        if context.user_data.get("pending_chat_rename"):
            if update.message.voice:
                await update.message.reply_text(
                    "⚠️ Please send the new chat name as text (or type `cancel`)."
                )
                return

            candidate = (update.message.text or "").strip()
            if not candidate:
                await update.message.reply_text(
                    "⚠️ Chat name cannot be empty. Send a name or type `cancel`."
                )
                return

            if candidate.lower() in {"cancel", "/cancel"}:
                context.user_data.pop("pending_chat_rename", None)
                await update.message.reply_text("❎ Rename canceled.")
                return

            user_id = update.effective_user.id
            active_chat_id = get_active_chat(user_id)
            if not active_chat_id:
                context.user_data.pop("pending_chat_rename", None)
                await update.message.reply_text(
                    "⚠️ No active chat found. Use /chat to create or select one."
                )
                return

            renamed = rename_chat(user_id, active_chat_id, candidate)
            context.user_data.pop("pending_chat_rename", None)
            if not renamed:
                await update.message.reply_text(
                    "⚠️ Could not rename the active chat. Please try again."
                )
                return

            await update.message.reply_text(
                f"✅ Active chat renamed to: *{candidate}*",
                parse_mode="Markdown",
            )
            await chat_menu_handler(update, context)
            return

        if (not update.message.voice) and await _handle_pending_issue_input(update, context):
            return

        if context.user_data.get("pending_task_edit"):
            if not update.message.voice:
                candidate = (update.message.text or "").strip().lower()
                if candidate in {"cancel", "/cancel"}:
                    context.user_data.pop("pending_task_edit", None)
                    context.user_data.pop("pending_task_confirmation", None)
                    await update.message.reply_text("❎ Task edit canceled.")
                    return

            revised_text = ""
            if update.message.voice:
                msg = await update.message.reply_text("🎧 Transcribing your edited task...")
                revised_text = await _transcribe_voice_message(
                    update.message.voice.file_id, context
                )
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id, message_id=msg.message_id
                )
            else:
                revised_text = (update.message.text or "").strip()

            if not revised_text:
                await update.message.reply_text(
                    "⚠️ I couldn't read the edited task text. Please try again."
                )
                return

            context.user_data["pending_task_edit"] = False
            context.user_data["pending_task_confirmation"] = {
                "text": revised_text,
                "message_id": str(update.message.message_id),
            }
            preview = revised_text if len(revised_text) <= 300 else f"{revised_text[:300]}..."
            keyboard = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("✅ Confirm", callback_data="taskconfirm:confirm")],
                    [InlineKeyboardButton("✏️ Edit", callback_data="taskconfirm:edit")],
                    [InlineKeyboardButton("❌ Cancel", callback_data="taskconfirm:cancel")],
                ]
            )
            await update.message.reply_text(
                "🛡️ *Confirm task creation*\n\n" "Updated request preview:\n\n" f"_{preview}_",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
            return

        # Guard: Don't process commands as tasks
        if update.message.text and update.message.text.startswith("/"):
            logger.info(f"Ignoring command in hands_free_handler: {update.message.text}")
            return

        if await resolve_pending_project_selection(
            _build_telegram_interactive_ctx(update, context),
            _hands_free_routing_handler_deps(),
        ):
            return

        text = ""
        status_text = "⚡ AI Listening..." if update.message.voice else "🤖 Nexus thinking..."
        status_msg = await update.message.reply_text(status_text)

        # Get text from Audio or Text
        if update.message.voice:
            logger.info("Processing voice message...")
            text = await _transcribe_voice_message(update.message.voice.file_id, context)
            if not text:
                logger.warning("Voice transcription returned empty text")
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=status_msg.message_id,
                    text="⚠️ Transcription failed",
                )
                return
        else:
            logger.info(f"Processing text input... text={update.message.text[:50]}")
            text = update.message.text

        active_chat = get_chat(update.effective_user.id)
        active_chat_metadata = active_chat.get("metadata") if isinstance(active_chat, dict) else {}
        preferred_project_key = None
        preferred_agent_type = None
        if isinstance(active_chat_metadata, dict):
            preferred_project_key = active_chat_metadata.get("project_key")
            preferred_agent_type = active_chat_metadata.get("primary_agent_type")

        if await handle_feature_ideation_request(
            _build_telegram_interactive_ctx(update, context),
            str(getattr(status_msg, "message_id", "")),
            text,
            _feature_ideation_handler_deps(),
            preferred_project_key=preferred_project_key,
            preferred_agent_type=preferred_agent_type,
        ):
            return

        await route_hands_free_text(
            update,
            context,
            status_msg,
            text,
            _hands_free_routing_handler_deps(),
        )
    except Exception as e:
        logger.error(f"Unexpected error in hands_free_handler: {e}", exc_info=True)
        with contextlib.suppress(Exception):
            await update.message.reply_text(f"❌ Error: {str(e)[:100]}")


# --- 2. SELECTION MODE (Menu) ---
# (Steps 1 & 2 are purely Telegram UI, no AI needed)


async def start_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if TELEGRAM_ALLOWED_USER_IDS and update.effective_user.id not in TELEGRAM_ALLOWED_USER_IDS:
        return
    keyboard = [[InlineKeyboardButton(name, callback_data=code)] for code, name in PROJECTS.items()]
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="flow:close")])
    await update.message.reply_text(
        "📂 **Select Project:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )
    return SELECT_PROJECT


async def project_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["project"] = query.data
    keyboard = [[InlineKeyboardButton(name, callback_data=code)] for code, name in TYPES.items()]
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="flow:close")])
    await query.edit_message_text(
        f"📂 Project: **{PROJECTS[query.data]}**\n\n🛠 **Select Type:**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return SELECT_TYPE


async def type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["type"] = query.data
    await query.edit_message_text("📝 **Speak or Type the task:**", parse_mode="Markdown")
    return INPUT_TASK


# --- 3. SAVING THE TASK (Uses Gemini only if Voice) ---
async def save_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    project = context.user_data["project"]
    task_type = context.user_data["type"]

    logger.info(
        "Selection task received: user=%s message_id=%s project=%s type=%s has_voice=%s",
        update.effective_user.id,
        update.message.message_id if update.message else None,
        project,
        task_type,
        bool(update.message and update.message.voice),
    )

    text = ""
    if update.message.voice:
        msg = await update.message.reply_text("🎧 Transcribing (CLI)...")
        # Re-use the helper function to just get text
        text = await _transcribe_voice_message(update.message.voice.file_id, context)
        await context.bot.delete_message(
            chat_id=update.effective_chat.id, message_id=msg.message_id
        )
    else:
        text = update.message.text

    if not text:
        await update.message.reply_text("⚠️ Transcription failed. Please try again.")
        return ConversationHandler.END

    # Refine description using orchestrator (Gemini CLI preferred)
    refined_text = text
    try:
        logger.info("Refining description with orchestrator (len=%s)", len(text))
        refine_result = orchestrator.run_text_to_speech_analysis(
            text=text, task="refine_description", project_name=PROJECTS.get(project)
        )
        candidate = refine_result.get("text", "").strip()
        if candidate:
            refined_text = candidate
    except Exception as e:
        logger.warning(f"Failed to refine description: {e}")

    # Generate task name using orchestrator (CLI only)
    task_name = ""
    try:
        logger.info("Generating task name with orchestrator (len=%s)", len(refined_text))
        name_result = orchestrator.run_text_to_speech_analysis(
            text=refined_text[:300], task="generate_name", project_name=PROJECTS.get(project)
        )
        task_name = name_result.get("text", "").strip().strip("\"`'")
    except Exception as e:
        logger.warning(f"Failed to generate task name: {e}")
        task_name = ""

    # Write File
    # Map project name to workspace (e.g., "nexus" → "ghabs")
    workspace = project
    if project in PROJECT_CONFIG:
        workspace = PROJECT_CONFIG[project].get("workspace", project)

    target_dir = get_inbox_dir(os.path.join(BASE_DIR, workspace), project)
    os.makedirs(target_dir, exist_ok=True)
    filename = f"{task_type}_{update.message.message_id}.md"

    with open(os.path.join(target_dir, filename), "w") as f:
        task_name_line = f"**Task Name:** {task_name}\n" if task_name else ""
        f.write(
            f"# {TYPES[task_type]}\n**Project:** {PROJECTS[project]}\n**Type:** {task_type}\n"
            f"{task_name_line}**Status:** Pending\n\n"
            f"{refined_text}\n\n"
            f"---\n"
            f"**Raw Input:**\n{text}"
        )

    await update.message.reply_text(f"✅ Saved to `{project}`.")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


async def flow_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Close button for the /new flow."""
    return await _call_core_callback_handler(update, context, callback_flow_close_handler)


# --- MONITORING COMMANDS ---
def extract_issue_number_from_file(file_path):
    """Extract issue number from task file content if present."""
    try:
        with open(file_path) as f:
            content = f.read()
        match = re.search(r"\*\*Issue:\*\*\s*https?://[^\s`]+/(?:-/)?issues/(\d+)", content)
        if match:
            return match.group(1)
    except Exception as e:
        logger.warning(f"Failed to read issue number from {file_path}: {e}")
    return None


async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows pending tasks in inbox folders."""
    await monitoring_status_handler(
        _build_telegram_interactive_ctx(update, context),
        _monitoring_handler_deps(),
    )


async def progress_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show active issues with current workflow step, agent type, tool, and duration."""
    logger.info(f"Progress requested by user: {update.effective_user.id}")
    if TELEGRAM_ALLOWED_USER_IDS and update.effective_user.id not in TELEGRAM_ALLOWED_USER_IDS:
        logger.warning(f"Unauthorized access attempt by ID: {update.effective_user.id}")
        return

    launched_agents = HostStateManager.load_launched_agents()
    if not launched_agents:
        await update.effective_message.reply_text("ℹ️ No active agents tracked.")
        return

    now = time.time()
    lines = ["📊 *Agent Progress*\n"]
    for issue_num, info in sorted(launched_agents.items(), key=lambda x: x[0]):
        if not isinstance(info, dict):
            continue
        agent_type = info.get("agent_type", "unknown")
        tool = info.get("tool", "unknown")
        tier = info.get("tier", "unknown")
        ts = info.get("timestamp", 0)
        exclude = info.get("exclude_tools", [])
        elapsed = int(now - ts) if ts else 0
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        duration_str = f"{hours}h {minutes}m" if hours else f"{minutes}m {seconds}s"
        line = (
            f"• Issue *#{issue_num}* — `{agent_type}` via `{tool}`\n"
            f"  Tier: `{tier}` | Running: `{duration_str}`"
        )
        if exclude:
            line += f"\n  Excluded tools: `{', '.join(exclude)}`"
        lines.append(line)

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
    )


async def active_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows active tasks being worked on."""
    await monitoring_active_handler(
        _build_telegram_interactive_ctx(update, context),
        _monitoring_handler_deps(),
    )


async def assign_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Assigns a GitHub issue to the user."""
    await issue_assign_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


@rate_limited("implement")
async def implement_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Requests Copilot agent implementation for an issue (approval workflow).

    Adds an `agent:requested` label and notifies `@ProjectLead` with a comment
    so they can approve (add `agent:approved`) or click "Code with agent mode".
    """
    await issue_implement_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


async def prepare_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Augments an issue with Copilot-friendly instructions and acceptance criteria."""
    await issue_prepare_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


async def track_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Subscribe to issue updates and track status changes.

    Usage:
      /track <project> <issue#>    - Track issue in specific project

    Examples:
      /track nxs 456
    """
    await issue_track_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


async def tracked_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show active globally tracked issues."""
    await issue_tracked_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


async def untrack_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop tracking an issue.

    Usage:
      /untrack <issue#>              - Stop global tracking
      /untrack <project> <issue#>    - Stop per-project tracking
    """
    await issue_untrack_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


async def myissues_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all issues tracked by the user across projects."""
    await issue_myissues_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


@rate_limited("logs")
async def logs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show combined timeline of GitHub activity and bot/processor logs for an issue."""
    await monitoring_logs_handler(
        _build_telegram_interactive_ctx(update, context), _monitoring_handler_deps()
    )


@rate_limited("logs")
async def logsfull_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show combined timeline of GitHub activity and full log lines for an issue."""
    await monitoring_logsfull_handler(
        _build_telegram_interactive_ctx(update, context), _monitoring_handler_deps()
    )


@rate_limited("logs")
async def tail_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show a short tail of the latest task log for an issue."""
    await monitoring_tail_handler(
        _build_telegram_interactive_ctx(update, context), _monitoring_handler_deps()
    )


@rate_limited("logs")
async def tailstop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop an active live tail session for the current user/chat."""
    await monitoring_tailstop_handler(
        _build_telegram_interactive_ctx(update, context),
        _monitoring_handler_deps(),
    )


@rate_limited("logs")
async def fuse_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show retry-fuse status for an issue."""
    await monitoring_fuse_handler(
        _build_telegram_interactive_ctx(update, context), _monitoring_handler_deps()
    )


async def audit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display workflow audit trail for an issue (timeline of state changes, agent launches, etc)."""
    await ops_audit_handler(_build_telegram_interactive_ctx(update, context), _ops_handler_deps())


@rate_limited("stats")
async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display system analytics and performance statistics."""
    await ops_stats_handler(_build_telegram_interactive_ctx(update, context), _ops_handler_deps())


@rate_limited("stats")
async def inboxq_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inspect inbox queue summary for postgres backend."""
    await ops_inboxq_handler(_build_telegram_interactive_ctx(update, context), _ops_handler_deps())


@rate_limited("reprocess")
async def reprocess_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-run agent processing for an open issue."""
    await workflow_reprocess_handler(
        _build_telegram_interactive_ctx(update, context), _workflow_handler_deps()
    )


async def continue_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Continue/resume agent processing for an issue with a continuation prompt."""
    await workflow_continue_handler(
        _build_telegram_interactive_ctx(update, context), _workflow_handler_deps()
    )


async def forget_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permanently forget local workflow/tracker state for an issue."""
    await workflow_forget_handler(
        _build_telegram_interactive_ctx(update, context), _workflow_handler_deps()
    )


async def kill_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kill a running Copilot agent process."""
    await workflow_kill_handler(
        _build_telegram_interactive_ctx(update, context), _workflow_handler_deps()
    )


async def reconcile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reconcile workflow and local completion from structured GitHub comments."""
    await workflow_reconcile_handler(
        _build_telegram_interactive_ctx(update, context), _workflow_handler_deps()
    )


async def wfstate_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show workflow state + signal drift snapshot for an issue."""
    await workflow_wfstate_handler(
        _build_telegram_interactive_ctx(update, context), _workflow_handler_deps()
    )


async def visualize_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Render a Mermaid workflow diagram for an issue."""
    await workflow_visualize_handler(
        _build_telegram_interactive_ctx(update, context), _visualize_handler_deps()
    )


async def pause_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pause auto-chaining with project picker support."""
    await workflow_pause_picker_handler(
        _build_telegram_interactive_ctx(update, context), _workflow_handler_deps()
    )


async def resume_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resume auto-chaining with project picker support."""
    await workflow_resume_picker_handler(
        _build_telegram_interactive_ctx(update, context), _workflow_handler_deps()
    )


async def stop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop workflow with project picker support."""
    await workflow_stop_picker_handler(
        _build_telegram_interactive_ctx(update, context), _workflow_handler_deps()
    )


# pause_handler, resume_handler, and stop_handler now wrap commands.workflow handlers


async def agents_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all agents for a specific project."""
    await ops_agents_handler(_build_telegram_interactive_ctx(update, context), _ops_handler_deps())


@rate_limited("direct")
async def direct_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a direct request to a specific agent for a project."""
    await ops_direct_handler(_build_telegram_interactive_ctx(update, context), _ops_handler_deps())


async def comments_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View recent comments on an issue."""
    await issue_comments_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


async def respond_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Post a response to an issue and automatically continue the agent."""
    await issue_respond_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


async def inline_keyboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses from notifications."""
    await _call_core_callback_handler(update, context, callback_inline_keyboard_handler)


async def _report_bug_action_wrapper(ctx, issue_num: str, project_key: str):
    repo_key = get_repo(project_key)
    await handle_report_bug(
        ctx,
        issue_num,
        repo_key=repo_key,
        get_direct_issue_plugin=_get_direct_issue_plugin,
    )


async def telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global Telegram error handler for uncaught update exceptions."""
    logger.exception("Unhandled exception while processing update", exc_info=context.error)

    # Truncate and format
    user_msg = format_error_for_user(context.error, "Internal error")

    # Bug Reporting Button
    buttons = []
    # If we have an issue context in user_data, add a report button
    pending_issue = context.user_data.get("pending_issue")
    pending_project = context.user_data.get("pending_project")
    if pending_issue and pending_project:
        buttons = [
            [Button("🐞 Report Bug", callback_data=f"report_bug_{pending_issue}|{pending_project}")]
        ]

    if isinstance(update, Update) and update.effective_message:
        with contextlib.suppress(Exception):
            await update.effective_message.reply_text(
                f"❌ {user_msg}",
                reply_markup=_buttons_to_reply_markup(buttons) if buttons else None,
            )


# --- MAIN ---
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Initialize report scheduler (start it after app runs)
    report_scheduler = None
    if os.getenv("ENABLE_SCHEDULED_REPORTS", "true").lower() == "true":
        report_scheduler = ReportScheduler()
        logger.info("📊 Scheduled reports will be enabled after startup")

    # Initialize alerting system (start it after app runs)
    alerting_system = None
    if os.getenv("ENABLE_ALERTING", "true").lower() == "true":
        alerting_system = init_alerting_system()
        logger.info("🚨 Alerting system will be enabled after startup")

    # Register commands on startup (Telegram client menu)
    original_post_init = on_startup

    async def post_init_with_scheduler(application):
        """Post init that also starts the report scheduler, alerting system, and event handlers."""
        await original_post_init(application)
        if report_scheduler:
            report_scheduler.start()
            logger.info("📊 Scheduled reports started")
        if alerting_system:
            alerting_system.start()
            logger.info("🚨 Alerting system started")
        # Attach EventBus event handlers (Telegram & Discord notifications)
        try:
            from orchestration.nexus_core_helpers import setup_event_handlers

            setup_event_handlers()
            logger.info("🔔 EventBus event handlers initialized")
        except Exception as exc:
            logger.warning("EventBus event handler setup failed: %s", exc)

    app.post_init = post_init_with_scheduler

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("new", start_selection)],
        states={
            SELECT_PROJECT: [
                CallbackQueryHandler(project_selected, pattern=r"^[a-z_]+$"),
                CallbackQueryHandler(flow_close_handler, pattern=r"^flow:close$"),
            ],
            SELECT_TYPE: [
                CallbackQueryHandler(type_selected, pattern=r"^[a-z-]+$"),
                CallbackQueryHandler(flow_close_handler, pattern=r"^flow:close$"),
            ],
            INPUT_TASK: [MessageHandler(filters.TEXT | filters.VOICE, save_task)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("menu", menu_handler))
    app.add_handler(CommandHandler("rename", rename_handler))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("status", status_handler))
    app.add_handler(CommandHandler("inboxq", inboxq_handler))
    app.add_handler(CommandHandler("active", active_handler))
    app.add_handler(CommandHandler("progress", progress_handler))
    app.add_handler(CommandHandler("track", track_handler))
    app.add_handler(CommandHandler("tracked", tracked_handler))
    app.add_handler(CommandHandler("untrack", untrack_handler))
    app.add_handler(CommandHandler("myissues", myissues_handler))
    app.add_handler(CommandHandler("logs", logs_handler))
    app.add_handler(CommandHandler("logsfull", logsfull_handler))
    app.add_handler(CommandHandler("tail", tail_handler))
    app.add_handler(CommandHandler("tailstop", tailstop_handler))
    app.add_handler(CommandHandler("fuse", fuse_handler))
    app.add_handler(CommandHandler("audit", audit_handler))
    app.add_handler(CommandHandler("wfstate", wfstate_handler))
    app.add_handler(CommandHandler("visualize", visualize_handler))
    app.add_handler(CommandHandler("stats", stats_handler))
    app.add_handler(CommandHandler("comments", comments_handler))
    app.add_handler(CommandHandler("reprocess", reprocess_handler))
    app.add_handler(CommandHandler("reconcile", reconcile_handler))
    app.add_handler(CommandHandler("continue", continue_handler))
    app.add_handler(CommandHandler("forget", forget_handler))
    app.add_handler(CommandHandler("kill", kill_handler))
    app.add_handler(CommandHandler("pause", pause_handler))
    app.add_handler(CommandHandler("resume", resume_handler))
    app.add_handler(CommandHandler("stop", stop_handler))
    app.add_handler(CommandHandler("agents", agents_handler))
    app.add_handler(CommandHandler("direct", direct_handler))
    app.add_handler(CommandHandler("respond", respond_handler))
    app.add_handler(CommandHandler("assign", assign_handler))
    app.add_handler(CommandHandler("implement", implement_handler))
    app.add_handler(CommandHandler("prepare", prepare_handler))
    app.add_handler(CommandHandler("chat", chat_menu_handler))
    app.add_handler(CommandHandler("chatagents", chat_agents_handler))
    # Menu navigation callbacks
    app.add_handler(CallbackQueryHandler(chat_callback_handler, pattern=r"^chat:"))
    app.add_handler(CallbackQueryHandler(menu_callback_handler, pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(project_picker_handler, pattern=r"^pickcmd:"))
    app.add_handler(CallbackQueryHandler(issue_picker_handler, pattern=r"^pickissue"))
    app.add_handler(CallbackQueryHandler(monitor_project_picker_handler, pattern=r"^pickmonitor:"))
    app.add_handler(CallbackQueryHandler(close_flow_handler, pattern=r"^flow:close$"))
    app.add_handler(CallbackQueryHandler(feature_callback_handler, pattern=r"^feat:"))
    app.add_handler(
        CallbackQueryHandler(task_confirmation_callback_handler, pattern=r"^taskconfirm:")
    )
    # Inline keyboard callback handler (must be before ConversationHandler callbacks)
    app.add_handler(
        CallbackQueryHandler(
            inline_keyboard_handler,
            pattern=r"^(logs|logsfull|status|pause|resume|stop|audit|reprocess|respond|approve|reject|wfapprove|wfdeny|report_bug)_",
        )
    )
    # Exclude commands from the auto-router catch-all
    app.add_handler(
        MessageHandler((filters.TEXT | filters.VOICE) & (~filters.COMMAND), hands_free_handler)
    )
    app.add_error_handler(telegram_error_handler)

    print("Nexus Online...")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
