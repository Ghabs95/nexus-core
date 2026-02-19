"""
Nexus Core - Production-grade AI workflow orchestration framework.

Copyright (c) 2026 Nexus Team
Licensed under MIT License
"""

__version__ = "0.2.0"

from nexus.core.workflow import WorkflowEngine, WorkflowDefinition
from nexus.core.orchestrator import AIOrchestrator
from nexus.core.completion import (
    CompletionSummary,
    DetectedCompletion,
    build_completion_comment,
    generate_completion_instructions,
    scan_for_completions,
)
from nexus.core.guards import LaunchGuard
from nexus.core.models import (
    Workflow,
    WorkflowStep,
    WorkflowState,
    Agent,
    Task,
    AgentResult,
)

# Adapter exports
from nexus.adapters.storage import FileStorage
from nexus.adapters.git import GitHubPlatform

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
]
