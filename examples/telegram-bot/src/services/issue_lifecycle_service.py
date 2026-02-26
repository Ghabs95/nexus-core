"""Issue lifecycle helpers extracted from inbox_processor."""

import asyncio
import contextlib
import logging
import os
import re

from orchestration.nexus_core_helpers import get_git_platform

logger = logging.getLogger(__name__)


def create_issue(
    *,
    title: str,
    body: str,
    project: str,
    workflow_label: str,
    task_type: str,
    repo_key: str,
) -> str:
    """Create a provider-backed issue and apply labels with fallback behavior."""
    type_label = f"type:{task_type}"
    project_label = f"project:{project}"
    labels = [project_label, type_label, workflow_label]

    try:
        platform = get_git_platform(repo_key, project_name=project)
        issue_obj = None

        try:
            issue_obj = asyncio.run(platform.create_issue(title=title, body=body, labels=labels))
        except Exception as create_with_labels_exc:
            logger.warning(
                "Issue create with labels failed for project '%s': %s. Retrying without labels.",
                project,
                create_with_labels_exc,
            )
            issue_obj = asyncio.run(platform.create_issue(title=title, body=body, labels=None))

        if not issue_obj:
            raise RuntimeError("GitPlatform.create_issue returned no issue")

        issue_num = str(issue_obj.number)
        label_specs: list[tuple[str, str, str]] = []
        for label in labels:
            if label.startswith("workflow:"):
                label_specs.append((label, "0E8A16", "Workflow tier"))
            elif label.startswith("type:"):
                label_specs.append((label, "1D76DB", "Task type"))
            else:
                label_specs.append((label, "5319E7", "Project key"))

        for label, color, description in label_specs:
            with contextlib.suppress(Exception):
                asyncio.run(platform.ensure_label(label, color=color, description=description))

        with contextlib.suppress(Exception):
            asyncio.run(platform.update_issue(issue_num, labels=labels))

        logger.info("Issue created via GitPlatform adapter")
        return issue_obj.url
    except Exception as exc:
        raise RuntimeError(f"Git platform issue create failed: {exc}") from exc


def rename_task_file_and_sync_issue_body(
    *,
    task_file_path: str,
    issue_url: str,
    issue_body: str,
    project_name: str,
    repo_key: str,
) -> str:
    """Rename local task file to include issue number and sync issue body task-file path."""
    issue_num = str(issue_url).rstrip("/").split("/")[-1]
    old_basename = os.path.basename(task_file_path)
    new_basename = re.sub(r"_(\d+)\.md$", f"_{issue_num}.md", old_basename)
    if new_basename == old_basename:
        return task_file_path

    renamed_path = os.path.join(os.path.dirname(task_file_path), new_basename)
    os.rename(task_file_path, renamed_path)
    logger.info("Renamed task file: %s -> %s", old_basename, new_basename)

    corrected_body = issue_body.replace(task_file_path, renamed_path)
    try:
        platform = get_git_platform(repo_key, project_name=project_name)
        asyncio.run(platform.update_issue(issue_num, body=corrected_body))
    except Exception as update_exc:
        logger.warning(
            "Failed to update issue body after task file rename for issue #%s: %s",
            issue_num,
            update_exc,
        )
    return renamed_path
