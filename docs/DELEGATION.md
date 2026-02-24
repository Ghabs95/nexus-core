# Multi-Agent Collaborative Delegation Protocol

The Multi-Agent Collaborative Delegation Protocol enables complex, non-linear workflows by allowing a "lead" agent to delegate specific sub-tasks to other specialized agents and await structured results.

## Overview

Unlike traditional linear workflows where agents execute in a pre-defined sequence, the delegation protocol allows for dynamic, nested execution. A lead agent (e.g., `developer`) can request a sub-task from another agent (e.g., `reviewer`), pause its own execution logic, and resume once the sub-agent provides a structured callback.

### Key Components

| Component | Description |
|---|---|
| `DelegationRequest` | A formal request from a lead agent containing the task description, context, and target sub-agent. |
| `DelegationCallback` | A structured response from a sub-agent containing the result of the sub-task. |
| `HandoffManager` | A thread-safe registry that tracks active delegations and matches callbacks to their original requests. |
| `AIOrchestrator` | Updated to support `execute_with_delegation()`, which handles the registration and resolution of delegation chains. |

---

## How It Works

### 1. Initiating a Delegation

When a lead agent needs assistance from another agent, it emits a `DelegationRequest`. In the Nexus core, this is registered via the `HandoffManager`.

```python
from nexus.core.models import DelegationRequest
from nexus.plugins.plugin_runtime import HandoffManager

# 1. Create the request
request = DelegationRequest(
    lead_agent="developer",
    sub_agent="reviewer",
    issue_number="70",
    workflow_id="nexus-70-full",
    task_description="Please review the changes in models.py",
    task_context={"files": ["nexus/core/models.py"]}
)

# 2. Register with the manager
handoff_manager.register(request)
```

### 2. Executing with Delegation

The `AIOrchestrator` provides a dedicated method `execute_with_delegation()` that automates the lifecycle:

```python
result = await orchestrator.execute_with_delegation(
    agent_name="reviewer",
    prompt="Review these changes...",
    workspace="/path/to/repo",
    delegation_request=request,
    handoff_manager=handoff_manager
)
```

### 3. Resolving the Callback

The sub-agent communicates its completion by embedding a special JSON marker in its output. This allows the protocol to work across different agent runtimes without requiring side-channel communication.

**Sub-agent output example:**

```text
I have finished the review. Everything looks good!

{"__delegation_callback__": {
    "delegation_id": "550e8400-e29b-41d4-a716-446655440000",
    "result": {"status": "approved", "comments": []},
    "success": true
}}
```

The `AIOrchestrator` automatically:
1. Detects this marker using regex.
2. Parses the JSON payload.
3. Calls `handoff_manager.complete()` to resolve the delegation.
4. Strips the marker from the final output returned to the lead agent.

---

## Technical Details

### Thread Safety

The `HandoffManager` uses a `threading.Lock` to ensure that concurrent agent executions can safely register and resolve delegations without race conditions.

### Expiry and Stale Delegations

Delegations can optionally include an `expires_at` timestamp (ISO-8601). The `HandoffManager.expire_stale()` method can be called periodically to clean up delegations that were never resolved.

### Data Models

#### DelegationStatus (Enum)
- `PENDING`: Created but not yet active.
- `ACTIVE`: Registered and awaiting callback.
- `COMPLETED`: Successfully resolved via callback.
- `FAILED`: Explicitly marked as failed.
- `EXPIRED`: Time limit reached before resolution.

#### DelegationRequest (Dataclass)
- `delegation_id`: Unique UUID4 identifier.
- `lead_agent`: Identifier of the delegating agent.
- `sub_agent`: Identifier of the target agent.
- `issue_number`: Associated GitHub issue.
- `workflow_id`: Associated workflow execution.
- `task_description`: Human-readable description of the sub-task.
- `task_context`: Arbitrary dictionary of data for the sub-agent.
- `expires_at`: Optional expiry timestamp.

#### DelegationCallback (Dataclass)
- `delegation_id`: Matches the original request.
- `result`: Arbitrary dictionary of output data.
- `success`: Boolean indicating task success.
- `error`: Optional error message if success is False.
