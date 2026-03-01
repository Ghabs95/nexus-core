# Refactor Guardrails

This document defines lightweight guardrails for large-file refactors. The goal is to keep orchestration files thin and
move business logic into testable services/modules.

## Scope

Primary hotspots:

- `examples/nexus-bot/src/inbox_processor.py`
- `examples/nexus-bot/src/telegram_bot.py`
- `examples/nexus-bot/src/webhook_server.py`
- `nexus/core/workflow.py`
- `nexus/plugins/builtin/ai_runtime_plugin.py`

## Budgets (Targets)

These are targets, not hard blockers by default:

- Wiring/orchestrator modules: `< 800` LOC
- Handler functions: `< 120` lines (temporary ceiling `150`)
- Service functions: `< 80` lines (except orchestration wrappers)
- Core orchestration methods (`WorkflowEngine.complete_step`, `AIOrchestrator.invoke_agent`): `< 100` lines

## Boundary Rules

- `inbox_processor.py`, `telegram_bot.py`, `webhook_server.py` are wiring/orchestration modules.
- Do not add new business logic to those files when a service/module boundary already exists.
- Pass dependencies explicitly into services; avoid hidden globals in extracted modules.
- Keep provider-specific CLI logic in provider invokers, not in orchestration façades.

## Test Conventions for Refactors

For every extraction:

1. Add direct unit tests for the new module/service.
2. Keep or add a wiring smoke test for the original entrypoint file.
3. Run targeted regression tests for the touched area.

Examples in this repo:

- `examples/nexus-bot/tests/test_*_service.py` (direct service tests)
- `examples/nexus-bot/tests/test_inbox_processor_dispatch.py` (wiring smoke test)
- `tests/test_ai_runtime_policy_helpers.py` (core helper extraction regression coverage)

## Hotspot Check Script

Use:

```bash
./venv/bin/python tools/check_hotspots.py
```

Optional strict mode:

```bash
./venv/bin/python tools/check_hotspots.py --fail-on-targets
```

Default mode is non-blocking and prints warnings only.
