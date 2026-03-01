# ADR-089: Telegram `/watch` Live Workflow Updates

## Status

Proposed

## Context

The Nexus Telegram bot currently provides static workflow visualization through Mermaid diagrams. Users must manually
request updates (e.g., via `/visualize`) to see progress. ADR-083 introduced real-time WebSocket events for the web
visualizer, but these are not yet leveraged by the Telegram bot. Providing live updates in chat improves the developer
experience by allowing "fire and forget" monitoring of long-running workflows.

## Decision

We will implement a `/watch` command in the Telegram bot that subscribes to live workflow events and relays them to the
user's chat session.

### 1. Command UX

- `/watch <project> <issue#>`: Starts a live watch for the specified workflow.
- `/watch status`: Displays the current watch target and session health.
- `/watch stop`: Terminates the active watch session.

### 2. Event Mapping

The bot will connect as a Socket.IO client to the `/visualizer` namespace and listen for:

- `step_status_changed`: Relayed as a concise message: `▶️ [agent] status (step: id)`.
- `workflow_completed`: Relayed as a terminal summary: `✅ Workflow completed: status (summary)`. This event also
  triggers an automatic unsubscription.
- `mermaid_diagram`: Optionally processed to provide a high-level progress digest (e.g., "3/10 steps done").

### 3. Backend-Safe State Handling

Subscriptions must persist across bot restarts and be compatible with both `filesystem` and `postgres` backends.

- **Storage Key**: `workflow_watch_subscriptions` in the host state.
- **Schema**:
  ```json
  {
    "chat_id:user_id": {
      "chat_id": "integer",
      "user_id": "integer",
      "project_key": "string",
      "issue_num": "string",
      "workflow_id": "string",
      "mermaid_enabled": "boolean",
      "last_event_at": "timestamp",
      "last_event_key": "string",
      "last_sent_at": "timestamp",
      "updated_at": "timestamp"
    }
  }
  ```
- The `HostStateManager` will be used to ensure backend-agnostic persistence.

### 4. Noise Control and Throttling

To prevent chat flooding:

- **Deduplication**: Events with the same `(issue, step_id, status)` within a short window will be ignored.
- **Throttling**: Non-terminal updates will be throttled (default: 1 update per 2 seconds). Terminal events always
  bypass the throttle.

### 5. Failure and Reconnect Behavior

- **Socket Disconnect**: The bot will attempt to reconnect to the Socket.IO server with exponential backoff.
- **Bot Restart**: On startup, the bot will rehydrate subscriptions from storage and resume watching.
- **Stale Subscriptions**: The initial implementation does not perform automatic TTL-based cleanup. Subscriptions may
  persist until a `/watch stop` command is issued or a future maintenance/TTL mechanism is introduced.

## Consequences

- **Pros**: Real-time feedback in Telegram; consistent monitoring experience across web and mobile; reuses existing
  WebSocket infrastructure.
- **Cons**: Adds complexity to the bot's runtime (Socket.IO client management); potential for noise if not throttled
  correctly.

## Alignment

- **ADR-083**: Reuses the `step_status_changed` and `workflow_completed` event contract.
- **Storage Backends**: Adheres to the dual-backend support requirement.
- **Framework Integration**: Remains strictly in the integration layer (`examples/nexus-bot`).
