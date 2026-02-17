"""
Nexus Core - Production-grade AI workflow orchestration framework.

Copyright (c) 2026 Nexus Team
Licensed under MIT License
"""

__version__ = "0.1.0"

from nexus.core.workflow import WorkflowEngine, WorkflowDefinition
from nexus.core.orchestrator import AIOrchestrator
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
