"""Core data models for Nexus workflows."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class WorkflowState(Enum):
    """Workflow execution state."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(Enum):
    """Individual step execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Severity(Enum):
    """Alert/notification severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class Agent:
    """AI agent definition."""

    name: str
    display_name: str
    description: str
    provider_preference: Optional[str] = None  # "openai", "copilot", "gemini", etc.
    timeout: int = 600  # seconds
    max_retries: int = 3

    def __str__(self) -> str:
        return f"@{self.name}"

    def __hash__(self) -> int:
        return hash(self.name)


@dataclass
class WorkflowStep:
    """Single step in a workflow execution."""

    step_num: int
    name: str
    agent: Agent
    prompt_template: str
    condition: Optional[str] = None  # Python expression, e.g. "prev_step.result.tier == 'high'"
    timeout: Optional[int] = None  # Override agent default
    retry: Optional[int] = None  # Override agent default
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    status: StepStatus = StepStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None

    def __str__(self) -> str:
        return f"Step {self.step_num}: {self.name} ({self.agent.name})"


@dataclass
class Workflow:
    """Complete workflow definition and state."""

    id: str
    name: str
    version: str
    description: str = ""
    steps: List[WorkflowStep] = field(default_factory=list)
    state: WorkflowState = WorkflowState.PENDING
    current_step: int = 0
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def get_step(self, step_num: int) -> Optional[WorkflowStep]:
        """Get step by number."""
        for step in self.steps:
            if step.step_num == step_num:
                return step
        return None

    def get_next_step(self) -> Optional[WorkflowStep]:
        """Get the next pending step."""
        return self.get_step(self.current_step + 1)

    def is_complete(self) -> bool:
        """Check if workflow is complete."""
        return self.state in (WorkflowState.COMPLETED, WorkflowState.FAILED, WorkflowState.CANCELLED)

    def __len__(self) -> int:
        return len(self.steps)


@dataclass
class Task:
    """Input task to be processed by workflow."""

    id: str
    workflow_id: str
    title: str
    description: str
    created_by: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"Task #{self.id}: {self.title}"


@dataclass
class AgentResult:
    """Result from agent execution."""

    success: bool
    output: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    execution_time: float = 0.0
    provider_used: Optional[str] = None
    error: Optional[str] = None
    retry_count: int = 0


@dataclass
class AuditEvent:
    """Single audit log entry."""

    workflow_id: str
    timestamp: datetime
    event_type: str  # e.g., "STEP_STARTED", "STEP_COMPLETED", "WORKFLOW_PAUSED"
    data: Dict[str, Any]
    user_id: Optional[str] = None

    def __str__(self) -> str:
        return f"[{self.timestamp.isoformat()}] {self.event_type}: {self.workflow_id}"


@dataclass
class RateLimitStatus:
    """Rate limit status for an AI provider."""

    provider: str
    is_limited: bool
    reset_at: Optional[datetime] = None
    requests_remaining: Optional[int] = None
    requests_limit: Optional[int] = None


@dataclass
class WorkflowExecution:
    """Complete workflow execution context."""

    workflow: Workflow
    task: Task
    current_context: Dict[str, Any] = field(default_factory=dict)
    audit_log: List[AuditEvent] = field(default_factory=list)

    def add_audit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Add an audit event."""
        event = AuditEvent(
            workflow_id=self.workflow.id,
            timestamp=datetime.utcnow(),
            event_type=event_type,
            data=data,
        )
        self.audit_log.append(event)
