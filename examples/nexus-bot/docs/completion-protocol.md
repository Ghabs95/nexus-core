# Completion Protocol

When an AI agent finishes its work step, it must produce a **completion summary** — a structured report that enables the
orchestrator to post rich GitHub comments and auto-chain to the next agent.

## Two Modes

The mode depends on `NEXUS_STORAGE_BACKEND`:

| Backend      | How agent reports                                         | Where it's stored                     |
|--------------|-----------------------------------------------------------|---------------------------------------|
| `filesystem` | Writes `completion_summary_{issue}.json` to disk          | `.nexus/tasks/{project}/completions/` |
| `postgres`   | POSTs JSON to `http://localhost:{PORT}/api/v1/completion` | `nexus_completions` table             |

## Completion Payload

```json
{
  "status": "complete",
  "summary": "Brief one-line summary of work completed",
  "key_findings": [
    "Finding or result 1",
    "Finding or result 2"
  ],
  "effort_breakdown": {
    "task_name": "duration or effort"
  },
  "verdict": "Assessment of work quality",
  "next_agent": "agent_type_for_next_step"
}
```

### Field Reference

| Field              | Type     | Required | Description                                 |
|--------------------|----------|----------|---------------------------------------------|
| `status`           | string   | ✅        | `complete`, `in-progress`, or `blocked`     |
| `summary`          | string   | ✅        | One-line summary of accomplishment          |
| `key_findings`     | string[] | ❌        | Discoveries, test results, notable findings |
| `effort_breakdown` | object   | ❌        | Key-value pairs of time/effort per task     |
| `verdict`          | string   | ❌        | Quality assessment or readiness statement   |
| `next_agent`       | string   | ❌        | `agent_type` for the next workflow step     |

### Postgres Mode — curl Example

```bash
curl -s -X POST http://localhost:8081/api/v1/completion \
  -H "Content-Type: application/json" \
  -d '{
    "issue_number": "42",
    "agent_type": "developer",
    "status": "complete",
    "summary": "Implemented authentication module",
    "key_findings": ["All 14 tests pass", "No breaking changes"],
    "next_agent": "reviewer"
  }'
```

Response: `201 Created` with `dedup_key`.

### Filesystem Mode — File Write

```bash
COMPLETIONS_DIR=".nexus/tasks/myproject/completions"
mkdir -p "$COMPLETIONS_DIR"

cat > "$COMPLETIONS_DIR/completion_summary_42.json" <<EOF
{
  "status": "complete",
  "summary": "Implemented authentication module",
  "key_findings": ["All 14 tests pass"],
  "next_agent": "reviewer"
}
EOF
```

## Deduplication

When using postgres, completions are deduplicated via a `dedup_key` of format `{issue}:{agent_type}:{status}`. If the
same agent re-submits for the same issue, the existing row is updated (not duplicated).

## Agent Checklists

### Reviewer / QA Agents

Before setting `status: "complete"`:

- Full regression evidence present (CI link or command output)
- Regression status in `key_findings` and/or `verdict`
- If regression fails → do NOT approve; set status to `blocked`

### Implementation Agents

Before setting `status: "complete"`:

- Run syntax/static check (e.g. `python3 -m py_compile`, `npm run lint`, `flutter analyze`)
- Record commands and results in `key_findings`
- If checks fail → set `status: "blocked"` with explanation

## GitHub Comment Format

The orchestrator auto-posts a comment when a completion is detected:

```markdown
### ✅ Agent Completed

**Summary:** Implemented authentication module

**Key Findings:**

- All 14 tests pass
- No breaking changes

**Verdict:** ✅ Implementation complete, ready for review

**Next:** Ready for `@reviewer`

_Automated comment from Nexus._
```
