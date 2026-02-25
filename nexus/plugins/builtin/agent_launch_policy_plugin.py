"""Built-in plugin: Agent launch prompt composition policy."""

import os
import re

from nexus.core.completion import generate_completion_instructions
from nexus.core.workflow import WorkflowDefinition


class AgentLaunchPolicyPlugin:
    """Compose workflow/agent prompts for launch and continuation flows."""

    def build_agent_prompt(
        self,
        *,
        issue_url: str,
        tier_name: str,
        task_content: str,
        agent_type: str,
        continuation: bool = False,
        continuation_prompt: str | None = None,
        workflow_path: str = "",
        nexus_dir: str = ".nexus",
        project_name: str = "",
    ) -> str:
        """Build launch prompt used by orchestrator agent invocation."""
        workflow_type = WorkflowDefinition.normalize_workflow_type(tier_name)
        instructions = self._get_comment_and_summary_instructions(
            issue_url=issue_url,
            agent_type=agent_type,
            workflow_path=workflow_path,
            workflow_type=workflow_type,
            nexus_dir=nexus_dir,
            project_name=project_name,
        )

        if continuation:
            if continuation_prompt and continuation_prompt.startswith("You are @"):
                return (
                    f"{continuation_prompt}\n\n"
                    f"Issue: {issue_url}\n"
                    f"Tier: {tier_name}\n"
                    f"Workflow Tier: {workflow_type}\n\n"
                    f"Review the previous work in the GitHub comments and task file, then complete your step.\n\n"
                    f"**GIT WORKFLOW (CRITICAL):**\n"
                    f"1. Check the issue body for **Target Branch** field (e.g., `feat/new-feature`)\n"
                    f"2. Identify the correct sub-repo within the workspace "
                    f"(e.g., backend-service, web-app, worker-service)\n"
                    f"3. In that sub-repo: \n"
                    f"   - For feat/fix/chore: create branch from `develop`: "
                    f"`git checkout develop && git pull && git checkout -b <branch-name>`\n"
                    f"   - For hotfix: create branch from `main`: "
                    f"`git checkout main && git pull && git checkout -b <branch-name>`\n"
                    f"4. Make your changes and commit with descriptive messages\n"
                    f"5. Push the branch: `git push -u origin <branch-name>`\n"
                    f"6. Include branch name in your GitHub comment "
                    f"(e.g., 'Pushed to feat/new-feature in backend-service')\n\n"
                    f"⛔ **GIT SAFETY RULES (STRICT):**\n"
                    f"❌ NEVER push to protected branches: `main`, `develop`, `master`, `test`, `staging`, "
                    f"`production`\n"
                    f"❌ NEVER delete any branch: No `git branch -d` or `git push --delete`\n"
                    f"✅ ONLY push to the dedicated feature branch specified in **Target Branch** field\n"
                    f"✅ Valid branch prefixes: feat/*, fix/*, hotfix/*, chore/*, refactor/*, docs/*, "
                    f"build/*, ci/*\n"
                    f"⚠️  Violating these rules can break production and cause team disruption\n\n"
                    f"{instructions}\n\n"
                    f"Task context:\n{task_content}"
                )

            base_prompt = continuation_prompt or "Please continue with the next step."
            merge_policy = self._get_merge_policy_block(agent_type)
            return (
                f"You are a {agent_type} agent. You previously started working on this task:\n\n"
                f"Issue: {issue_url}\n"
                f"Tier: {tier_name}\n"
                f"Workflow Tier: {workflow_type}\n\n"
                f"{base_prompt}\n\n"
                f"{merge_policy}"
                f"{instructions}\n\n"
                f"Task content:\n{task_content}"
            )

        return (
            f"You are a {agent_type} agent. A new task has arrived and a GitHub issue has been created.\n\n"
            f"Issue: {issue_url}\n"
            f"Tier: {tier_name}\n"
            f"Workflow Tier: {workflow_type}\n\n"
            f"**YOUR JOB:** Analyze, triage, and route. DO NOT try to implement or invoke other agents.\n\n"
            f"REQUIRED ACTIONS:\n"
            f"1. Read the GitHub issue body and understand the task\n"
            f"2. Analyze the codebase to assess scope and complexity\n"
            f"3. Identify which sub-repo(s) are affected\n"
            f"4. Determine severity (Critical/High/Medium/Low)\n"
            f"5. Determine which agent type should handle it next\n\n"
            f"**DO NOT:**\n"
            f"❌ Read other agent configuration files\n"
            f"❌ Use any 'invoke', 'task', or 'run tool' to start other agents\n"
            f"❌ Try to implement the feature yourself\n\n"
            f"{instructions}\n\n"
            f"Task details:\n{task_content}"
        )

    @staticmethod
    def _get_merge_policy_block(agent_type: str) -> str:
        """Return merge-policy instructions for deployer agents, empty for others."""
        if agent_type not in {"deployer", "ops"}:
            return ""
        return (
            "**⚠️ PR CREATION (REQUIRED if no PR exists):**\n"
            "First check whether a PR already exists for the issue branch:\n"
            "  `gh pr list --repo <REPO> --head <branch> --state open`\n"
            "If none is found, CREATE the PR — this is always allowed:\n"
            "  `gh pr create --title \"<title>\" --body \"<body>\" --base <base> --head <branch> --repo <REPO>`\n"
            "For the title: derive it from the issue title using conventional commits format\n"
            "(e.g. \"feat: add retry logic\" or \"fix: handle nil pointer\" — NOT \"fix: resolve #N\").\n\n"
            "**⛔ PR MERGE POLICY (applies ONLY to merging, not creation):**\n"
            "This project enforces `require_human_merge_approval: always`.\n"
            "You MUST NOT run `gh pr merge` or any merge command.\n"
            "Instead:\n"
            "1. Ensure the PR exists (create it if missing)\n"
            "2. Verify the PR is ready (CI green, reviews approved)\n"
            "3. Post this comment on the issue:\n"
            "   `🚀 Deployment ready. PR requires human review before merge.`\n"
            "4. Do NOT merge. A human will merge after review.\n\n"
        )

    def _get_workflow_steps_for_prompt(
        self,
        *,
        workflow_path: str,
        agent_type: str,
        workflow_type: str,
    ) -> str:
        """Read workflow file and format steps for prompt context."""
        if not workflow_path:
            return ""
        if not os.path.exists(workflow_path):
            return ""

        return WorkflowDefinition.to_prompt_context(
            workflow_path,
            current_agent_type=agent_type,
            workflow_type=workflow_type,
        )

    def _get_comment_and_summary_instructions(
        self,
        *,
        issue_url: str,
        agent_type: str,
        workflow_path: str,
        workflow_type: str,
        nexus_dir: str,
        project_name: str = "",
    ) -> str:
        """Return instructions for GitHub completion comment and summary file."""
        issue_match = re.search(r"/issues/(\d+)", issue_url or "")
        issue_num = issue_match.group(1) if issue_match else "UNKNOWN"

        workflow_steps = self._get_workflow_steps_for_prompt(
            workflow_path=workflow_path,
            agent_type=agent_type,
            workflow_type=workflow_type,
        )

        return generate_completion_instructions(
            issue_number=issue_num,
            agent_type=agent_type,
            workflow_steps_text=workflow_steps,
            nexus_dir=nexus_dir,
            project_name=project_name,
            completion_backend=self.config.get("completion_backend", "filesystem"),
            webhook_url=self.config.get("webhook_url", ""),
        )


def register_plugins(registry) -> None:
    """Register built-in agent launch policy plugin."""
    from nexus.plugins import PluginKind

    registry.register_factory(
        kind=PluginKind.INPUT_ADAPTER,
        name="agent-launch-policy",
        version="0.1.0",
        factory=lambda config: AgentLaunchPolicyPlugin(),
        description="Agent launch prompt composition policy",
    )
