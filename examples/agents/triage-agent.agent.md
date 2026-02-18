# Copilot Instructions: triage-agent

You are implementing the `triage-agent` agent for Nexus Core.

## Agent Definition

**Name:** triage-agent
**Description:** Analyzes GitHub issues and classifies them by type and priority
**Version:** 0.1.0

## Purpose

Reads new GitHub issues, analyzes title/description, and:
1. Classifies as: bug | feature | documentation | support
2. Assigns priority: p0-critical | p1-high | p2-medium | p3-low
3. Suggests appropriate labels and assignee
4. Posts findings as issue comment


## Requirements

### Required Tools (must be called/used)
```
- github:read_issue
- github:add_comment
- github:add_label
- ai:completion
```

### Input Schema
You will receive:
- `issue_url` (string, required): GitHub issue URL
  Example: `https://github.com/Ghabs95/nexus-core/issues/42`

### Output Schema
You must return an object with:
- `classification` (enum): Issue type
- `priority` (enum): Priority level
- `suggested_labels` (array): Recommended GitHub labels
- `reasoning` (string): Why we classified it this way

## AI Instructions

When calling the LLM, use this prompt:

```
Analyze this GitHub issue for the Nexus Core framework.

Issue Title: {issue_title}
Issue Body: {issue_body}

Classify it into ONE category:
- BUG: Something is broken or doesn't work
- FEATURE: New functionality or enhancement request
- DOC: Documentation improvement needed
- SUPPORT: Question or general support

Assign priority (p0=needs immediate fix, p3=nice to have).

Consider:
- Does it affect production functionality? (↑ priority)
- Is it a common pain point? (↑ priority)
- Can users work around it? (↓ priority)

Return JSON:
{
  "classification": "bug|feature|doc|support",
  "priority": "p0-critical|p1-high|p2-medium|p3-low",
  "reasoning": "2-3 sentences explaining your classification",
  "suggested_labels": ["label1", "label2"]
}

```

## Reference Example

### Input
issue_url: https://github.com/Ghabs95/nexus-core/issues/15
### Expected Output
classification: feature
priority: p2-medium
reasoning: Request for PostgreSQL storage adapter. Useful but not blocking core functionality.
suggested_labels:
- enhancement
- adapters/storage
- good-first-issue

## Implementation Notes

1. Follow the Nexus Core async/await patterns
2. Add proper error handling and retries
3. Include type hints for all parameters
4. Write docstrings for public methods
5. Add logging at key decision points

## Routing After Execution

After successful execution:
- If classification == 'bug', then debug-agent
- If classification == 'feature', then design-agent
- If classification == 'doc', then docs-agent
- Default: close_loop

## Testing

After implementation:
1. Write at least 2 unit tests covering different input scenarios
2. Test error conditions (missing inputs, API failures, etc.)
3. Verify output matches the schema
4. Test timeout/retry behavior

## Resources

- Parent workflow: Check `examples/workflows/` for how this agent is used
- Similar agents: See `examples/agents/` for reference implementations
- Framework docs: Check `docs/USAGE.md` for Nexus Core patterns

