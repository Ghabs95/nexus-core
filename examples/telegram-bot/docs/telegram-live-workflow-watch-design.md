# Telegram `/watch` Live Workflow Watch — Design Proposal

## Metadata

- Feature: `feat/telegram-workflow-live-watch`
- Scope: `examples/telegram-bot`
- Depends on: `docs/ADR-083-Live-Visualizer-Updates.md`
- Related commands: `/visualize`, `/tail`, `/tailstop`, `/wfstate`

## 1. Problem and Goals

The Telegram bot already provides static workflow visibility (`/visualize` Mermaid snapshot) and log tailing (`/tail`), but there is no direct live stream of workflow transitions equivalent to the web `/visualizer` page.

### Goals

- Add `/watch <project> <issue#>` to stream workflow status updates in chat.
- Subscribe to existing `/visualizer` event types without introducing a new backend event protocol:
  - `step_status_changed`
  - `workflow_completed`
  - optional `mermaid_diagram`
- Support both storage backends (`filesystem` and `postgres`) safely.
- Handle disconnect/reconnect and missed-event recovery deterministically.

### Non-goals

- Replacing `/visualize` static snapshot behavior.
- Reworking web visualizer event contracts.
- Adding new workflow state tables or schema migrations.

## 2. Existing Architecture Alignment

Current repository behavior already matches most prerequisites:

- `orchestration/nexus_core_helpers.py` bridges workflow EventBus events to Socket.IO `/visualizer` events (`step_status_changed`, `workflow_completed`, `mermaid_diagram`).
- `state_manager.py` provides `HostStateManager.emit_transition(...)` and `emit_step_status_changed(...)` for outbound transition events.
- Storage backend abstraction already exists for host/workflow state through `state_manager.py` and `integrations/workflow_state_factory.py`.

Design principle: implement Telegram watch as a **consumer** of the same event stream, not a parallel state machine.

## 3. Command UX

## `/watch` command syntax

- `/watch <project_key> <issue#>`: start or replace watch session for caller chat/user.
- `/watch stop` or `/watch stop <project_key> <issue#>`: stop active watch session(s).
- `/watch mermaid on|off`: toggle optional Mermaid updates for active session.
- `/watch status`: show active watch subscriptions and last received event timestamp.

### UX behavior

- On start:
  - Validate user authorization (same pattern as existing handlers).
  - Resolve project and issue via `ensure_project_issue` helper.
  - Send confirmation message: `👀 Watching workflow for <project>#<issue> ...`
- During watch:
  - Post concise transition updates: `▶️ <agent_type> <status> (step: <step_id>)`
  - Coalesce bursts to avoid Telegram flood (debounce window, e.g., 1-2 seconds).
  - **Deduplication**: Events with the same `(issue, step_id, status)` within a short window will be ignored.
  - **Throttling**: Non-terminal updates will be throttled (default: 1 update per 2 seconds). Terminal events always bypass the throttle.
  - Only post Mermaid updates when explicitly enabled.
- On completion (`workflow_completed`):
  - Send terminal summary: `✅ Workflow completed: <status> (<summary>)`
  - Auto-stop session for that issue.
- On stop:
  - `⏹️ Stopped workflow watch for <project>#<issue>.`

## 4. Event Mapping Contract

Use the ADR-083 event payloads with no schema changes.

### `step_status_changed` -> Telegram message

Input payload:

```json
{
  "issue": "106",
  "workflow_id": "nexus-106-full",
  "step_id": "designer",
  "agent_type": "designer",
  "status": "running",
  "timestamp": 1740494242.0
}
```

Rendered message template:

```text
👣 Workflow #106
Step: designer (@designer)
Status: running
Workflow: nexus-106-full
Time: 2026-02-28T..Z
```

### `workflow_completed` -> Telegram message

Input payload:

```json
{
  "issue": "106",
  "workflow_id": "nexus-106-full",
  "status": "success",
  "summary": "All steps completed",
  "timestamp": 1740494500.0
}
```

Rendered message template:

```text
✅ Workflow complete for #106
Result: success
Summary: All steps completed
```

### Optional `mermaid_diagram` -> Telegram update

- Default `off` to avoid noisy/large messages.
- When `on`, prefer editing a pinned watch message (single rolling Mermaid block) rather than sending new messages per event.

## 5. Backend-Safe State Handling

Watch session state is host runtime metadata, not workflow source of truth.

### State model

Create a host-state key (`watch_sessions`) in the existing host-state store abstraction. The implementation enforces a single active subscription per chat/user — the most recent `/watch` command replaces any existing session.

- Session key: `<chat_id>:<user_id>`
- Value fields:
  - `chat_id`
  - `user_id`
  - `project_key`
  - `issue_num`
  - `workflow_id` (nullable until resolved from events)
  - `mermaid_enabled` (bool)
  - `last_event_at`
  - `last_event_key`
  - `last_sent_at`
  - `updated_at`

### Persistence strategy

- Reuse `HostStateManager._load_json_state/_save_json_state` patterns.
- Respect `NEXUS_HOST_STATE_BACKEND` (`filesystem` default, optional `postgres`) with existing fallback behavior.
- Do not duplicate workflow step state inside watch session state; derive live truth from events and recovery snapshots.

### Recovery snapshot

On watch start or reconnect, fetch a one-time snapshot from workflow state (`build_workflow_snapshot`/`wfstate` dependencies) and send:

- current step
- current agent
- workflow state
- expected running agent

This covers missed events during downtime and works identically across backend types.

## 6. Failure and Reconnect Behavior

## Failure classes

- Socket event bridge unavailable.
- Telegram API send/edit errors (rate limit, parse errors, network).
- Bot restart causing in-memory subscribers to drop.

### Strategy

- Use an internal watch dispatcher with bounded queue per chat/user; if overflow, emit single warning and continue from latest state.
- Persist sessions; on startup recovery service, reload active sessions and reattach subscribers.
- Heartbeat timeout (no events for N seconds while workflow is `running`) triggers `/watch` recovery snapshot message.
- On send failure:
  - retry with exponential backoff for transient errors,
  - fallback from edit to send if target message missing,
  - stop session after repeated permanent failures and notify user.

## 7. Integration Points (Implementation Plan)

## New components

- `src/services/workflow_watch_service.py`
  - subscribe/unsubscribe registry
  - session persistence adapter
  - event-to-telegram rendering
  - reconnect + recovery logic

## Existing files to extend

- `src/handlers/monitoring_command_handlers.py`
  - add `/watch` handler wrappers (`watch`, `watchstop` optional alias)
- `src/services/telegram/telegram_main_bootstrap_service.py`
  - register new command in command map and application handlers
- `src/services/command_contract.py`
  - add `watch` to `TELEGRAM_COMMANDS`
- `src/services/telegram/telegram_bootstrap_ui_service.py`
  - add help text lines
- `src/telegram_bot.py`
  - wire dependencies through existing DI pattern
- tests:
  - `tests/test_monitoring_command_handlers.py`
  - new `tests/test_workflow_watch_service.py`

## 8. Observability and Safety

- Log fields: `issue`, `project`, `workflow_id`, `event_type`, `session_key`, `backend`.
- Add metric counters (if existing telemetry sink available):
  - `watch_sessions_started_total`
  - `watch_events_forwarded_total`
  - `watch_events_dropped_total`
  - `watch_recoveries_total`
- Respect existing allowed-user checks before session creation and before sending events.

## 9. Rollout Plan

1. Land `/watch` command + service behind env flag `NEXUS_TELEGRAM_WATCH_ENABLED=true` (default true in examples).
2. Add tests for command parsing, event mapping, persistence, reconnect logic.
3. Validate on filesystem backend locally.
4. Validate on postgres backend with simulated restart.
5. Promote to default docs/help as stable.

## 10. Open Questions

- Should `/watch` allow multiple issues per user concurrently, or enforce one active issue per chat/user?
- Should Mermaid updates be full diagram replacement or step-focused delta excerpt?
- Should `/watch` mirror to tracked users (`tracked_issues`) or remain opt-in per command caller only?

## Feature Alignment

- `alignment_score`: `1.00`
- `alignment_summary`: Repository-native ADR-083 and bot documentation strongly support adding Telegram live watch by consuming existing `/visualizer` event contracts. The design leverages existing WebSocket infrastructure and state management abstractions.
- `alignment_artifacts`:
  - `docs/ADR-083-Live-Visualizer-Updates.md`
  - `examples/telegram-bot/src/orchestration/nexus_core_helpers.py`
  - `examples/telegram-bot/docs/storage-backends.md`
  - `examples/telegram-bot/docs/workflow-lifecycle.md`
  - `examples/telegram-bot/README.md`
