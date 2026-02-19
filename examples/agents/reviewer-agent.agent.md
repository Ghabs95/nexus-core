# Copilot Instructions: reviewer-agent

You are implementing the `reviewer-agent` agent for Nexus Core.

## Agent Definition

**Name:** reviewer-agent
**Description:** Reviews pull requests for code quality and best practices
**Version:** 0.1.0

## Purpose

When a PR is submitted, this agent:
1. Reads the PR diff
2. Analyzes code quality, style, and best practices
3. Checks for security issues
4. Suggests improvements
5. Posts review comments
6. Approves or requests changes


## Requirements

### Required Tools (must be called/used)
```
- github:read_pr
- github:review_pr
- github:suggest_changes
- github:add_comment
- github:approve_pr
- ai:completion
```

### Input Schema
You will receive:
- `pr_url` (string, required): URL to pull request
- `review_depth` (enum, optional): Review depth level

### Output Schema
You must return an object with:
- `review_comments` (array): List of review comments with file/line context
- `approval_status` (enum): Final review decision
- `security_issues` (array): Security concerns found
- `code_quality_score` (integer): Overall code quality score (0-100)

## AI Instructions

When calling the LLM, use this prompt:

```

```

## Implementation Notes

1. Follow the Nexus Core async/await patterns
2. Add proper error handling and retries
3. Include type hints for all parameters
4. Write docstrings for public methods
5. Add logging at key decision points

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

