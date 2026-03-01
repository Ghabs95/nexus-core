"""
Nexus ARC (Agentic Runtime Core) - Production-grade AI workflow orchestration framework.

Copyright (c) 2026 Nexus Team
Licensed under Apache 2.0
"""

__version__ = "0.1.0"

from nexus.adapters.git import GitHubPlatform

# Adapter exports
from nexus.adapters.storage import FileStorage
from nexus.core.completion import (
    CompletionSummary,
    DetectedCompletion,
    build_completion_comment,
    generate_completion_instructions,
    scan_for_completions,
)
from nexus.core.guards import LaunchGuard
from nexus.core.models import (
    Agent,
    AgentResult,
    Task,
    Workflow,
    WorkflowState,
    WorkflowStep,
)
from nexus.core.orchestrator import AIOrchestrator
from nexus.core.workflow import WorkflowDefinition, WorkflowEngine
from nexus.plugins import (
    PluginKind,
    PluginNotFoundError,
    PluginRegistrationError,
    PluginRegistry,
    PluginSpec,
)

__all__ = [
    # Version
    "__version__",
    # Core
    "WorkflowEngine",
    "WorkflowDefinition",
    "AIOrchestrator",
    # Completion Protocol
    "CompletionSummary",
    "DetectedCompletion",
    "build_completion_comment",
    "generate_completion_instructions",
    "scan_for_completions",
    # Guards
    "LaunchGuard",
    # Models
    "Workflow",
    "WorkflowStep",
    "WorkflowState",
    "Agent",
    "Task",
    "AgentResult",
    # Adapters
    "FileStorage",
    "GitHubPlatform",
    # Plugins
    "PluginKind",
    "PluginSpec",
    "PluginRegistry",
    "PluginRegistrationError",
    "PluginNotFoundError",
]
