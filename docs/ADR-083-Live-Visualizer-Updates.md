# ADR 083: Live Visualizer Updates

## Status

Proposed

## Context

The Nexus Mermaid visualizer (accessible via `/visualizer` on the webhook server) currently requires manual page
refreshes to see workflow progress. Real-time visibility into workflow execution stages improves monitoring efficiency
and provides immediate feedback during complex orchestration tasks.

Triage confirmed that partial WebSocket infrastructure (using `flask-socketio` and `eventlet`) is already in place, but
it only broadcasts `agent_registered` and `workflow_mapped` events. Step-level transitions and workflow completion
events are missing.

Furthermore, the web visualizer (`visualizer.html`) uses Cytoscape.js for rendering, while the Mermaid stack is
primarily used for static PNG generation in Telegram.

## Decision

We will enhance the existing WebSocket infrastructure to broadcast step-level status changes and workflow completion. We
will also introduce a hybrid visualization approach in the web dashboard:

1. **Cytoscape.js**: Continue using Cytoscape.js as the primary live graph, updating node styles in real-time based on
   status changes.
2. **Mermaid.js**: Add a dedicated Mermaid tab to the visualizer that renders live Mermaid diagrams using `mermaid.js` (
   loaded via CDN).

### WebSocket API Contract (Namespace: `/visualizer`)

#### 1. `step_status_changed`

Emitted each time a workflow step transitions state.

**Payload:**

```json
{
  "issue": "83",
  "workflow_id": "nexus-83-full",
  "step_id": "analyze",
  "agent_type": "triage",
  "status": "running",
  "timestamp": 1740494242.0
}
```

**Status values:** `pending`, `running`, `done`, `failed`, `skipped`

#### 2. `workflow_completed`

Emitted when the full workflow finishes.

**Payload:**

```json
{
  "issue": "83",
  "workflow_id": "nexus-83-full",
  "status": "success",
  "summary": "All 11 steps completed",
  "timestamp": 1740494500.0
}
```

**Status values:** `success`, `failed`

#### 3. `mermaid_diagram`

Emitted after each `step_status_changed` with updated diagram syntax.

**Payload:**

```json
{
  "issue": "83",
  "workflow_id": "nexus-83-full",
  "diagram": "flowchart LR
  triage[triage]:::done --> designer[designer]:::running
  designer --> developer[developer]:::pending",
  "timestamp": 1740494242.0
}
```

## Consequences

- **Positive**: Real-time feedback for users monitoring workflows.
- **Positive**: unified visualization strategy supporting both graph-based (Cytoscape) and syntax-based (Mermaid) views.
- **Neutral**: Requires updates to both backend (Python) and frontend (HTML/JS).
- **Neutral**: No new Python dependencies as `flask-socketio` is already used.
