# Contributing to Nexus Core

Thank you for your interest in contributing to Nexus Core! We welcome contributions from the community.

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/nexus-core
   cd nexus-core
   ```
3. **Create a virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
4. **Install in development mode**:
   ```bash
   pip install -e ".[dev]"
   ```

## Development Workflow

### 1. Create a Branch

```bash
git checkout -b feature/my-new-feature
# or
git checkout -b fix/bug-description
```

### 2. Make Changes

- Write code following our style guide (see below)
- Add tests for new functionality
- Update documentation as needed

### 3. Run Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov

# Run specific test
pytest tests/test_workflow.py::test_create_workflow
```

### 4. Format and Lint

```bash
# Format code
black .

# Check linting
ruff check .

# Type check
mypy nexus/
```

### 5. Commit Changes

Use conventional commit messages:

```
feat: add PostgreSQL storage adapter
fix: handle timeout in GitHub adapter
docs: update README with new examples
test: add tests for workflow pause/resume
```

### 6. Push and Create PR

```bash
git push origin feature/my-new-feature
```

Then create a Pull Request on GitHub.

## Code Style

- **Black** for formatting (100-char line length)
- **PEP 8** naming conventions
- **Type hints** on all function signatures
- **Docstrings** for public APIs (Google style)

### Example

```python
async def create_workflow(self, workflow: Workflow) -> Workflow:
    """
    Create and persist a new workflow.
    
    Args:
        workflow: Workflow object to create
        
    Returns:
        Created workflow with updated timestamps
        
    Raises:
        ValueError: If workflow ID already exists
    """
    # Implementation
```

## What to Contribute

### Good First Issues

- Implement new storage adapters (Redis, S3)
- Add notification channels (Discord, Email)
- Write example workflows
- Improve documentation
- Add integration tests

### High Priority

- GraphQL API for workflow management
- Web dashboard for monitoring
- Workflow definition from YAML
- Distributed execution (Celery)
- More AI providers (Anthropic API, local models)

### Documentation

- Tutorial: "Build Your First Workflow"
- Guide: "Implementing Custom Adapters"
- API reference improvements
- More code examples

## Testing Guidelines

### Unit Tests

- Test each component in isolation
- Use mocks for external dependencies
- Aim for >80% coverage

```python
import pytest
from nexus.core.workflow import WorkflowEngine

@pytest.mark.asyncio
async def test_create_workflow(mock_storage):
    engine = WorkflowEngine(storage=mock_storage)
    workflow = await engine.create_workflow(sample_workflow)
    assert workflow.state == WorkflowState.PENDING
```

### Integration Tests

- Test adapter implementations with real backends
- Use fixtures for setup/teardown
- Mark with `@pytest.mark.integration`

## Pull Request Guidelines

### Before Submitting

- [ ] Tests pass (`pytest`)
- [ ] Code is formatted (`black .`)
- [ ] Linting passes (`ruff check .`)
- [ ] Type checks pass (`mypy nexus/`)
- [ ] Documentation updated
- [ ] CHANGELOG.md updated (if user-facing change)

### PR Description Template

```markdown
## Description
[Brief description of changes]

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
[How did you test this?]

## Checklist
- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] CHANGELOG.md updated
```

## Code Review Process

1. Maintainer reviews your PR
2. Address feedback if any
3. Once approved, maintainer merges

We try to review PRs within 2-3 days.

## Adapter Implementation Guide

### Creating a New Storage Adapter

1. Create file: `nexus/adapters/storage/{name}.py`
2. Inherit from `StorageBackend`
3. Implement all abstract methods
4. Add tests: `tests/adapters/storage/test_{name}.py`
5. Export in `nexus/adapters/storage/__init__.py`

Example:

```python
from nexus.adapters.storage.base import StorageBackend

class RedisStorage(StorageBackend):
    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        # Initialize Redis client
    
    async def save_workflow(self, workflow: Workflow) -> None:
        # Implementation
```

### Creating a New AI Provider

Follow same pattern but inherit from `AIProvider`:

```python
from nexus.adapters.ai.base import AIProvider

class AnthropicProvider(AIProvider):
    @property
    def name(self) -> str:
        return "anthropic"
    
    async def execute_agent(self, context: ExecutionContext) -> AgentResult:
        # Call Claude API
```

## Community

- **Discord**: [Join our Discord](https://discord.gg/nexus-core)
- **GitHub Discussions**: Ask questions, share ideas
- **Issue Tracker**: Report bugs, request features

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.

---

**Questions?** Open an issue or ask in Discord!
