# n8n State Machine Bridge

This bridge lets n8n own visible workflow state while Nexus ARC keeps control of
policy and coding execution.

## Runtime Split

- Chat reasoning stays in OpenClaw/Nexus.
- n8n stores and displays workflow state transitions.
- Nexus command bridge exposes authenticated state and coding endpoints.
- OpenCode runs coding work only through the Nexus bridge, in an isolated git
  worktree under an allowlisted repository root.

## Endpoints

All endpoints require the existing command bridge bearer token.

### Create Run

`POST /api/v1/n8n/runs`

```json
{
  "run_id": "optional-stable-id",
  "task": "Implement the agreed task",
  "project_key": "nexus",
  "issue_number": "123",
  "requester": {
    "source": "telegram",
    "sender_id": "47168736"
  }
}
```

### Update Run

`POST /api/v1/n8n/runs/update`

```json
{
  "run_id": "n8n-abc123",
  "state": "approved",
  "message": "Gab approved implementation"
}
```

Allowed states:

`queued`, `reasoning`, `planning`, `approved`, `coding`, `review`, `test`,
`pr`, `done`, `blocked`, `failed`, `cancelled`.

### Get Run

`GET /api/v1/n8n/runs/<run_id>`

### Execute Coding

`POST /api/v1/n8n/coding/execute`

```json
{
  "run_id": "n8n-abc123",
  "task": "Make the requested code change",
  "project_key": "nexus",
  "worker": "opencode",
  "agent": "build",
  "base_branch": "develop",
  "branch": "n8n/n8n-abc123"
}
```

The bridge resolves `project_key` to the configured workspace, creates a git
worktree, then launches:

```bash
opencode run --agent build --format json --dir <worktree> --dangerously-skip-permissions <prompt>
```

OpenCode is instructed not to push, merge, or create PRs. PR creation should be
an explicit later state in the n8n workflow.

## Environment

- `NEXUS_N8N_STATE_DIR`: run records, default `/var/lib/nexus/n8n-state`
- `NEXUS_N8N_WORKTREE_DIR`: isolated worktrees, default `/var/lib/nexus/n8n-worktrees`
- `NEXUS_N8N_LOG_DIR`: OpenCode logs, default `/var/lib/nexus/logs/n8n`
- `NEXUS_N8N_REPO_ALLOWLIST`: colon-separated allowed roots, default `/home/ubuntu/git`
- `NEXUS_OPENCODE_CLI`: OpenCode binary path, default from `PATH`

## n8n Shape

A minimal workflow is:

1. Manual/chat webhook trigger.
2. `POST /api/v1/n8n/runs`.
3. Human approval node.
4. `POST /api/v1/n8n/runs/update` with `approved`.
5. `POST /api/v1/n8n/coding/execute`.
6. Poll `GET /api/v1/n8n/runs/<run_id>` or inspect returned job log.
7. Move to review/test/PR states.

Use n8n credentials for the bearer token. Do not put the token in node bodies.
