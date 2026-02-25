"""Unit tests for rate_limiter module."""

import time


from rate_limiter import RateLimit, RateLimiter, UserQuota


class TestUserQuota:
    """Tests for UserQuota class."""
    
    def test_add_request(self):
        """Test adding requests to quota."""
        quota = UserQuota(user_id=123)
        quota.add_request(1000.0)
        quota.add_request(1001.0)
        
        assert len(quota.timestamps) == 2
        assert quota.timestamps[0] == 1000.0
        assert quota.timestamps[1] == 1001.0
    
    def test_cleanup_old(self):
        """Test cleaning up old timestamps."""
        quota = UserQuota(user_id=123)
        current_time = time.time()
        
        # Add old and recent requests
        quota.add_request(current_time - 100)  # Old
        quota.add_request(current_time - 50)   # Old
        quota.add_request(current_time - 10)   # Recent
        quota.add_request(current_time - 5)    # Recent
        
        quota.cleanup_old(window_seconds=30)
        
        # Only recent requests should remain
        assert len(quota.timestamps) == 2
    
    def test_count_recent(self):
        """Test counting recent requests."""
        quota = UserQuota(user_id=123)
        current_time = time.time()
        
        quota.add_request(current_time - 100)
        quota.add_request(current_time - 10)
        quota.add_request(current_time - 5)
        
        count = quota.count_recent(window_seconds=30)
        assert count == 2  # Only the recent 2


class TestRateLimiter:
    """Tests for RateLimiter class."""
    
    def test_initialization(self):
        """Test rate limiter initialization."""
        limiter = RateLimiter()
        
        assert len(limiter.user_quotas) == 0
        assert limiter.global_quota.user_id == 0
    
    def test_check_limit_allows_first_requests(self):
        """Test that initial requests are allowed."""
        limiter = RateLimiter()
        limit = RateLimit(max_requests=5, window_seconds=60)
        
        for i in range(5):
            allowed, error = limiter.check_limit(123, "test_action", limit)
            assert allowed is True
            assert error is None
            limiter.record_request(123, "test_action")
    
    def test_check_limit_blocks_excess(self):
        """Test that excess requests are blocked."""
        limiter = RateLimiter()
        limit = RateLimit(max_requests=3, window_seconds=60)
        
        # Allow first 3
        for i in range(3):
            allowed, _ = limiter.check_and_record(123, "test_action", limit)
            assert allowed is True
        
        # Block 4th
        allowed, error = limiter.check_limit(123, "test_action", limit)
        assert allowed is False
        assert error is not None
        assert "Rate limit exceeded" in error
    
    def test_sliding_window(self):
        """Test sliding window behavior."""
        limiter = RateLimiter()
        limit = RateLimit(max_requests=2, window_seconds=2)
        
        current_time = time.time()
        
        # Add 2 requests at t=0
        quota = limiter.user_quotas[123]["test"]
        quota.user_id = 123
        quota.add_request(current_time - 3)  # Old, outside window
        quota.add_request(current_time - 1)  # Recent
        quota.add_request(current_time - 0.5)  # Recent
        
        # Should be blocked (2 recent requests)
        allowed, error = limiter.check_limit(123, "test", limit)
        assert allowed is False
        
        # After cleanup, old request removed, should allow 1 more
        time.sleep(2.1)
        allowed, error = limiter.check_limit(123, "test", limit)
        assert allowed is True
    
    def test_get_remaining(self):
        """Test getting remaining quota."""
        limiter = RateLimiter()
        limit = RateLimit(max_requests=5, window_seconds=60)
        limiter.DEFAULT_LIMITS["test"] = limit
        
        # Initially all 5 available
        remaining = limiter.get_remaining(123, "test")
        assert remaining == 5
        
        # After 2 requests, 3 remaining
        limiter.check_and_record(123, "test")
        limiter.check_and_record(123, "test")
        remaining = limiter.get_remaining(123, "test")
        assert remaining == 3
    
    def test_reset_user_specific_action(self):
        """Test resetting specific action for a user."""
        limiter = RateLimiter()
        limit = RateLimit(max_requests=1, window_seconds=60)
        
        # Max out the limit
        limiter.check_and_record(123, "test", limit)
        allowed, _ = limiter.check_limit(123, "test", limit)
        assert allowed is False
        
        # Reset and verify it's allowed again
        limiter.reset_user(123, "test")
        allowed, _ = limiter.check_limit(123, "test", limit)
        assert allowed is True
    
    def test_reset_user_all_actions(self):
        """Test resetting all actions for a user."""
        limiter = RateLimiter()
        limit = RateLimit(max_requests=1, window_seconds=60)
        
        # Max out multiple limits
        limiter.check_and_record(123, "action1", limit)
        limiter.check_and_record(123, "action2", limit)
        
        # Reset all
        limiter.reset_user(123)
        
        # Both should be allowed again
        allowed, _ = limiter.check_limit(123, "action1", limit)
        assert allowed is True
        allowed, _ = limiter.check_limit(123, "action2", limit)
        assert allowed is True
    
    def test_per_user_isolation(self):
        """Test that different users have separate quotas."""
        limiter = RateLimiter()
        limit = RateLimit(max_requests=1, window_seconds=60)
        
        # User 1 maxes out
        limiter.check_and_record(1, "test", limit)
        allowed, _ = limiter.check_limit(1, "test", limit)
        assert allowed is False
        
        # User 2 should still be allowed
        allowed, _ = limiter.check_limit(2, "test", limit)
        assert allowed is True
    
    def test_cleanup_old_data(self):
        """Test cleanup removes old timestamps."""
        limiter = RateLimiter()
        current_time = time.time()
        
        # Add old data
        quota = limiter.user_quotas[123]["test"]
        quota.user_id = 123
        quota.add_request(current_time - 10000)  # Very old
        
        limiter.cleanup_old_data()
        
        # Old data should be removed
        assert len(quota.timestamps) == 0
    
    def test_get_stats(self):
        """Test getting rate limiter statistics."""
        limiter = RateLimiter()
        
        # Add some activity
        limiter.check_and_record(1, "action1")
        limiter.check_and_record(2, "action2")
        
        stats = limiter.get_stats()
        
        assert stats["active_users"] == 2
        assert stats["total_tracked_actions"] == 2
        assert "configured_limits" in stats
    
    def test_state_persistence(self, tmp_path):
        """Test saving and loading state."""
        state_file = tmp_path / "rate_limits.json"
        
        # Create limiter and add some data
        limiter1 = RateLimiter(str(state_file))
        limiter1.check_and_record(123, "test")
        limiter1.save_state()
        
        # Create new limiter and load state
        limiter2 = RateLimiter(str(state_file))
        
        # Should have the saved quota
        assert 123 in limiter2.user_quotas
        assert "test" in limiter2.user_quotas[123]
        assert len(limiter2.user_quotas[123]["test"].timestamps) == 1
    
    def test_default_limits_configured(self):
        """Test that default limits are properly configured."""
        limiter = RateLimiter()
        
        # Check some expected defaults
        assert "user_global" in limiter.DEFAULT_LIMITS
        assert "logs" in limiter.DEFAULT_LIMITS
        assert "stats" in limiter.DEFAULT_LIMITS
        assert "github_api" in limiter.DEFAULT_LIMITS
        
        # Verify they have reasonable values
        assert limiter.DEFAULT_LIMITS["user_global"].max_requests > 0
        assert limiter.DEFAULT_LIMITS["logs"].window_seconds > 0
