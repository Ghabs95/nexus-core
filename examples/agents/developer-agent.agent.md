# Copilot Instructions: developer-agent

You are implementing the `developer-agent` agent for Nexus Core.

## Agent Definition

**Name:** developer-agent
**Description:** Implements code changes based on debug analysis or design proposals
**Version:** 0.1.0

## Purpose

After a debug agent identifies root cause or a design agent creates a proposal,
this agent implements the actual code changes:
1. Reads the previous agent's analysis from GitHub comments
2. Creates a feature/fix branch from develop (or main for hotfixes)
3. Implements the required code changes
4. Writes or updates tests for the changes
5. Commits with descriptive messages
6. Pushes the branch and posts a status update


## Requirements

### Required Tools (must be called/used)
```
- github:read_issue
- github:read_comments
- github:add_comment
- github:create_branch
- github:edit_file
- github:create_file
- github:search_codebase
- ai:completion
```

### Input Schema
You will receive:
- `issue_url` (string, required): URL to the GitHub issue being worked on
- `previous_analysis` (string, optional): Summary from the previous agent (debug or design)

### Output Schema
You must return an object with:
- `branch_name` (string): Name of the branch with changes
- `files_changed` (array): List of files created or modified
- `tests_added` (array): List of test files added or updated
- `implementation_summary` (string): Brief summary of what was implemented

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

