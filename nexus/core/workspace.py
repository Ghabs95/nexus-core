"""
Core module for managing isolated workspaces via Git worktrees.
"""

import logging
import os
import subprocess

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """Manages Git worktree creation and cleanup for isolated agent execution."""

    @staticmethod
    def provision_worktree(base_repo_path: str, issue_number: str) -> str:
        """
        Provision an isolated Git worktree for the given issue.
        
        This isolates the agent's work into a specific branch without polluting 
        the main project checkout, guaranteeing that concurrent agents don't conflict.
        
        Args:
            base_repo_path: The absolute path to the main git repository checkout.
            issue_number: The GitHub issue number (used to generate branch/folder names).
            
        Returns:
            The absolute path to the provisioned worktree directory.
        """
        issue_number_str = str(issue_number).strip()
        worktree_dir = os.path.join(base_repo_path, ".nexus", "worktrees", f"issue-{issue_number_str}")
        branch_name = f"feature/issue-{issue_number_str}"

        logger.info(f"Provisioning worktree for issue {issue_number_str} at {worktree_dir}")

        # Check if the worktree already exists and is valid
        if os.path.isdir(worktree_dir):
            if os.path.exists(os.path.join(worktree_dir, ".git")):
                logger.info(f"Worktree for issue {issue_number_str} already exists. Reusing.")
                return worktree_dir
            else:
                logger.warning(f"Invalid worktree found at {worktree_dir} (missing .git). Cleaning up.")
                import shutil
                shutil.rmtree(worktree_dir, ignore_errors=True)

        os.makedirs(os.path.dirname(worktree_dir), exist_ok=True)

        env = os.environ.copy()
        
        # Check if the branch already exists locally or remotely
        branch_exists_locally = False
        try:
            result = subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
                cwd=base_repo_path,
                env=env,
            )
            branch_exists_locally = (result.returncode == 0)
        except Exception:
            pass

        try:
            if branch_exists_locally:
                # Add worktree using the existing branch
                subprocess.run(
                    ["git", "worktree", "add", worktree_dir, branch_name],
                    cwd=base_repo_path,
                    check=True,
                    capture_output=True,
                    text=True,
                    env=env,
                )
                logger.info(f"Adding worktree using existing branch {branch_name}")
            else:
                # Add worktree and create the branch simultaneously
                subprocess.run(
                    ["git", "worktree", "add", "-b", branch_name, worktree_dir],
                    cwd=base_repo_path,
                    check=True,
                    capture_output=True,
                    text=True,
                    env=env,
                )
                logger.info(f"Adding worktree and creating new branch {branch_name}")
                
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to provision worktree: {e.stderr}")
            # Fallback to base repo path
            return base_repo_path
            
        return worktree_dir

    @staticmethod
    def cleanup_worktree(base_repo_path: str, issue_number: str) -> bool:
        """
        Remove the isolated Git worktree for the given issue.
        
        Args:
            base_repo_path: The absolute path to the main git repository checkout.
            issue_number: The GitHub issue number.
            
        Returns:
            True if cleanup was successful or skipped, False if an error occurred.
        """
        issue_number_str = str(issue_number).strip()
        worktree_dir = os.path.join(base_repo_path, ".nexus", "worktrees", f"issue-{issue_number_str}")
        
        if not os.path.exists(worktree_dir):
            return True
            
        logger.info(f"Cleaning up worktree for issue {issue_number_str} at {worktree_dir}")
        
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", worktree_dir],
                cwd=base_repo_path,
                check=True,
                capture_output=True,
                text=True,
            )
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to remove worktree: {e.stderr}")
            return False
