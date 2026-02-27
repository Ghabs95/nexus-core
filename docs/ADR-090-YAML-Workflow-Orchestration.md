# ADR 090: YAML Workflow Orchestration Contract

## Status
Proposed

## Context
Nexus already supports YAML workflow definitions for step sequencing (`steps`, `on_success`, `routes`) and tier selection (`workflow_types`). However, end-to-end orchestration behavior is still split across code paths:

- `WorkflowEngine` controls step state transitions.
- `ProcessOrchestrator` handles completion scanning, timeout checks, and auto-chaining.
- Host apps (for example the Telegram bot runtime) still own parts of the polling and recovery loop.

This creates configuration drift: step logic is declarative in YAML, but orchestration runtime behavior is mostly imperative in host code.

Issue #90 requires a design-focused, YAML-based orchestration system so runtime behavior can be declared, validated, and reused consistently.

## Decision
Introduce a versioned orchestration section in workflow YAML and a matching runtime contract in `nexus-core`.

### 1. YAML Contract (v2)
Workflows keep existing step definitions and add a top-level `orchestration` block.

```yaml
schema_version: "2.0"
name: "Enterprise Development Workflow"
workflow_types:
  full: "full"
  shortened: "shortened"

orchestration:
  polling:
    interval_seconds: 15
    completion_glob: ".nexus/tasks/nexus/completions/completion_summary_*.json"
    dedupe_cache_size: 500
  timeouts:
    default_agent_timeout_seconds: 3600
    liveness_miss_threshold: 3
    timeout_action: "retry" # retry | fail_step | alert_only
  chaining:
    enabled: true
    require_completion_comment: true
    block_on_closed_issue: true
  retries:
    max_retries_per_step: 2
    backoff: "exponential" # constant | linear | exponential
    initial_delay_seconds: 1.0
  recovery:
    stale_running_step_action: "reconcile" # reconcile | fail_workflow
```

### 2. Runtime API Contract
Add explicit orchestration abstractions consumed by hosts:

```python
@dataclass
class WorkflowOrchestrationConfig:
    interval_seconds: int = 15
    completion_glob: str = ".nexus/tasks/nexus/completions/completion_summary_*.json"
    dedupe_cache_size: int = 500
    default_agent_timeout_seconds: int = 3600
    liveness_miss_threshold: int = 3
    timeout_action: str = "retry"
    chaining_enabled: bool = True
    require_completion_comment: bool = True
    block_on_closed_issue: bool = True
    max_retries_per_step: int = 2
    backoff: str = "exponential"
    initial_delay_seconds: float = 1.0
    stale_running_step_action: str = "reconcile"
```

```python
class OrchestrationRuntime(Protocol):
    def load_orchestration_config(self, workflow_id: str) -> WorkflowOrchestrationConfig: ...
    def scan_completions(self, glob_pattern: str) -> list[DetectedCompletion]: ...
    def apply_completion(self, completion: DetectedCompletion) -> bool: ...
    def check_timeouts(self, workflow_id: str) -> list[TimeoutResult]: ...
    def emit_orchestration_event(self, workflow_id: str, event: str, payload: dict) -> None: ...
```

`ProcessOrchestrator` becomes an implementation detail that executes this contract instead of embedding hardcoded behavior.

### 3. Backward Compatibility
- If `schema_version` is absent, treat workflow as v1 and continue using current defaults.
- If `orchestration` block is missing, hydrate `WorkflowOrchestrationConfig` from safe defaults.
- Existing `timeout_seconds` in v1 workflows remains valid and maps to `default_agent_timeout_seconds`.

### 4. Validation Rules
- Reject unknown enum values in `timeout_action`, `backoff`, `stale_running_step_action`.
- Reject non-positive integers for intervals/timeouts/retry counts.
- Reject `completion_glob` values outside the workspace root.
  - For absolute paths, resolve the canonical base path and require `base_path` to be inside the canonical workspace root (`Path.resolve()` + containment check, not string-prefix comparison).
  - For relative paths, reject any traversal (`..`) segments.
- Parse orchestration booleans with strict YAML semantics.
  - Accepted typed values: `true|false` (`bool`) and canonical string forms (`"true"`, `"false"`, `"yes"`, `"no"`, `"on"`, `"off"`, `"1"`, `"0"`), case-insensitive.
  - Invalid boolean tokens must return validation errors; never coerce with Python `bool(<str>)`.
- Dry-run output must include resolved orchestration config plus predicted step flow.

### 5. Contract Clarifications (Post-Review)
- `chaining.enabled`, `chaining.require_completion_comment`, and `chaining.block_on_closed_issue` are policy controls and must remain deterministic across YAML/string input forms.
- `polling.completion_glob` is a security-sensitive field and must be validated with canonical-path containment to prevent sibling-prefix escapes (for example `/tmp/workspace-evil` must not pass for root `/tmp/workspace`).
- Validation failures for boolean parsing and path containment should be surfaced through the same orchestration error channel used by dry-run and loader validation, so host applications receive consistent diagnostics.

## Consequences

### Positive
- Orchestration behavior becomes declarative and versioned, not host-specific.
- Host adapters need less custom glue for polling, timeout, and chaining logic.
- Implementation scope for Developer is explicit (parser + config model + orchestrator wiring + tests).

### Tradeoffs
- Additional schema validation surface area.
- Need to preserve v1 behavior to avoid breaking existing workflow files.

## Implementation Handoff (Developer)
1. Extend workflow YAML loader to parse `schema_version` and `orchestration`.
2. Add `WorkflowOrchestrationConfig` model and defaulting logic.
3. Refactor `ProcessOrchestrator` to consume parsed config values instead of hardcoded constants.
4. Extend dry-run/validation output to include orchestration validation errors.
5. Add tests for:
   - v1 fallback compatibility
   - valid/invalid orchestration enums
   - strict boolean parsing for chaining flags
   - canonical workspace containment checks for absolute completion globs
   - timeout/retry/chaining behavior sourced from YAML
