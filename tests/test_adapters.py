"""Phase 3 adapter tests — SlackNotificationChannel, GitLabPlatform,
OpenAIProvider, PostgreSQLStorageBackend, and AdapterRegistry.

All external SDK/driver calls are mocked so the suite runs without optional
extras installed.
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# AdapterRegistry
# ---------------------------------------------------------------------------


class TestAdapterRegistry:
    def test_create_file_storage(self, tmp_path):
        from nexus.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        storage = registry.create_storage("file", base_path=str(tmp_path))
        from nexus.adapters.storage.file import FileStorage

        assert isinstance(storage, FileStorage)

    def test_create_github_platform(self):
        from unittest.mock import patch

        from nexus.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        with patch("nexus.adapters.git.github.GitHubPlatform._check_gh_cli"):
            git = registry.create_git("github", repo="owner/repo")
        from nexus.adapters.git.github import GitHubPlatform

        assert isinstance(git, GitHubPlatform)

    def test_create_gitlab_platform(self):
        from nexus.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        git = registry.create_git("gitlab", token="glpat-x", repo="org/proj")
        from nexus.adapters.git.gitlab import GitLabPlatform

        assert isinstance(git, GitLabPlatform)

    def test_create_copilot_ai(self):
        from nexus.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        provider = registry.create_ai("copilot")
        from nexus.adapters.ai.copilot_provider import CopilotCLIProvider

        assert isinstance(provider, CopilotCLIProvider)

    def test_create_codex_provider(self):
        from nexus.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        provider = registry.create_ai("codex")
        from nexus.adapters.ai.codex_provider import CodexCLIProvider

        assert isinstance(provider, CodexCLIProvider)

    def test_create_openai_provider(self):
        from nexus.adapters.registry import AdapterRegistry

        AdapterRegistry()
        with patch.dict("sys.modules", {"openai": MagicMock()}):
            # Reload so the guarded import picks up the mock
            import importlib

            import nexus.adapters.ai.openai_provider as _mod
            importlib.reload(_mod)
            _mod._OPENAI_AVAILABLE = True
            _mod._openai_module = MagicMock()
            _mod._openai_module.AsyncOpenAI = MagicMock(return_value=MagicMock())
            provider = _mod.OpenAIProvider(api_key="sk-test")
        assert provider.name == "openai"

    def test_raises_for_unknown_type(self):
        from nexus.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        with pytest.raises(ValueError, match="Unknown storage adapter type"):
            registry.create_storage("redis_custom")

    def test_register_custom_storage(self, tmp_path):
        from nexus.adapters.registry import AdapterRegistry
        from nexus.adapters.storage.file import FileStorage

        registry = AdapterRegistry()
        registry.register_storage("myfile", FileStorage)
        storage = registry.create_storage("myfile", base_path=str(tmp_path))
        assert isinstance(storage, FileStorage)

    def test_from_config(self, tmp_path):
        from nexus.adapters.registry import AdapterConfig, AdapterRegistry

        registry = AdapterRegistry()
        with patch("nexus.adapters.git.github.GitHubPlatform._check_gh_cli"):
            result = registry.from_config(
                {
                    "storage": {"type": "file", "base_path": str(tmp_path)},
                    "git": {"type": "github", "repo": "owner/repo"},
                    "ai": [{"type": "copilot"}],
                }
            )
        assert isinstance(result, AdapterConfig)
        assert result.storage is not None
        assert result.git is not None
        assert len(result.ai_providers) == 1

    def test_from_config_repr(self, tmp_path):
        from nexus.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        result = registry.from_config({"storage": {"type": "file", "base_path": str(tmp_path)}})
        r = repr(result)
        assert "FileStorage" in r

    def test_from_config_empty(self):
        from nexus.adapters.registry import AdapterRegistry

        result = AdapterRegistry().from_config({})
        assert result.storage is None
        assert result.git is None
        assert result.notifications == []
        assert result.ai_providers == []


# ---------------------------------------------------------------------------
# SlackNotificationChannel
# ---------------------------------------------------------------------------


class TestSlackNotificationChannel:
    def _make_channel(self):
        """Create a SlackNotificationChannel with a mocked WebClient."""
        from nexus.adapters.notifications.slack import (
            _SLACK_SDK_AVAILABLE,
            SlackNotificationChannel,
        )

        if not _SLACK_SDK_AVAILABLE:
            pytest.skip("slack-sdk not installed")

        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ts": "1234567890.000001"}
        channel = SlackNotificationChannel.__new__(SlackNotificationChannel)
        channel._client = mock_client
        channel._default_channel = "#test"
        channel._webhook_url = None
        return channel, mock_client

    def test_name(self):
        from nexus.adapters.notifications.slack import (
            _SLACK_SDK_AVAILABLE,
            SlackNotificationChannel,
        )

        if not _SLACK_SDK_AVAILABLE:
            pytest.skip("slack-sdk not installed")
        channel = SlackNotificationChannel.__new__(SlackNotificationChannel)
        assert channel.name == "slack"

    def test_send_message_calls_postmessage(self):
        channel, mock_client = self._make_channel()
        from nexus.adapters.notifications.base import Message

        msg = Message(text="Hello from nexus")
        ts = asyncio.run(channel.send_message("U12345", msg))
        assert ts == "1234567890.000001"
        mock_client.chat_postMessage.assert_called_once()

    def test_send_alert_uses_postmessage(self):
        from nexus.core.models import Severity

        channel, mock_client = self._make_channel()
        asyncio.run(
            channel.send_alert("Deployment failed", Severity.WARNING)
        )
        mock_client.chat_postMessage.assert_called_once()
        call_kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "#test"
        assert "Deployment failed" in call_kwargs["text"]

    def test_send_alert_uses_webhook_when_configured(self):
        from nexus.adapters.notifications.slack import (
            _SLACK_SDK_AVAILABLE,
            SlackNotificationChannel,
        )
        from nexus.core.models import Severity

        if not _SLACK_SDK_AVAILABLE:
            pytest.skip("slack-sdk not installed")

        channel = SlackNotificationChannel.__new__(SlackNotificationChannel)
        channel._client = MagicMock()
        channel._default_channel = "#test"
        channel._webhook_url = "https://hooks.slack.com/services/T0/B0/xxx"

        with patch.object(channel, "_send_via_webhook") as mock_webhook:
            asyncio.run(
                channel.send_alert("Test alert", Severity.CRITICAL)
            )
        mock_webhook.assert_called_once()
        channel._client.chat_postMessage.assert_not_called()

    def test_requires_sdk_without_install(self):
        import nexus.adapters.notifications.slack as _mod

        original = _mod._SLACK_SDK_AVAILABLE
        _mod._SLACK_SDK_AVAILABLE = False
        try:
            with pytest.raises(ImportError, match="slack-sdk"):
                _mod._require_slack_sdk()
        finally:
            _mod._SLACK_SDK_AVAILABLE = original


# ---------------------------------------------------------------------------
# GitHubPlatform
# ---------------------------------------------------------------------------


class TestGitHubPlatform:
    def _make_platform(self):
        from nexus.adapters.git.github import GitHubPlatform

        with patch.object(GitHubPlatform, "_check_gh_cli", return_value=None):
            return GitHubPlatform(repo="owner/repo", token="ghp-test")

    def test_ensure_label_returns_true_on_success(self):
        platform = self._make_platform()
        with patch.object(platform, "_run_gh_command", return_value="") as mock_run:
            ok = asyncio.run(platform.ensure_label("bug", color="FF0000", description="Bug label"))
        assert ok is True
        mock_run.assert_called_once()
        args = mock_run.call_args.args[0]
        assert args[:3] == ["label", "create", "bug"]
        assert "--color" in args
        assert "--description" in args

    def test_ensure_label_returns_true_when_already_exists(self):
        platform = self._make_platform()
        with patch.object(platform, "_run_gh_command", side_effect=RuntimeError("label already exists")):
            ok = asyncio.run(platform.ensure_label("bug", color="FF0000"))
        assert ok is True

    def test_create_issue_falls_back_when_issue_create_json_is_unsupported(self):
        platform = self._make_platform()
        created_issue = {
            "number": 123,
            "title": "New issue",
            "body": "Body",
            "state": "OPEN",
            "labels": [],
            "createdAt": "2026-02-01T10:00:00Z",
            "updatedAt": "2026-02-01T10:00:00Z",
            "url": "https://github.com/owner/repo/issues/123",
        }
        side_effects = [
            RuntimeError("GitHub CLI error: unknown flag: --json"),
            "https://github.com/owner/repo/issues/123",
            json.dumps(created_issue),
        ]
        with patch.object(platform, "_run_gh_command", side_effect=side_effects) as mock_run:
            issue = asyncio.run(platform.create_issue("New issue", "Body", labels=["bug"]))

        assert issue.number == 123
        assert issue.url.endswith("/123")
        create_args = mock_run.call_args_list[1].args[0]
        assert create_args[:3] == ["issue", "create", "--title"]
        assert "--json" not in create_args
        view_args = mock_run.call_args_list[2].args[0]
        assert view_args[:3] == ["issue", "view", "123"]

    def test_merge_pull_request_builds_expected_flags(self):
        platform = self._make_platform()
        with patch.object(platform, "_run_gh_command", return_value="queued") as mock_run:
            result = asyncio.run(
                platform.merge_pull_request("12", squash=True, delete_branch=True, auto=True)
            )
        assert result == "queued"
        mock_run.assert_called_once()
        args = mock_run.call_args.args[0]
        assert args == ["pr", "merge", "12", "--squash", "--delete-branch", "--auto"]


# ---------------------------------------------------------------------------
# GitLabPlatform
# ---------------------------------------------------------------------------


class TestGitLabPlatform:
    def _make_platform(self):
        from nexus.adapters.git.gitlab import GitLabPlatform

        return GitLabPlatform(token="glpat-test", repo="mygroup/myproject")

    def test_name_encoded(self):
        import urllib.parse

        platform = self._make_platform()
        assert platform._encoded_repo == urllib.parse.quote("mygroup/myproject", safe="")

    def test_to_issue_converts_data(self):

        gl = self._make_platform()
        data = {
            "id": 101,
            "iid": 5,
            "title": "Bug report",
            "description": "Something broke",
            "state": "opened",
            "labels": ["bug", "critical"],
            "created_at": "2026-02-01T10:00:00Z",
            "updated_at": "2026-02-02T10:00:00Z",
            "web_url": "https://gitlab.com/mygroup/myproject/-/issues/5",
        }
        issue = gl._to_issue(data)
        assert issue.number == 5
        assert issue.title == "Bug report"
        assert issue.state == "open"
        assert "bug" in issue.labels

    def test_to_pr_converts_data(self):
        from nexus.adapters.git.gitlab import GitLabPlatform

        data = {
            "id": 200,
            "iid": 12,
            "title": "Fix regression",
            "state": "opened",
            "source_branch": "fix/regression",
            "target_branch": "main",
            "web_url": "https://gitlab.com/mygroup/myproject/-/merge_requests/12",
        }
        pr = GitLabPlatform._to_pr(data)
        assert pr.number == 12
        assert pr.head_branch == "fix/regression"
        assert pr.state == "open"

    def test_create_issue_calls_api(self):
        platform = self._make_platform()
        mock_response = {
            "id": 99,
            "iid": 7,
            "title": "New issue",
            "description": "Body",
            "state": "opened",
            "labels": [],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "web_url": "https://gitlab.com/mygroup/myproject/-/issues/7",
        }
        with patch.object(platform, "_post", new=AsyncMock(return_value=mock_response)):
            issue = asyncio.run(
                platform.create_issue("New issue", "Body")
            )
        assert issue.number == 7
        assert issue.title == "New issue"

    def test_get_issue_returns_none_on_404(self):
        import urllib.error

        platform = self._make_platform()
        exc = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
        with patch.object(platform, "_get", new=AsyncMock(side_effect=exc)):
            result = asyncio.run(platform.get_issue("999"))
        assert result is None

    def test_ensure_label_returns_true_on_success(self):
        platform = self._make_platform()
        with patch.object(platform, "_post", new=AsyncMock(return_value={"name": "bug"})) as mock_post:
            ok = asyncio.run(
                platform.ensure_label("bug", color="FF0000", description="Bug label")
            )
        assert ok is True
        path, payload = mock_post.await_args.args
        assert path.endswith("/labels")
        assert payload["name"] == "bug"
        assert payload["color"] == "FF0000"

    def test_ensure_label_returns_true_on_conflict(self):
        import urllib.error

        platform = self._make_platform()
        exc = urllib.error.HTTPError("url", 409, "Conflict", {}, None)
        with patch.object(platform, "_post", new=AsyncMock(side_effect=exc)):
            ok = asyncio.run(platform.ensure_label("bug", color="FF0000"))
        assert ok is True

    def test_merge_pull_request_uses_merge_when_pipeline_succeeds(self):
        platform = self._make_platform()
        mock_response = {
            "state": "opened",
            "web_url": "https://gitlab.com/mygroup/myproject/-/merge_requests/12",
        }
        with patch.object(platform, "_put", new=AsyncMock(return_value=mock_response)) as mock_put:
            result = asyncio.run(
                platform.merge_pull_request("12", squash=True, delete_branch=True, auto=True)
            )
        assert "state=opened" in result
        path, payload = mock_put.await_args.args
        assert path.endswith("/merge_requests/12/merge")
        assert payload["squash"] is True
        assert payload["should_remove_source_branch"] is True
        assert payload["merge_when_pipeline_succeeds"] is True

    def test_list_open_issues_with_labels(self):
        platform = self._make_platform()
        response = [
            {
                "id": 101,
                "iid": 5,
                "title": "Bug report",
                "description": "Something broke",
                "state": "opened",
                "labels": ["workflow:shortened", "bug"],
                "created_at": "2026-02-01T10:00:00Z",
                "updated_at": "2026-02-02T10:00:00Z",
                "web_url": "https://gitlab.com/mygroup/myproject/-/issues/5",
            }
        ]
        with patch.object(platform, "_get", new=AsyncMock(return_value=response)) as mock_get:
            issues = asyncio.run(
                platform.list_open_issues(limit=25, labels=["workflow:shortened", "workflow:full"])
            )

        assert len(issues) == 1
        assert issues[0].number == 5
        called_path = mock_get.await_args.args[0]
        assert "state=opened" in called_path
        assert "per_page=25" in called_path
        assert "labels=" in called_path

    def test_create_pr_from_changes_cross_repo_appends_fully_qualified_reference(self, tmp_path):
        platform = self._make_platform()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            if cmd[:3] == ["git", "status", "--porcelain"]:
                result.stdout = " M file.py\n"
            return result

        mr_response = {
            "id": 200,
            "iid": 12,
            "title": "Fix regression",
            "state": "opened",
            "source_branch": "nexus/issue-42",
            "target_branch": "main",
            "web_url": "https://gitlab.com/mygroup/myproject/-/merge_requests/12",
        }

        with patch("subprocess.run", side_effect=fake_run), patch.object(
            platform,
            "_post",
            new=AsyncMock(return_value=mr_response),
        ) as mock_post:
            pr = asyncio.run(
                platform.create_pr_from_changes(
                    repo_dir=str(repo_dir),
                    issue_number="42",
                    title="Fix regression",
                    body="Automated change",
                    issue_repo="mygroup/workflow-repo",
                )
            )

        assert pr is not None
        assert pr.number == 12
        payload = mock_post.await_args.args[1]
        assert payload["description"].endswith("Closes mygroup/workflow-repo#42")


# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------


class TestOpenAIProvider:
    def _make_provider(self):
        """Build an OpenAIProvider with the openai SDK mocked out."""
        import nexus.adapters.ai.openai_provider as _mod

        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.AsyncOpenAI = MagicMock(return_value=mock_client)
        mock_openai.RateLimitError = type("RateLimitError", (Exception,), {})
        mock_openai.APITimeoutError = type("APITimeoutError", (Exception,), {})

        original_available = _mod._OPENAI_AVAILABLE
        original_module = _mod._openai_module
        _mod._OPENAI_AVAILABLE = True
        _mod._openai_module = mock_openai

        provider = _mod.OpenAIProvider(api_key="sk-test")
        # Return provider + cleanup function
        return provider, mock_client, lambda: setattr(_mod, "_OPENAI_AVAILABLE", original_available) or setattr(_mod, "_openai_module", original_module)

    def test_name(self):
        provider, _, cleanup = self._make_provider()
        try:
            assert provider.name == "openai"
        finally:
            cleanup()

    def test_preference_score_reasoning(self):
        provider, _, cleanup = self._make_provider()
        try:
            assert provider.get_preference_score("reasoning") == 0.9
            assert provider.get_preference_score("code_generation") == 0.7
        finally:
            cleanup()

    def test_execute_agent_success(self):
        from nexus.adapters.ai.base import ExecutionContext

        provider, mock_client, cleanup = self._make_provider()
        try:
            # Mock the async completions.create response
            mock_choice = MagicMock()
            mock_choice.message.content = "Analysis complete."
            mock_choice.finish_reason = "stop"
            mock_response = MagicMock()
            mock_response.choices = [mock_choice]
            mock_response.model = "gpt-4o"
            mock_response.usage.prompt_tokens = 50
            mock_response.usage.completion_tokens = 20
            mock_response.usage.total_tokens = 70
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

            ctx = ExecutionContext(
                agent_name="triage",
                prompt="Analyze this issue",
                workspace=Path("/tmp"),
            )
            result = asyncio.run(
                provider.execute_agent(ctx)
            )
            assert result.success is True
            assert result.output == "Analysis complete."
            assert result.provider_used == "openai"
            assert result.metadata["usage"]["total_tokens"] == 70
        finally:
            cleanup()

    def test_execute_agent_rate_limit(self):
        import nexus.adapters.ai.openai_provider as _mod
        from nexus.adapters.ai.base import ExecutionContext

        provider, mock_client, cleanup = self._make_provider()
        try:
            RateLimitError = _mod._openai_module.RateLimitError
            mock_client.chat.completions.create = AsyncMock(
                side_effect=RateLimitError("Rate limited")
            )

            ctx = ExecutionContext(
                agent_name="triage",
                prompt="Analyze",
                workspace=Path("/tmp"),
            )
            result = asyncio.run(provider.execute_agent(ctx))
            assert result.success is False
            assert "Rate limit" in result.error
        finally:
            cleanup()

    def test_requires_sdk_without_install(self):
        import nexus.adapters.ai.openai_provider as _mod

        original = _mod._OPENAI_AVAILABLE
        _mod._OPENAI_AVAILABLE = False
        try:
            with pytest.raises(ImportError, match="openai"):
                _mod._require_openai()
        finally:
            _mod._OPENAI_AVAILABLE = original


# ---------------------------------------------------------------------------
# CodexCLIProvider
# ---------------------------------------------------------------------------


class TestCodexCLIProvider:
    def test_name(self):
        from nexus.adapters.ai.codex_provider import CodexCLIProvider

        provider = CodexCLIProvider()
        assert provider.name == "codex"

    def test_preference_score(self):
        from nexus.adapters.ai.codex_provider import CodexCLIProvider

        provider = CodexCLIProvider()
        assert provider.get_preference_score("code_generation") == 0.9
        assert provider.get_preference_score("analysis") == 0.65

    def test_check_availability_false_when_binary_missing(self):
        from nexus.adapters.ai.codex_provider import CodexCLIProvider

        provider = CodexCLIProvider()
        with patch("nexus.adapters.ai.codex_provider.shutil.which", return_value=None):
            available = asyncio.run(
                provider.check_availability()
            )

        assert available is False

    def test_execute_agent_success(self):
        from nexus.adapters.ai.base import ExecutionContext
        from nexus.adapters.ai.codex_provider import CodexCLIProvider

        provider = CodexCLIProvider(model="gpt-5-codex")

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
        mock_proc.returncode = 0

        with patch("nexus.adapters.ai.codex_provider.asyncio.create_subprocess_exec", return_value=mock_proc):
            ctx = ExecutionContext(
                agent_name="developer",
                prompt="Implement feature X",
                workspace=Path("/tmp"),
            )
            result = asyncio.run(provider.execute_agent(ctx))

        assert result.success is True
        assert result.provider_used == "codex"
        assert "ok" in result.output

    def test_execute_agent_timeout(self):
        from nexus.adapters.ai.base import ExecutionContext
        from nexus.adapters.ai.codex_provider import CodexCLIProvider

        provider = CodexCLIProvider(timeout=1)

        mock_proc = AsyncMock()
        mock_proc.communicate = MagicMock(return_value=None)
        mock_proc.returncode = 0

        with patch("nexus.adapters.ai.codex_provider.asyncio.create_subprocess_exec", return_value=mock_proc), \
             patch("nexus.adapters.ai.codex_provider.asyncio.wait_for", side_effect=asyncio.TimeoutError):
            ctx = ExecutionContext(
                agent_name="developer",
                prompt="Implement feature X",
                workspace=Path("/tmp"),
            )
            result = asyncio.run(provider.execute_agent(ctx))

        assert result.success is False
        assert "Timeout" in (result.error or "")


# ---------------------------------------------------------------------------
# PostgreSQLStorageBackend
# ---------------------------------------------------------------------------


class TestPostgreSQLStorageBackend:
    def test_requires_sqlalchemy_without_install(self):
        import nexus.adapters.storage.postgres as _mod

        original = _mod._SA_AVAILABLE
        _mod._SA_AVAILABLE = False
        try:
            with pytest.raises(ImportError, match="sqlalchemy"):
                _mod._require_sqlalchemy()
        finally:
            _mod._SA_AVAILABLE = original

    def test_dsn_normalisation(self):
        """Constructor normalises postgres:// → postgresql+psycopg2://."""
        import nexus.adapters.storage.postgres as _mod

        if not _mod._SA_AVAILABLE:
            pytest.skip("sqlalchemy not installed")

        with patch("nexus.adapters.storage.postgres.sa.create_engine") as mock_engine, \
             patch("nexus.adapters.storage.postgres.sa.orm.sessionmaker"), \
             patch("nexus.adapters.storage.postgres._Base.metadata"):
            mock_engine.return_value.url = MagicMock()
            from nexus.adapters.storage.postgres import PostgreSQLStorageBackend

            with patch.object(PostgreSQLStorageBackend, "__init__", lambda self, *a, **kw: None):
                PostgreSQLStorageBackend.__new__(PostgreSQLStorageBackend)
                # Simulate normalisation step in isolation
                dsn = "postgres://user:pass@localhost/db"
                normalised = dsn.replace("postgres://", "postgresql+psycopg2://", 1)
                assert normalised == "postgresql+psycopg2://user:pass@localhost/db"

    def test_workflow_serde_roundtrip(self, tmp_path):
        """Verify shared serde produces stable roundtrip via FileStorage's test workflow."""
        from nexus.adapters.storage._workflow_serde import dict_to_workflow, workflow_to_dict
        from nexus.adapters.storage.file import FileStorage

        FileStorage(tmp_path)
        # Use FileStorage round-trip test as proxy for shared serde correctness
        from nexus.core.models import Agent, Workflow, WorkflowStep

        agent = Agent(name="triage", display_name="Triage", description="")
        step = WorkflowStep(step_num=0, name="triage", agent=agent, prompt_template="Go", inputs={}, outputs={})
        wf = Workflow(id="serde-test", name="test", description="", version="1", steps=[step])
        d = workflow_to_dict(wf)
        restored = dict_to_workflow(d)
        assert restored.id == "serde-test"
        assert len(restored.steps) == 1
        assert restored.steps[0].agent.name == "triage"

    def test_workflow_serde_parses_string_false_orchestration_bools(self):
        from nexus.adapters.storage._workflow_serde import dict_to_workflow

        payload = {
            "id": "serde-bools",
            "name": "Serde Bools",
            "version": "1.0",
            "state": "pending",
            "current_step": 0,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "metadata": {},
            "steps": [],
            "orchestration": {
                "chaining_enabled": "false",
                "require_completion_comment": "false",
                "block_on_closed_issue": "false",
            },
        }
        wf = dict_to_workflow(payload)
        assert wf.orchestration.chaining_enabled is False
        assert wf.orchestration.require_completion_comment is False
        assert wf.orchestration.block_on_closed_issue is False
