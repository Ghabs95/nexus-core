"""Rate limiting and throttling for Telegram commands and GitHub API calls."""
import json
import logging
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class RateLimit:
    """Rate limit configuration for a specific action."""
    max_requests: int  # Maximum requests allowed
    window_seconds: int  # Time window in seconds
    name: str = ""  # Optional name for logging
    
    def __post_init__(self):
        if not self.name:
            self.name = f"{self.max_requests}/{self.window_seconds}s"


@dataclass
class UserQuota:
    """Track user's request history for rate limiting."""
    user_id: int
    timestamps: deque = field(default_factory=deque)  # Request timestamps
    
    def add_request(self, timestamp: float = None):
        """Record a new request."""
        if timestamp is None:
            timestamp = time.time()
        self.timestamps.append(timestamp)
    
    def cleanup_old(self, window_seconds: int):
        """Remove timestamps outside the sliding window."""
        cutoff = time.time() - window_seconds
        while self.timestamps and self.timestamps[0] < cutoff:
            self.timestamps.popleft()
    
    def count_recent(self, window_seconds: int) -> int:
        """Count requests in the sliding window."""
        self.cleanup_old(window_seconds)
        return len(self.timestamps)


class RateLimiter:
    """
    Sliding window rate limiter for Telegram commands and API calls.
    
    Features:
    - Per-user rate limiting
    - Per-command rate limiting
    - Global rate limiting
    - Persistent state across restarts
    - Automatic cleanup of old data
    """
    
    # Default rate limits (can be overridden)
    DEFAULT_LIMITS = {
        # Per-user limits
        "user_global": RateLimit(30, 60, "30 requests/minute per user"),
        "user_commands": RateLimit(10, 60, "10 commands/minute per user"),
        
        # Per-command limits (stricter for heavy operations)
        "logs": RateLimit(5, 60, "5 log views/minute"),
        "stats": RateLimit(3, 60, "3 stats requests/minute"),
        "direct": RateLimit(3, 300, "3 direct agent calls/5min"),
        "reprocess": RateLimit(2, 300, "2 reprocesses/5min"),
        "implement": RateLimit(5, 300, "5 implementations/5min"),
        
        # GitHub API limits
        "github_api": RateLimit(100, 3600, "100 API calls/hour"),
        "github_issue_create": RateLimit(10, 3600, "10 issue creates/hour"),
    }
    
    def __init__(self, state_file: str | None = None):
        """
        Initialize rate limiter.
        
        Args:
            state_file: Optional path to persist rate limit state
        """
        self.state_file = state_file
        self.user_quotas: dict[int, dict[str, UserQuota]] = defaultdict(
            lambda: defaultdict(lambda: UserQuota(user_id=0))
        )
        self.global_quota = UserQuota(user_id=0)  # For global limits
        
        # Load persisted state if available
        if state_file and os.path.exists(state_file):
            self.load_state()
    
    def check_limit(
        self,
        user_id: int,
        action: str,
        limit: RateLimit | None = None
    ) -> tuple[bool, str | None]:
        """
        Check if a request is allowed under rate limits.
        
        Args:
            user_id: Telegram user ID
            action: Action being performed (e.g., "logs", "stats", "github_api")
            limit: Optional custom rate limit (uses default if not provided)
        
        Returns:
            Tuple of (allowed: bool, error_message: Optional[str])
        """
        if limit is None:
            limit = self.DEFAULT_LIMITS.get(action)
            if limit is None:
                # No limit configured, allow by default
                return (True, None)
        
        # Get or create user quota for this action
        quota = self.user_quotas[user_id][action]
        quota.user_id = user_id
        
        # Count recent requests in the sliding window
        recent_count = quota.count_recent(limit.window_seconds)
        
        if recent_count >= limit.max_requests:
            # Rate limit exceeded
            wait_time = int(limit.window_seconds - (time.time() - quota.timestamps[0]))
            error_msg = (
                f"⏱ Rate limit exceeded for {action}. "
                f"Limit: {limit.name}. "
                f"Try again in {wait_time}s."
            )
            logger.warning(
                f"Rate limit exceeded: user={user_id}, action={action}, "
                f"count={recent_count}/{limit.max_requests}"
            )
            return (False, error_msg)
        
        # Request is allowed
        return (True, None)
    
    def record_request(self, user_id: int, action: str):
        """
        Record a successful request for rate limiting tracking.
        
        Call this AFTER check_limit() returns True and the request succeeds.
        """
        quota = self.user_quotas[user_id][action]
        quota.user_id = user_id
        quota.add_request()
        
        # Also record in global quota if applicable
        if action in ["github_api", "github_issue_create"]:
            self.global_quota.add_request()
        
        # Periodic cleanup (every 100 requests)
        if len(quota.timestamps) % 100 == 0:
            self.cleanup_old_data()
    
    def check_and_record(
        self,
        user_id: int,
        action: str,
        limit: RateLimit | None = None
    ) -> tuple[bool, str | None]:
        """
        Convenience method to check limit and record if allowed.
        
        Returns:
            Tuple of (allowed: bool, error_message: Optional[str])
        """
        allowed, error = self.check_limit(user_id, action, limit)
        if allowed:
            self.record_request(user_id, action)
        return (allowed, error)
    
    def get_remaining(self, user_id: int, action: str) -> int:
        """
        Get remaining requests for a user+action in the current window.
        
        Returns:
            Number of requests remaining (or -1 if no limit configured)
        """
        limit = self.DEFAULT_LIMITS.get(action)
        if limit is None:
            return -1
        
        quota = self.user_quotas[user_id][action]
        recent_count = quota.count_recent(limit.window_seconds)
        return max(0, limit.max_requests - recent_count)
    
    def reset_user(self, user_id: int, action: str | None = None):
        """
        Reset rate limits for a user.
        
        Args:
            user_id: User to reset
            action: Optional specific action to reset (resets all if None)
        """
        if action:
            if user_id in self.user_quotas and action in self.user_quotas[user_id]:
                self.user_quotas[user_id][action] = UserQuota(user_id=user_id)
                logger.info(f"Reset rate limit: user={user_id}, action={action}")
        else:
            self.user_quotas[user_id] = defaultdict(lambda: UserQuota(user_id=user_id))
            logger.info(f"Reset all rate limits for user={user_id}")
    
    def cleanup_old_data(self):
        """Remove old timestamps from all quotas to free memory."""
        # Clean up user quotas
        for user_id in list(self.user_quotas.keys()):
            for action, quota in list(self.user_quotas[user_id].items()):
                limit = self.DEFAULT_LIMITS.get(action)
                if limit:
                    quota.cleanup_old(limit.window_seconds)
                    # Remove empty quotas
                    if not quota.timestamps:
                        del self.user_quotas[user_id][action]
            
            # Remove users with no active quotas
            if not self.user_quotas[user_id]:
                del self.user_quotas[user_id]
        
        # Clean up global quota
        self.global_quota.cleanup_old(3600)  # Keep 1 hour of data
    
    def get_stats(self) -> dict:
        """Get rate limiter statistics."""
        return {
            "active_users": len(self.user_quotas),
            "total_tracked_actions": sum(
                len(actions) for actions in self.user_quotas.values()
            ),
            "global_requests_last_hour": len(self.global_quota.timestamps),
            "configured_limits": len(self.DEFAULT_LIMITS),
        }
    
    def save_state(self):
        """Save rate limiter state to disk for persistence."""
        if not self.state_file:
            return
        
        # Convert to JSON-serializable format
        state = {
            "timestamp": time.time(),
            "user_quotas": {
                str(user_id): {
                    action: list(quota.timestamps)
                    for action, quota in actions.items()
                }
                for user_id, actions in self.user_quotas.items()
            },
            "global_quota": list(self.global_quota.timestamps)
        }
        
        try:
            with open(self.state_file, 'w') as f:
                json.dump(state, f, indent=2)
            logger.debug(f"Saved rate limiter state to {self.state_file}")
        except Exception as e:
            logger.error(f"Failed to save rate limiter state: {e}")
    
    def load_state(self):
        """Load rate limiter state from disk."""
        if not self.state_file or not os.path.exists(self.state_file):
            return
        
        try:
            with open(self.state_file) as f:
                state = json.load(f)
            
            # Restore user quotas
            for user_id_str, actions in state.get("user_quotas", {}).items():
                user_id = int(user_id_str)
                for action, timestamps in actions.items():
                    quota = UserQuota(user_id=user_id)
                    quota.timestamps = deque(timestamps)
                    self.user_quotas[user_id][action] = quota
            
            # Restore global quota
            self.global_quota.timestamps = deque(state.get("global_quota", []))
            
            # Cleanup old data after loading
            self.cleanup_old_data()
            
            logger.info(f"Loaded rate limiter state from {self.state_file}")
        except Exception as e:
            logger.error(f"Failed to load rate limiter state: {e}")


# Global rate limiter instance
rate_limiter = None


def get_rate_limiter(state_file: str | None = None) -> RateLimiter:
    """Get or create global rate limiter instance."""
    global rate_limiter
    if rate_limiter is None:
        if state_file is None:
            # Default location
            from config import DATA_DIR
            state_file = os.path.join(DATA_DIR, "rate_limits.json")
        rate_limiter = RateLimiter(state_file)
    return rate_limiter
