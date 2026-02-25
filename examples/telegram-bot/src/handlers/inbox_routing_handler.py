import logging
import os
from typing import Any

from nexus.core.utils.task_name import generate_task_name, normalize_task_name

from config import (
    BASE_DIR,
    PROJECT_CONFIG,
    get_inbox_dir,
    get_project_display_names,
    get_task_types,
    normalize_project_key,
)
from handlers.common_routing import extract_json_dict

logger = logging.getLogger(__name__)

# Config-driven registries
PROJECTS = get_project_display_names()
TYPES = get_task_types()

def _parse_classification_result(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize orchestrator classification output into a plain dict payload."""
    if not isinstance(result, dict):
        return {}

    if result.get("project"):
        return result

    for field in ("response", "text", "output"):
        candidate = result.get(field)
        if isinstance(candidate, dict) and candidate:
            return candidate
        if isinstance(candidate, str) and candidate.strip():
            parsed = extract_json_dict(candidate)
            if parsed:
                merged = dict(result)
                merged.update(parsed)
                return merged

    return result

def _refine_task_description(content: str, project: str) -> str:
    """Prepend the project name if it's missing."""
    project_display = PROJECTS.get(project, project)
    if not content.lower().startswith(project.lower()) and not content.lower().startswith(project_display.lower()):
        return f"{project_display}: {content}"
    return content

async def process_inbox_task(
    text: str,
    orchestrator,
    message_id_or_unique_id: str,
    project_hint: str | None = None,
) -> dict[str, Any]:
    """
    Core logic for processing a task from natural language text.
    Classifies the task, creates the markdown file in the inbox, and returns the result.
    
    Returns a dict with:
    - success: bool
    - message: str (Error or success message to show the user)
    - project: str (Optional)
    - content: str (Optional)
    - pending_resolution: dict (Optional, if project needs manual selection)
    """
    normalized_project_hint = normalize_project_key(str(project_hint or ""))
    if not normalized_project_hint:
        normalized_project_hint = str(project_hint or "").strip().lower()
    known_projects = dict(PROJECTS)
    if isinstance(PROJECT_CONFIG, dict):
        for project_key in PROJECT_CONFIG.keys():
            normalized_key = str(project_key).strip().lower()
            if not normalized_key:
                continue
            known_projects.setdefault(normalized_key, normalized_key)
    result: dict[str, Any] = {}

    if normalized_project_hint in known_projects:
        logger.info(
            "Using project context '%s' directly; skipping project classification.",
            normalized_project_hint,
        )
        result = {
            "project": normalized_project_hint,
            "type": "feature",
            "task_name": "",
            "content": text,
        }
    else:
        logger.info("Running task classification...")
        result = orchestrator.run_text_to_speech_analysis(
            text=text,
            task="classify",
            projects=list(known_projects.keys()),
            types=list(TYPES.keys())
        )
        logger.info(f"Analysis result: {result}")

    # Parse Result
    try:
        result = _parse_classification_result(result)

        project = result.get("project")
        if isinstance(project, str):
            project = normalize_project_key(project) or project.strip().lower()

        if (not project or project not in known_projects) and normalized_project_hint in known_projects:
            logger.info(
                "Using contextual project fallback '%s' for inbox routing",
                normalized_project_hint,
            )
            project = normalized_project_hint

        if not project or project not in known_projects:
            task_type = result.get("type", "feature")
            if task_type not in TYPES:
                task_type = "feature"
            
            pending_resolution = {
                "raw_text": text or "",
                "content": result.get("text", text or ""),
                "task_type": task_type,
                "task_name": result.get("task_name", ""),
            }
            options = ", ".join(sorted(PROJECTS.keys()))
            error_msg = (
                f"❌ Could not classify project (received: '{project}').\n\n"
                f"Reply with a project key: {options}"
            )
            logger.error(f"Project classification failed: project={project}, valid={list(known_projects.keys())}")
            
            return {
                "success": False,
                "message": error_msg,
                "pending_resolution": pending_resolution
            }
        
        task_type = result.get("type", "feature")
        if task_type not in TYPES:
            logger.warning(f"Type '{task_type}' not in TYPES, defaulting to 'feature'")
            task_type = "feature"
        
        content = result.get("content") or text
        content = _refine_task_description(content, str(project))
        task_name = normalize_task_name(result.get("task_name", ""))
        if not task_name:
            task_name = generate_task_name(
                orchestrator,
                content,
                known_projects.get(str(project), str(project)),
                logger=logger,
            )
        logger.info(f"Parsed: project={project}, type={task_type}, task_name={task_name}")
    except Exception as e:
        logger.error(f"JSON parsing error: {e}", exc_info=True)
        return {
            "success": False,
            "message": "⚠️ JSON Error"
        }

    # Save to File
    logger.info(f"Getting inbox dir for project: {project}")
    
    # Map project name to workspace (e.g., "nexus" → "ghabs")
    workspace = project
    if project in PROJECT_CONFIG:
        workspace = PROJECT_CONFIG[project].get("workspace", project)
        logger.info(f"Mapped project '{project}' → workspace '{workspace}'")
    else:
        logger.warning(f"Project '{project}' not in PROJECT_CONFIG, using as-is for workspace")
    
    target_dir = get_inbox_dir(os.path.join(BASE_DIR, workspace), project)
    logger.info(f"Target inbox dir: {target_dir}")
    os.makedirs(target_dir, exist_ok=True)
    
    filename = f"task_{message_id_or_unique_id}.md"
    filepath = os.path.join(target_dir, filename)

    logger.info(f"Writing to file: {filepath}")
    with open(filepath, "w") as f:
        f.write(
            f"# {TYPES.get(task_type, 'Task')}\n"
            f"**Project:** {PROJECTS.get(project, project)}\n"
            f"**Type:** {task_type}\n"
            f"**Task Name:** {task_name}\n"
            f"**Status:** Pending\n\n"
            f"{content}\n\n"
            f"---\n"
            f"**Raw Input:**\n{text}"
        )

    logger.info(f"✅ File saved: {filepath}")
    
    return {
        "success": True,
        "message": f"✅ Routed to `{project}`\n📝 *{content}*",
        "project": project,
        "content": content
    }

async def save_resolved_task(pending_project: dict, selected_project: str, message_id_or_unique_id: str) -> dict[str, Any]:
    """Save a task that previously lacked a clear project after the user specifies one."""
    project = normalize_project_key(selected_project) or selected_project
    task_type = str(pending_project.get("task_type", "feature"))
    if task_type not in TYPES:
        task_type = "feature"
    text = str(pending_project.get("raw_text", "")).strip()
    content = str(pending_project.get("content", text)).strip()
    content = _refine_task_description(content, project)
    task_name = str(pending_project.get("task_name", "")).strip()

    workspace = str(project)
    if project in PROJECT_CONFIG:
        workspace = str(PROJECT_CONFIG[project].get("workspace", project))
    target_dir = get_inbox_dir(os.path.join(BASE_DIR, workspace), str(project))
    os.makedirs(target_dir, exist_ok=True)
    
    filename = f"task_{message_id_or_unique_id}.md"
    filepath = os.path.join(target_dir, filename)
    with open(filepath, "w") as f:
        f.write(
            f"# {TYPES.get(task_type, 'Task')}\n"
            f"**Project:** {PROJECTS.get(project, project)}\n"
            f"**Type:** {task_type}\n"
            f"**Task Name:** {task_name}\n"
            f"**Status:** Pending\n\n"
            f"{content}\n\n"
            f"---\n"
            f"**Raw Input:**\n{text}"
        )

    return {
        "success": True,
        "message": f"✅ Routed to `{project}`\n📝 *{content}*"
    }
