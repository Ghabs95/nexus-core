"""User management and per-project tracking.

Manages user access and tracks which issues each user is monitoring per project.
Allows users to track different issues across multiple projects (nxs, etc.).

Universal Nexus Identity (UNI): Users are identified by a platform-agnostic
UUID (nexus_id), enabling seamless profile synchronization across Telegram and Discord.
"""
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from config import NEXUS_STATE_DIR

logger = logging.getLogger(__name__)

# User tracking file
USER_DATA_FILE = Path(NEXUS_STATE_DIR) / "user_tracking.json"


@dataclass
class UserProject:
    """Represents a user's tracking for a specific project."""
    project_name: str  # nxs, etc.
    tracked_issues: list[str]  # List of issue numbers as strings
    last_activity: str  # ISO format timestamp


@dataclass
class PlatformIdentity:
    """A platform-specific identity linked to a UNI."""
    platform: str           # "telegram" | "discord"
    platform_user_id: str   # Platform-native ID (str for uniformity)
    linked_at: str          # ISO-8601 timestamp


@dataclass
class User:
    """Represents a Nexus user identified by a platform-agnostic UUID."""
    nexus_id: str                                        # UUID4 (primary key)
    username: str | None
    first_name: str | None
    platform_identities: dict[str, PlatformIdentity]    # platform → PlatformIdentity
    projects: dict[str, UserProject]                     # project_name → UserProject
    created_at: str  # ISO format timestamp
    last_seen: str   # ISO format timestamp


class UserManager:
    """Manages users and their per-project issue tracking."""

    def __init__(self, data_file: Path = USER_DATA_FILE):
        """
        Initialize user manager.

        Args:
            data_file: Path to user data JSON file
        """
        self.data_file = data_file
        self.users: dict[str, User] = {}                          # nexus_id → User
        self._platform_index: dict[tuple[str, str], str] = {}    # (platform, id) → nexus_id
        self.load_users()

    def _rebuild_platform_index(self) -> None:
        self._platform_index = {}
        for user in self.users.values():
            for platform, identity in user.platform_identities.items():
                self._platform_index[(platform, identity.platform_user_id)] = user.nexus_id

    def load_users(self) -> None:
        """Load user data from file, migrating legacy telegram_id records automatically."""
        try:
            if self.data_file.exists():
                with open(self.data_file) as f:
                    data = json.load(f)

                migrated = False
                for key, user_data in data.items():
                    projects = {}
                    for proj_name, proj_data in user_data.get("projects", {}).items():
                        projects[proj_name] = UserProject(
                            project_name=proj_data["project_name"],
                            tracked_issues=proj_data["tracked_issues"],
                            last_activity=proj_data["last_activity"],
                        )

                    # --- Legacy migration: records have telegram_id but no nexus_id ---
                    if "nexus_id" not in user_data:
                        nexus_id = str(uuid.uuid4())
                        telegram_id_str = str(user_data.get("telegram_id", key))
                        platform_identities = {
                            "telegram": PlatformIdentity(
                                platform="telegram",
                                platform_user_id=telegram_id_str,
                                linked_at=user_data.get("created_at", datetime.now().isoformat()),
                            )
                        }
                        migrated = True
                    else:
                        nexus_id = user_data["nexus_id"]
                        platform_identities = {}
                        for platform, pi_data in user_data.get("platform_identities", {}).items():
                            platform_identities[platform] = PlatformIdentity(
                                platform=pi_data["platform"],
                                platform_user_id=pi_data["platform_user_id"],
                                linked_at=pi_data["linked_at"],
                            )

                    self.users[nexus_id] = User(
                        nexus_id=nexus_id,
                        username=user_data.get("username"),
                        first_name=user_data.get("first_name"),
                        platform_identities=platform_identities,
                        projects=projects,
                        created_at=user_data.get("created_at", datetime.now().isoformat()),
                        last_seen=user_data.get("last_seen", datetime.now().isoformat()),
                    )

                self._rebuild_platform_index()
                logger.info(f"Loaded {len(self.users)} users from {self.data_file}")

                if migrated:
                    logger.info("Migrated legacy telegram_id records to UNI schema; re-saving.")
                    self.save_users()
            else:
                logger.info("No existing user data file, starting fresh")

        except Exception as e:
            logger.error(f"Error loading user data: {e}")
            self.users = {}
            self._platform_index = {}

    def save_users(self) -> None:
        """Save user data to file atomically."""
        try:
            data = {}
            for nexus_id, user in self.users.items():
                platform_identities_dict = {}
                for platform, identity in user.platform_identities.items():
                    platform_identities_dict[platform] = asdict(identity)

                projects_dict = {}
                for proj_name, proj in user.projects.items():
                    projects_dict[proj_name] = asdict(proj)

                data[nexus_id] = {
                    "nexus_id": user.nexus_id,
                    "username": user.username,
                    "first_name": user.first_name,
                    "platform_identities": platform_identities_dict,
                    "projects": projects_dict,
                    "created_at": user.created_at,
                    "last_seen": user.last_seen,
                }

            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.data_file.with_suffix(".tmp")
            with open(tmp_path, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.data_file)

            logger.debug(f"Saved {len(self.users)} users to {self.data_file}")

        except Exception as e:
            logger.error(f"Error saving user data: {e}")

    def get_or_create_user_by_platform(
        self,
        platform: str,
        platform_user_id: str,
        username: str | None = None,
        first_name: str | None = None,
    ) -> User:
        """
        Resolve or create a user by platform identity.

        Args:
            platform: Platform name ("telegram" or "discord")
            platform_user_id: Platform-native user ID (as string)
            username: Optional username hint
            first_name: Optional first-name hint

        Returns:
            User object with a stable nexus_id
        """
        now = datetime.now().isoformat()
        key = (platform, platform_user_id)

        if key in self._platform_index:
            nexus_id = self._platform_index[key]
            user = self.users[nexus_id]
            user.last_seen = now
            if username:
                user.username = username
            if first_name:
                user.first_name = first_name
        else:
            nexus_id = str(uuid.uuid4())
            user = User(
                nexus_id=nexus_id,
                username=username,
                first_name=first_name,
                platform_identities={
                    platform: PlatformIdentity(
                        platform=platform,
                        platform_user_id=platform_user_id,
                        linked_at=now,
                    )
                },
                projects={},
                created_at=now,
                last_seen=now,
            )
            self.users[nexus_id] = user
            self._platform_index[key] = nexus_id
            logger.info(f"Created new user nexus_id={nexus_id} platform={platform} id={platform_user_id}")

        self.save_users()
        return user

    def get_user_by_nexus_id(self, nexus_id: str) -> User | None:
        """Return user by UNI, or None if not found."""
        return self.users.get(nexus_id)

    def link_platform(
        self,
        nexus_id: str,
        platform: str,
        platform_user_id: str,
    ) -> bool:
        """
        Attach an additional platform account to an existing UNI.

        Returns:
            True if linked, False if user not found
        """
        user = self.users.get(nexus_id)
        if not user:
            return False

        now = datetime.now().isoformat()
        user.platform_identities[platform] = PlatformIdentity(
            platform=platform,
            platform_user_id=platform_user_id,
            linked_at=now,
        )
        self._platform_index[(platform, platform_user_id)] = nexus_id
        self.save_users()
        logger.info(f"Linked {platform}/{platform_user_id} → nexus_id={nexus_id}")
        return True

    def unlink_platform(self, nexus_id: str, platform: str) -> bool:
        """
        Detach a platform account from a UNI.

        Returns:
            True if unlinked, False if user/platform not found
        """
        user = self.users.get(nexus_id)
        if not user or platform not in user.platform_identities:
            return False

        identity = user.platform_identities.pop(platform)
        self._platform_index.pop((platform, identity.platform_user_id), None)
        self.save_users()
        logger.info(f"Unlinked {platform} from nexus_id={nexus_id}")
        return True

    def track_issue(
        self,
        nexus_id: str,
        project: str,
        issue_number: str,
    ) -> None:
        """
        Track an issue for a user in a specific project.

        Args:
            nexus_id: Universal Nexus Identity (UUID)
            project: Project name (nxs, etc.)
            issue_number: GitHub issue number
        """
        user = self.users.get(nexus_id)
        if not user:
            logger.warning(f"track_issue: nexus_id={nexus_id} not found")
            return

        if project not in user.projects:
            user.projects[project] = UserProject(
                project_name=project,
                tracked_issues=[],
                last_activity=datetime.now().isoformat(),
            )

        project_data = user.projects[project]
        if issue_number not in project_data.tracked_issues:
            project_data.tracked_issues.append(issue_number)
            project_data.last_activity = datetime.now().isoformat()
            logger.info(f"User nexus_id={nexus_id} now tracking {project}#{issue_number}")
            self.save_users()

    def untrack_issue(
        self,
        nexus_id: str,
        project: str,
        issue_number: str,
    ) -> bool:
        """
        Stop tracking an issue for a user in a specific project.

        Args:
            nexus_id: Universal Nexus Identity (UUID)
            project: Project name
            issue_number: GitHub issue number

        Returns:
            True if issue was untracked, False if it wasn't being tracked
        """
        user = self.users.get(nexus_id)
        if not user:
            return False

        if project not in user.projects:
            return False

        project_data = user.projects[project]
        if issue_number in project_data.tracked_issues:
            project_data.tracked_issues.remove(issue_number)
            project_data.last_activity = datetime.now().isoformat()
            logger.info(f"User nexus_id={nexus_id} stopped tracking {project}#{issue_number}")
            self.save_users()
            return True

        return False

    def get_user_tracked_issues(
        self,
        nexus_id: str,
        project: str | None = None,
    ) -> dict[str, list[str]]:
        """
        Get all issues tracked by a user.

        Args:
            nexus_id: Universal Nexus Identity (UUID)
            project: Optional project filter

        Returns:
            Dict mapping project names to lists of issue numbers
        """
        user = self.users.get(nexus_id)
        if not user:
            return {}

        if project:
            if project in user.projects:
                return {project: user.projects[project].tracked_issues}
            return {}

        return {
            proj_name: proj_data.tracked_issues
            for proj_name, proj_data in user.projects.items()
            if proj_data.tracked_issues
        }

    def get_issue_trackers(
        self,
        project: str,
        issue_number: str,
    ) -> list[str]:
        """
        Get all UNIs tracking a specific issue.

        Args:
            project: Project name
            issue_number: GitHub issue number

        Returns:
            List of nexus_id (UUID) strings
        """
        return [
            user.nexus_id
            for user in self.users.values()
            if project in user.projects
            and issue_number in user.projects[project].tracked_issues
        ]

    def get_user_stats(self, nexus_id: str) -> dict:
        """
        Get statistics for a user.

        Args:
            nexus_id: Universal Nexus Identity (UUID)

        Returns:
            Dict with user statistics
        """
        user = self.users.get(nexus_id)
        if not user:
            return {"exists": False}

        total_issues = sum(len(proj.tracked_issues) for proj in user.projects.values())

        return {
            "exists": True,
            "nexus_id": user.nexus_id,
            "username": user.username,
            "first_name": user.first_name,
            "platforms": list(user.platform_identities.keys()),
            "projects": list(user.projects.keys()),
            "total_tracked_issues": total_issues,
            "created_at": user.created_at,
            "last_seen": user.last_seen,
        }

    def get_all_users_stats(self) -> dict:
        """
        Get statistics for all users.

        Returns:
            Dict with overall statistics
        """
        total_projects: set[str] = set()
        total_tracked = 0

        for user in self.users.values():
            total_projects.update(user.projects.keys())
            total_tracked += sum(len(proj.tracked_issues) for proj in user.projects.values())

        return {
            "total_users": len(self.users),
            "total_projects": len(total_projects),
            "projects": sorted(total_projects),
            "total_tracked_issues": total_tracked,
        }


# Global singleton
_user_manager: UserManager | None = None


def get_user_manager() -> UserManager:
    """Get the global UserManager instance."""
    global _user_manager
    if _user_manager is None:
        _user_manager = UserManager()
    return _user_manager
