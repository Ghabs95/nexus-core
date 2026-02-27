# Refactor Testing Conventions

When extracting code from large files:

1. Add a direct unit test for the extracted module/service/helper.
2. Keep a wiring smoke test for the original entrypoint (or add one if missing).
3. Run targeted regressions for the touched domain.

Examples:

- `tests/test_ai_runtime_policy_helpers.py` + `tests/test_builtin_ai_runtime_plugin.py`
- `examples/telegram-bot/tests/test_*_service.py` + `examples/telegram-bot/tests/test_inbox_processor_dispatch.py`

Avoid preserving legacy behavior in tests when the canonical config/schema changed. Update fixtures/tests to the current
source-of-truth schema.
