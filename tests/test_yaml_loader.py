"""Tests for YamlWorkflowLoader — YAML-based workflow loading with schema validation."""
import pytest

from nexus.core.models import Workflow, WorkflowStep
from nexus.core.yaml_loader import RETRY_BACKOFF_STRATEGIES, YamlWorkflowLoader
from nexus.core.workflow_engine.workflow_definition_loader import validate_orchestration_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_dict(**overrides):
    """Return a minimal valid workflow dict."""
    base = {
        "name": "Test Workflow",
        "steps": [
            {"id": "step1", "name": "Triage", "agent_type": "triage"},
            {"id": "step2", "name": "Develop", "agent_type": "developer", "on_success": "step3"},
            {"id": "step3", "name": "Review", "agent_type": "reviewer", "final_step": True},
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# validate_dict — happy paths
# ---------------------------------------------------------------------------

class TestValidateDictHappy:
    def test_valid_minimal_workflow_no_errors(self):
        errors = YamlWorkflowLoader.validate_dict(_minimal_dict())
        assert errors == []

    def test_valid_workflow_with_condition(self):
        data = _minimal_dict()
        data["steps"][1]["condition"] = "result.get('needs_design') == True"
        errors = YamlWorkflowLoader.validate_dict(data)
        assert errors == []

    def test_valid_retry_policy_exponential(self):
        data = {
            "name": "Retry Test",
            "steps": [
                {
                    "id": "s1",
                    "agent_type": "triage",
                    "retry_policy": {"max_retries": 3, "backoff": "exponential", "initial_delay": 5},
                }
            ],
        }
        errors = YamlWorkflowLoader.validate_dict(data)
        assert errors == []

    def test_valid_retry_policy_all_strategies(self):
        for strategy in RETRY_BACKOFF_STRATEGIES:
            data = {
                "name": "Retry",
                "steps": [{"id": "s1", "agent_type": "triage", "retry_policy": {"backoff": strategy}}],
            }
            errors = YamlWorkflowLoader.validate_dict(data)
            assert errors == [], f"Unexpected errors for backoff={strategy}: {errors}"

    def test_valid_parallel_field(self):
        data = {
            "name": "Parallel Test",
            "steps": [
                {"id": "step1", "agent_type": "triage"},
                {"id": "step2", "agent_type": "developer", "parallel": ["step1"]},
            ],
        }
        errors = YamlWorkflowLoader.validate_dict(data)
        assert errors == []

    def test_tiered_workflow_valid(self):
        data = {
            "name": "Tiered",
            "full_workflow": {
                "steps": [
                    {"id": "t1", "agent_type": "triage"},
                    {"id": "d1", "agent_type": "developer"},
                ]
            },
        }
        errors = YamlWorkflowLoader.validate_dict(data, workflow_type="full")
        assert errors == []


# ---------------------------------------------------------------------------
# validate_dict — error cases
# ---------------------------------------------------------------------------

class TestValidateDictErrors:
    def test_non_dict_input(self):
        errors = YamlWorkflowLoader.validate_dict("not a dict")  # type: ignore[arg-type]
        assert any("mapping" in e for e in errors)

    def test_missing_name_and_id(self):
        data = {"steps": [{"id": "s1", "agent_type": "triage"}]}
        errors = YamlWorkflowLoader.validate_dict(data)
        assert any("name" in e or "id" in e for e in errors)

    def test_empty_steps_returns_error(self):
        data = {"name": "No Steps", "steps": []}
        errors = YamlWorkflowLoader.validate_dict(data)
        assert errors

    def test_missing_agent_type(self):
        data = {"name": "Bad", "steps": [{"id": "s1", "name": "No Type"}]}
        errors = YamlWorkflowLoader.validate_dict(data)
        assert any("agent_type" in e for e in errors)

    def test_invalid_on_success_reference(self):
        data = {
            "name": "Bad Ref",
            "steps": [{"id": "s1", "agent_type": "triage", "on_success": "nonexistent"}],
        }
        errors = YamlWorkflowLoader.validate_dict(data)
        assert any("nonexistent" in e for e in errors)

    def test_malformed_condition_syntax(self):
        data = {
            "name": "Bad Cond",
            "steps": [{"id": "s1", "agent_type": "triage", "condition": "x =="}],
        }
        errors = YamlWorkflowLoader.validate_dict(data)
        assert any("condition" in e for e in errors)

    def test_retry_policy_non_dict(self):
        data = {
            "name": "Bad Retry",
            "steps": [{"id": "s1", "agent_type": "triage", "retry_policy": "3"}],
        }
        errors = YamlWorkflowLoader.validate_dict(data)
        assert any("retry_policy" in e for e in errors)

    def test_retry_policy_negative_max_retries(self):
        data = {
            "name": "Bad Retry",
            "steps": [{"id": "s1", "agent_type": "triage", "retry_policy": {"max_retries": -1}}],
        }
        errors = YamlWorkflowLoader.validate_dict(data)
        assert any("max_retries" in e for e in errors)

    def test_retry_policy_invalid_backoff(self):
        data = {
            "name": "Bad Backoff",
            "steps": [{"id": "s1", "agent_type": "triage", "retry_policy": {"backoff": "random"}}],
        }
        errors = YamlWorkflowLoader.validate_dict(data)
        assert any("backoff" in e for e in errors)

    def test_parallel_non_list(self):
        data = {
            "name": "Bad Parallel",
            "steps": [{"id": "s1", "agent_type": "triage", "parallel": "step2"}],
        }
        errors = YamlWorkflowLoader.validate_dict(data)
        assert any("parallel" in e for e in errors)

    def test_step_not_dict(self):
        data = {"name": "Bad Step", "steps": ["not-a-dict"]}
        errors = YamlWorkflowLoader.validate_dict(data)
        assert errors

    def test_invalid_orchestration_enum_rejected(self):
        data = _minimal_dict(
            orchestration={
                "timeouts": {"timeout_action": "explode"},
            }
        )
        errors = YamlWorkflowLoader.validate_dict(data)
        assert any("timeout_action" in e for e in errors)

    def test_orchestration_string_false_values_parse_as_false(self):
        data = _minimal_dict(
            orchestration={
                "chaining": {
                    "enabled": "false",
                    "require_completion_comment": "false",
                    "block_on_closed_issue": "false",
                }
            }
        )
        wf = YamlWorkflowLoader.load_from_dict(data)
        assert wf.orchestration.chaining_enabled is False
        assert wf.orchestration.require_completion_comment is False
        assert wf.orchestration.block_on_closed_issue is False

    def test_orchestration_absolute_completion_glob_sibling_path_rejected(self):
        data = _minimal_dict(
            orchestration={
                "polling": {
                    "completion_glob": "/tmp/workspace-evil/.nexus/tasks/nexus/completions/*.json",
                }
            }
        )
        errors = validate_orchestration_config(data, workspace_root="/tmp/workspace")
        assert any("must resolve inside workspace root" in error for error in errors)


# ---------------------------------------------------------------------------
# load_from_dict — integration with WorkflowDefinition
# ---------------------------------------------------------------------------

class TestLoadFromDict:
    def test_returns_workflow_instance(self):
        wf = YamlWorkflowLoader.load_from_dict(_minimal_dict())
        assert isinstance(wf, Workflow)

    def test_workflow_name_preserved(self):
        wf = YamlWorkflowLoader.load_from_dict(_minimal_dict())
        assert "Test Workflow" in wf.name

    def test_workflow_steps_count(self):
        wf = YamlWorkflowLoader.load_from_dict(_minimal_dict())
        assert len(wf.steps) == 3

    def test_name_override(self):
        wf = YamlWorkflowLoader.load_from_dict(_minimal_dict(), name_override="Override Name")
        assert wf.name == "Override Name"

    def test_workflow_id_override(self):
        wf = YamlWorkflowLoader.load_from_dict(_minimal_dict(), workflow_id="custom-id")
        assert wf.id == "custom-id"

    def test_schema_error_raises_value_error(self):
        bad = {"steps": [{"id": "s1", "agent_type": "triage"}]}  # missing name/id
        with pytest.raises(ValueError, match="schema validation failed"):
            YamlWorkflowLoader.load_from_dict(bad)

    def test_strict_mode_raises_on_warning(self):
        # Unknown parallel step id → warning
        data = {
            "name": "Parallel Warn",
            "steps": [
                {"id": "s1", "agent_type": "triage", "parallel": ["nonexistent"]},
            ],
        }
        with pytest.raises(ValueError, match="strict mode"):
            YamlWorkflowLoader.load_from_dict(data, strict=True)

    def test_non_strict_mode_proceeds_with_warning(self):
        # Same data should succeed in non-strict mode
        data = {
            "name": "Parallel Warn",
            "steps": [
                {"id": "s1", "agent_type": "triage", "parallel": ["nonexistent"]},
            ],
        }
        wf = YamlWorkflowLoader.load_from_dict(data, strict=False)
        assert isinstance(wf, Workflow)

    def test_v1_timeout_seconds_maps_to_orchestration_default_timeout(self):
        wf = YamlWorkflowLoader.load_from_dict(_minimal_dict(timeout_seconds=1234))
        assert wf.schema_version == "1.0"
        assert wf.orchestration.default_agent_timeout_seconds == 1234

    def test_orchestration_v2_block_parses_into_workflow(self):
        data = _minimal_dict(
            schema_version="2.0",
            orchestration={
                "polling": {
                    "interval_seconds": 21,
                    "completion_glob": ".nexus/tasks/nexus/completions/completion_summary_*.json",
                    "dedupe_cache_size": 50,
                },
                "timeouts": {
                    "default_agent_timeout_seconds": 1800,
                    "liveness_miss_threshold": 2,
                    "timeout_action": "retry",
                },
                "chaining": {
                    "enabled": False,
                    "require_completion_comment": False,
                    "block_on_closed_issue": False,
                },
                "retries": {
                    "max_retries_per_step": 4,
                    "backoff": "linear",
                    "initial_delay_seconds": 2.5,
                },
                "recovery": {"stale_running_step_action": "reconcile"},
            },
        )
        wf = YamlWorkflowLoader.load_from_dict(data)
        assert wf.schema_version == "2.0"
        assert wf.orchestration.interval_seconds == 21
        assert wf.orchestration.backoff == "linear"
        assert wf.orchestration.chaining_enabled is False


# ---------------------------------------------------------------------------
# retry_policy in YAML → WorkflowStep.retry
# ---------------------------------------------------------------------------

class TestRetryPolicyParsing:
    def test_retry_policy_max_retries_mapped_to_step_retry(self):
        data = {
            "name": "Retry Mapping",
            "steps": [
                {
                    "id": "s1",
                    "agent_type": "triage",
                    "retry_policy": {"max_retries": 5, "backoff": "exponential"},
                }
            ],
        }
        wf = YamlWorkflowLoader.load_from_dict(data)
        step = wf.steps[0]
        assert step.retry == 5

    def test_explicit_retry_takes_precedence_over_retry_policy(self):
        data = {
            "name": "Retry Precedence",
            "steps": [
                {
                    "id": "s1",
                    "agent_type": "triage",
                    "retry": 2,
                    "retry_policy": {"max_retries": 10},
                }
            ],
        }
        wf = YamlWorkflowLoader.load_from_dict(data)
        assert wf.steps[0].retry == 2

    def test_no_retry_policy_step_retry_is_none(self):
        wf = YamlWorkflowLoader.load_from_dict(_minimal_dict())
        for step in wf.steps:
            assert step.retry is None


# ---------------------------------------------------------------------------
# parallel field in YAML → WorkflowStep.parallel_with
# ---------------------------------------------------------------------------

class TestParallelFieldParsing:
    def test_parallel_field_populated(self):
        data = {
            "name": "Parallel",
            "steps": [
                {"id": "step1", "agent_type": "triage"},
                {"id": "step2", "agent_type": "developer", "parallel": ["step1"]},
            ],
        }
        wf = YamlWorkflowLoader.load_from_dict(data)
        step2 = wf.steps[1]
        assert "step1" in step2.parallel_with

    def test_no_parallel_field_empty_list(self):
        wf = YamlWorkflowLoader.load_from_dict(_minimal_dict())
        for step in wf.steps:
            assert step.parallel_with == []

    def test_multiple_parallel_ids(self):
        data = {
            "name": "Multi Parallel",
            "steps": [
                {"id": "a", "agent_type": "triage"},
                {"id": "b", "agent_type": "developer"},
                {"id": "c", "agent_type": "reviewer", "parallel": ["a", "b"]},
            ],
        }
        wf = YamlWorkflowLoader.load_from_dict(data)
        step_c = wf.steps[2]
        assert "a" in step_c.parallel_with
        assert "b" in step_c.parallel_with


# ---------------------------------------------------------------------------
# validate (file-based)
# ---------------------------------------------------------------------------

class TestValidateFile:
    def test_missing_file_returns_error(self, tmp_path):
        errors = YamlWorkflowLoader.validate(str(tmp_path / "nonexistent.yaml"))
        assert any("not found" in e.lower() or "File" in e for e in errors)

    def test_valid_yaml_file_returns_no_errors(self, tmp_path):
        import yaml as _yaml
        wf_file = tmp_path / "workflow.yaml"
        wf_file.write_text(_yaml.dump(_minimal_dict()), encoding="utf-8")
        errors = YamlWorkflowLoader.validate(str(wf_file))
        assert errors == []

    def test_invalid_yaml_syntax_returns_error(self, tmp_path):
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("name: test\n  invalid: [unclosed", encoding="utf-8")
        errors = YamlWorkflowLoader.validate(str(bad_file))
        assert errors


# ---------------------------------------------------------------------------
# load (file-based)
# ---------------------------------------------------------------------------

class TestLoadFile:
    def test_load_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            YamlWorkflowLoader.load("/nonexistent/path/workflow.yaml")

    def test_load_valid_file(self, tmp_path):
        import yaml as _yaml
        wf_file = tmp_path / "workflow.yaml"
        wf_file.write_text(_yaml.dump(_minimal_dict()), encoding="utf-8")
        wf = YamlWorkflowLoader.load(str(wf_file))
        assert isinstance(wf, Workflow)

    def test_load_enterprise_workflow_yaml(self):
        """Smoke test against the example enterprise workflow YAML."""
        from pathlib import Path
        yaml_path = (
            Path(__file__).parent.parent
            / "examples"
            / "workflows"
            / "enterprise_workflow.yaml"
        )
        if not yaml_path.exists():
            pytest.skip("Example YAML not found")
        wf = YamlWorkflowLoader.load(str(yaml_path), workflow_type="full")
        assert isinstance(wf, Workflow)
        assert len(wf.steps) > 0


# ---------------------------------------------------------------------------
# retry_policy backoff_strategy and initial_delay stored on WorkflowStep
# ---------------------------------------------------------------------------

class TestRetryPolicyBackoffStorage:
    def test_backoff_strategy_stored_on_step(self):
        data = {
            "name": "Backoff Test",
            "steps": [
                {
                    "id": "s1",
                    "agent_type": "triage",
                    "retry_policy": {"max_retries": 3, "backoff": "linear"},
                }
            ],
        }
        wf = YamlWorkflowLoader.load_from_dict(data)
        assert wf.steps[0].backoff_strategy == "linear"

    def test_initial_delay_stored_on_step(self):
        data = {
            "name": "Delay Test",
            "steps": [
                {
                    "id": "s1",
                    "agent_type": "triage",
                    "retry_policy": {"max_retries": 2, "initial_delay": 5.0},
                }
            ],
        }
        wf = YamlWorkflowLoader.load_from_dict(data)
        assert wf.steps[0].initial_delay == 5.0

    def test_no_retry_policy_defaults(self):
        wf = YamlWorkflowLoader.load_from_dict(_minimal_dict())
        for step in wf.steps:
            assert step.backoff_strategy is None
            assert step.initial_delay == 0.0

    def test_constant_backoff_stored(self):
        data = {
            "name": "Constant Backoff",
            "steps": [
                {
                    "id": "s1",
                    "agent_type": "triage",
                    "retry_policy": {"backoff": "constant", "initial_delay": 10},
                }
            ],
        }
        wf = YamlWorkflowLoader.load_from_dict(data)
        assert wf.steps[0].backoff_strategy == "constant"
        assert wf.steps[0].initial_delay == 10.0


# ---------------------------------------------------------------------------
# get_runnable_steps
# ---------------------------------------------------------------------------

from typing import Any

from nexus.adapters.storage.base import StorageBackend
from nexus.core.models import Agent, AuditEvent, StepStatus, WorkflowState
from nexus.core.workflow import WorkflowEngine


class _InMemoryStorage(StorageBackend):
    def __init__(self) -> None:
        self._workflows: dict[str, Workflow] = {}
        self._audit: list[AuditEvent] = []

    async def save_workflow(self, workflow: Workflow) -> None:
        self._workflows[workflow.id] = workflow

    async def load_workflow(self, workflow_id: str) -> Workflow | None:
        return self._workflows.get(workflow_id)

    async def list_workflows(self, state=None, limit: int = 100):
        return list(self._workflows.values())

    async def delete_workflow(self, workflow_id: str) -> bool:
        return bool(self._workflows.pop(workflow_id, None))

    async def append_audit_event(self, event: AuditEvent) -> None:
        self._audit.append(event)

    async def get_audit_log(self, workflow_id: str, since=None) -> list[AuditEvent]:
        return [e for e in self._audit if e.workflow_id == workflow_id]

    async def save_agent_metadata(self, workflow_id: str, agent_name: str, metadata: dict[str, Any]) -> None:
        pass

    async def get_agent_metadata(self, workflow_id: str, agent_name: str) -> dict[str, Any] | None:
        return None

    async def cleanup_old_workflows(self, older_than_days: int = 30) -> int:
        return 0


def _make_agent(name: str) -> Agent:
    return Agent(name=name, display_name=name, description="test", timeout=60, max_retries=0)


def _make_step(num: int, name: str, parallel_with: list[str] | None = None) -> WorkflowStep:
    return WorkflowStep(
        step_num=num,
        name=name,
        agent=_make_agent(name),
        prompt_template="do work",
        parallel_with=parallel_with or [],
    )


class TestGetRunnableSteps:
    @pytest.mark.asyncio
    async def test_returns_empty_for_nonexistent_workflow(self):
        storage = _InMemoryStorage()
        engine = WorkflowEngine(storage=storage)
        result = await engine.get_runnable_steps("nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_workflow_not_running(self):
        step = _make_step(1, "triage")
        wf = Workflow(id="w1", name="test", version="1.0", steps=[step],
                      state=WorkflowState.PENDING, current_step=1)
        storage = _InMemoryStorage()
        await storage.save_workflow(wf)
        engine = WorkflowEngine(storage=storage)
        result = await engine.get_runnable_steps("w1")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_workflow_completed(self):
        step = _make_step(1, "triage")
        wf = Workflow(id="w1", name="test", version="1.0", steps=[step],
                      state=WorkflowState.COMPLETED, current_step=1)
        storage = _InMemoryStorage()
        await storage.save_workflow(wf)
        engine = WorkflowEngine(storage=storage)
        result = await engine.get_runnable_steps("w1")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_current_pending_step(self):
        step = _make_step(1, "triage")
        wf = Workflow(id="w1", name="test", version="1.0", steps=[step],
                      state=WorkflowState.RUNNING, current_step=1)
        storage = _InMemoryStorage()
        await storage.save_workflow(wf)
        engine = WorkflowEngine(storage=storage)
        result = await engine.get_runnable_steps("w1")
        assert len(result) == 1
        assert result[0].name == "triage"

    @pytest.mark.asyncio
    async def test_does_not_return_already_running_current_step(self):
        step = _make_step(1, "triage")
        step.status = StepStatus.RUNNING
        wf = Workflow(id="w1", name="test", version="1.0", steps=[step],
                      state=WorkflowState.RUNNING, current_step=1)
        storage = _InMemoryStorage()
        await storage.save_workflow(wf)
        engine = WorkflowEngine(storage=storage)
        result = await engine.get_runnable_steps("w1")
        assert result == []

    @pytest.mark.asyncio
    async def test_includes_parallel_pending_steps(self):
        step1 = _make_step(1, "triage")
        step2 = _make_step(2, "developer", parallel_with=["triage"])
        wf = Workflow(id="w1", name="test", version="1.0", steps=[step1, step2],
                      state=WorkflowState.RUNNING, current_step=1)
        storage = _InMemoryStorage()
        await storage.save_workflow(wf)
        engine = WorkflowEngine(storage=storage)
        result = await engine.get_runnable_steps("w1")
        names = {s.name for s in result}
        assert "triage" in names
        assert "developer" in names

    @pytest.mark.asyncio
    async def test_does_not_include_parallel_steps_with_wrong_reference(self):
        step1 = _make_step(1, "triage")
        step2 = _make_step(2, "developer", parallel_with=["reviewer"])  # wrong reference
        wf = Workflow(id="w1", name="test", version="1.0", steps=[step1, step2],
                      state=WorkflowState.RUNNING, current_step=1)
        storage = _InMemoryStorage()
        await storage.save_workflow(wf)
        engine = WorkflowEngine(storage=storage)
        result = await engine.get_runnable_steps("w1")
        assert len(result) == 1
        assert result[0].name == "triage"

    @pytest.mark.asyncio
    async def test_does_not_include_completed_parallel_steps(self):
        step1 = _make_step(1, "triage")
        step2 = _make_step(2, "developer", parallel_with=["triage"])
        step2.status = StepStatus.COMPLETED
        wf = Workflow(id="w1", name="test", version="1.0", steps=[step1, step2],
                      state=WorkflowState.RUNNING, current_step=1)
        storage = _InMemoryStorage()
        await storage.save_workflow(wf)
        engine = WorkflowEngine(storage=storage)
        result = await engine.get_runnable_steps("w1")
        names = {s.name for s in result}
        assert "triage" in names
        assert "developer" not in names

    @pytest.mark.asyncio
    async def test_no_parallel_with_returns_only_current(self):
        step1 = _make_step(1, "triage")
        step2 = _make_step(2, "developer")  # no parallel_with
        wf = Workflow(id="w1", name="test", version="1.0", steps=[step1, step2],
                      state=WorkflowState.RUNNING, current_step=1)
        storage = _InMemoryStorage()
        await storage.save_workflow(wf)
        engine = WorkflowEngine(storage=storage)
        result = await engine.get_runnable_steps("w1")
        assert len(result) == 1
        assert result[0].name == "triage"
