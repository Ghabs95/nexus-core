"""Agent definition discovery and resolution.

Provides utilities for finding agent YAML definitions by their
``spec.agent_type`` field.  This is the framework-level counterpart
of :class:`WorkflowDefinition` — while that class resolves *workflow*
YAML files, these helpers resolve *agent* YAML files that describe
individual agent capabilities, tools, and input/output contracts.
"""

import glob
import os
import re
from typing import Any, Dict, List, Optional

import yaml


def normalize_agent_key(agent_name: str) -> str:
    """Normalise an agent name for matching YAML ``metadata.name`` values.

    Converts CamelCase to kebab-case, replaces underscores/spaces with
    hyphens, collapses repeated hyphens, and lower-cases the result.

    Examples::

        >>> normalize_agent_key("ProductDesigner")
        'product-designer'
        >>> normalize_agent_key("qa_guard")
        'qa-guard'
        >>> normalize_agent_key("Atlas")
        'atlas'
    """
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", agent_name)
    name = name.replace("_", "-").replace(" ", "-")
    name = re.sub(r"-+", "-", name)
    return name.lower()


def find_agent_yaml(agent_type: str, search_dirs: List[str]) -> str:
    """Find an agent YAML definition file by ``spec.agent_type``.

    Iterates through *search_dirs* in order, scanning for YAML files
    with ``kind: Agent`` whose ``spec.agent_type`` matches *agent_type*
    (compared after normalisation via :func:`normalize_agent_key`).

    Returns the absolute path to the first matching file, or an empty
    string if no match is found.

    Parameters
    ----------
    agent_type:
        The agent type to resolve (e.g. ``"Architect"``, ``"triage"``).
    search_dirs:
        Ordered list of directories to search.  Non-existent directories
        are silently skipped.

    Returns
    -------
    str
        Absolute path to the matching YAML file, or ``""`` if not found.
    """
    normalized = normalize_agent_key(agent_type)

    for search_dir in search_dirs:
        if not os.path.isdir(search_dir):
            continue
        patterns = [
            os.path.join(search_dir, "**", "*.yaml"),
            os.path.join(search_dir, "**", "*.yml"),
        ]
        for pattern in patterns:
            for path in glob.glob(pattern, recursive=True):
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        data = yaml.safe_load(handle)
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("kind") != "Agent":
                    continue

                spec = data.get("spec", {})
                spec_agent_type = spec.get("agent_type", "")
                if not spec_agent_type:
                    continue

                if normalized == normalize_agent_key(spec_agent_type):
                    return os.path.abspath(path)
    return ""


def load_agent_definition(agent_type: str, search_dirs: List[str]) -> Optional[Dict[str, Any]]:
    """Load and parse an agent YAML definition.

    Combines :func:`find_agent_yaml` with YAML parsing.  Returns the
    parsed dictionary on success, or ``None`` if not found.

    Parameters
    ----------
    agent_type:
        The agent type to resolve.
    search_dirs:
        Ordered list of directories to search.

    Returns
    -------
    dict or None
        Parsed YAML content, or ``None`` if no definition found.
    """
    path = find_agent_yaml(agent_type, search_dirs)
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except Exception:
        return None
