"""User management and per-project tracking.

Manages user access and tracks which issues each user is monitoring per project.
Allows users to track different issues across multiple projects (nxs, etc.).
"""
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from config import DATA_DIR

logger = logging.getLogger(__name__)

# User tracking file
USER_DATA_FILE = Path(DATA_DIR) / "user_tracking.json"


@dataclass
class UserProject:
    """Represents a user's tracking for a specific project."""
    project_name: str  # nxs, etc.
    tracked_issues: list[str]  # List of issue numbers as strings
    last_activity: str  # ISO format timestamp
    

@dataclass
class User:
    """Represents a Nexus user."""
    telegram_id: int
    username: str | None
    first_name: str | None
    projects: dict[str, UserProject]  # project_name -> UserProject
    created_at: str  # ISO format timestamp
    last_seen: str  # ISO format timestamp
    

class UserManager:
    """Manages users and their per-project issue tracking."""
    
    def __init__(self, data_file: Path = USER_DATA_FILE):
        """
        Initialize user manager.
        
        Args:
            data_file: Path to user data JSON file
        """
        self.data_file = data_file
        self.users: dict[int, User] = {}
        self.load_users()
    
    def load_users(self) -> None:
        """Load user data from file."""
        try:
            if self.data_file.exists():
                with open(self.data_file) as f:
                    data = json.load(f)
                    
                for user_id_str, user_data in data.items():
                    user_id = int(user_id_str)
                    
                    # Convert projects dict
                    projects = {}
                    for proj_name, proj_data in user_data.get('projects', {}).items():
                        projects[proj_name] = UserProject(
                            project_name=proj_data['project_name'],
                            tracked_issues=proj_data['tracked_issues'],
                            last_activity=proj_data['last_activity']
                        )
                    
                    self.users[user_id] = User(
                        telegram_id=user_data['telegram_id'],
                        username=user_data.get('username'),
                        first_name=user_data.get('first_name'),
                        projects=projects,
                        created_at=user_data['created_at'],
                        last_seen=user_data['last_seen']
                    )
                
                logger.info(f"Loaded {len(self.users)} users from {self.data_file}")
            else:
                logger.info("No existing user data file, starting fresh")
        
        except Exception as e:
            logger.error(f"Error loading user data: {e}")
            self.users = {}
    
    def save_users(self) -> None:
        """Save user data to file."""
        try:
            # Convert to JSON-serializable format
            data = {}
            for user_id, user in self.users.items():
                projects_dict = {}
                for proj_name, proj in user.projects.items():
                    projects_dict[proj_name] = asdict(proj)
                
                data[str(user_id)] = {
                    'telegram_id': user.telegram_id,
                    'username': user.username,
                    'first_name': user.first_name,
                    'projects': projects_dict,
                    'created_at': user.created_at,
                    'last_seen': user.last_seen
                }
            
            # Ensure directory exists
            self.data_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Write to file
            with open(self.data_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            logger.debug(f"Saved {len(self.users)} users to {self.data_file}")
        
        except Exception as e:
            logger.error(f"Error saving user data: {e}")
    
    def get_or_create_user(
        self,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None
    ) -> User:
        """
        Get existing user or create new one.
        
        Args:
            telegram_id: Telegram user ID
            username: Telegram username
            first_name: User's first name
        
        Returns:
            User object
        """
        now = datetime.now().isoformat()
        
        if telegram_id in self.users:
            # Update user info
            user = self.users[telegram_id]
            user.last_seen = now
            if username:
                user.username = username
            if first_name:
                user.first_name = first_name
        else:
            # Create new user
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                projects={},
                created_at=now,
                last_seen=now
            )
            self.users[telegram_id] = user
            logger.info(f"Created new user: {telegram_id} (@{username})")
        
        self.save_users()
        return user
    
    def track_issue(
        self,
        telegram_id: int,
        project: str,
        issue_number: str,
        username: str | None = None,
        first_name: str | None = None
    ) -> None:
        """
        Track an issue for a user in a specific project.
        
        Args:
            telegram_id: Telegram user ID
            project: Project name (nxs, etc.)
            issue_number: GitHub issue number
            username: Telegram username
            first_name: User's first name
        """
        user = self.get_or_create_user(telegram_id, username, first_name)
        
        # Get or create project tracking
        if project not in user.projects:
            user.projects[project] = UserProject(
                project_name=project,
                tracked_issues=[],
                last_activity=datetime.now().isoformat()
            )
        
        # Add issue if not already tracked
        project_data = user.projects[project]
        if issue_number not in project_data.tracked_issues:
            project_data.tracked_issues.append(issue_number)
            project_data.last_activity = datetime.now().isoformat()
            logger.info(f"User {telegram_id} now tracking {project}#{issue_number}")
            self.save_users()
    
    def untrack_issue(
        self,
        telegram_id: int,
        project: str,
        issue_number: str
    ) -> bool:
        """
        Stop tracking an issue for a user in a specific project.
        
        Args:
            telegram_id: Telegram user ID
            project: Project name
            issue_number: GitHub issue number
        
        Returns:
            True if issue was untracked, False if it wasn't being tracked
        """
        if telegram_id not in self.users:
            return False
        
        user = self.users[telegram_id]
        
        if project not in user.projects:
            return False
        
        project_data = user.projects[project]
        if issue_number in project_data.tracked_issues:
            project_data.tracked_issues.remove(issue_number)
            project_data.last_activity = datetime.now().isoformat()
            logger.info(f"User {telegram_id} stopped tracking {project}#{issue_number}")
            self.save_users()
            return True
        
        return False
    
    def get_user_tracked_issues(
        self,
        telegram_id: int,
        project: str | None = None
    ) -> dict[str, list[str]]:
        """
        Get all issues tracked by a user.
        
        Args:
            telegram_id: Telegram user ID
            project: Optional project filter
        
        Returns:
            Dict mapping project names to lists of issue numbers
        """
        if telegram_id not in self.users:
            return {}
        
        user = self.users[telegram_id]
        
        if project:
            # Return specific project
            if project in user.projects:
                return {project: user.projects[project].tracked_issues}
            return {}
        
        # Return all projects
        result = {}
        for proj_name, proj_data in user.projects.items():
            if proj_data.tracked_issues:
                result[proj_name] = proj_data.tracked_issues
        
        return result
    
    def get_issue_trackers(
        self,
        project: str,
        issue_number: str
    ) -> list[int]:
        """
        Get all users tracking a specific issue.
        
        Args:
            project: Project name
            issue_number: GitHub issue number
        
        Returns:
            List of Telegram user IDs
        """
        trackers = []
        
        for user_id, user in self.users.items():
            if project in user.projects:
                if issue_number in user.projects[project].tracked_issues:
                    trackers.append(user_id)
        
        return trackers
    
    def get_user_stats(self, telegram_id: int) -> dict:
        """
        Get statistics for a user.
        
        Args:
            telegram_id: Telegram user ID
        
        Returns:
            Dict with user statistics
        """
        if telegram_id not in self.users:
            return {
                'exists': False
            }
        
        user = self.users[telegram_id]
        
        total_issues = sum(
            len(proj.tracked_issues)
            for proj in user.projects.values()
        )
        
        return {
            'exists': True,
            'username': user.username,
            'first_name': user.first_name,
            'projects': list(user.projects.keys()),
            'total_tracked_issues': total_issues,
            'created_at': user.created_at,
            'last_seen': user.last_seen
        }
    
    def get_all_users_stats(self) -> dict:
        """
        Get statistics for all users.
        
        Returns:
            Dict with overall statistics
        """
        total_users = len(self.users)
        total_projects = set()
        total_tracked = 0
        
        for user in self.users.values():
            total_projects.update(user.projects.keys())
            total_tracked += sum(
                len(proj.tracked_issues)
                for proj in user.projects.values()
            )
        
        return {
            'total_users': total_users,
            'total_projects': len(total_projects),
            'projects': sorted(total_projects),
            'total_tracked_issues': total_tracked
        }


# Global singleton
_user_manager: UserManager | None = None


def get_user_manager() -> UserManager:
    """Get the global UserManager instance."""
    global _user_manager
    if _user_manager is None:
        _user_manager = UserManager()
    return _user_manager
