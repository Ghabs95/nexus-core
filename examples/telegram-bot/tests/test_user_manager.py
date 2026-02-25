"""Tests for user_manager module."""


from user_manager import UserManager


class TestUserManager:
    """Tests for UserManager class."""
    
    def test_initialization(self, tmp_path):
        """Test UserManager initialization."""
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)
        
        assert manager.data_file == data_file
        assert manager.users == {}
    
    def test_create_new_user(self, tmp_path):
        """Test creating a new user."""
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)
        
        user = manager.get_or_create_user(
            telegram_id=12345,
            username="testuser",
            first_name="Test"
        )
        
        assert user.telegram_id == 12345
        assert user.username == "testuser"
        assert user.first_name == "Test"
        assert user.projects == {}
        assert 12345 in manager.users
    
    def test_get_existing_user(self, tmp_path):
        """Test getting an existing user updates last_seen."""
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)
        
        # Create user
        user1 = manager.get_or_create_user(12345, "user1", "User")
        first_seen = user1.last_seen
        
        # Get same user
        user2 = manager.get_or_create_user(12345, "user1_updated")
        
        assert user1 is user2  # Same object
        assert user2.username == "user1_updated"
        assert user2.last_seen > first_seen  # Updated timestamp
    
    def test_track_issue(self, tmp_path):
        """Test tracking an issue for a user."""
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)
        
        manager.track_issue(
            telegram_id=12345,
            project="proj_a",
            issue_number="123",
            username="testuser"
        )
        
        user = manager.users[12345]
        assert "proj_a" in user.projects
        assert "123" in user.projects["proj_a"].tracked_issues
    
    def test_track_multiple_issues_per_project(self, tmp_path):
        """Test tracking multiple issues in same project."""
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)
        
        manager.track_issue(12345, "proj_a", "123")
        manager.track_issue(12345, "proj_a", "456")
        manager.track_issue(12345, "proj_a", "789")
        
        user = manager.users[12345]
        assert len(user.projects["proj_a"].tracked_issues) == 3
        assert "123" in user.projects["proj_a"].tracked_issues
        assert "456" in user.projects["proj_a"].tracked_issues
        assert "789" in user.projects["proj_a"].tracked_issues
    
    def test_track_issues_across_multiple_projects(self, tmp_path):
        """Test tracking issues across different projects."""
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)
        
        manager.track_issue(12345, "proj_a", "123")
        manager.track_issue(12345, "proj_b", "456")
        manager.track_issue(12345, "proj_c", "789")
        
        user = manager.users[12345]
        assert len(user.projects) == 3
        assert "proj_a" in user.projects
        assert "proj_b" in user.projects
        assert "proj_c" in user.projects
    
    def test_track_duplicate_issue_ignored(self, tmp_path):
        """Test tracking same issue twice doesn't create duplicates."""
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)
        
        manager.track_issue(12345, "proj_a", "123")
        manager.track_issue(12345, "proj_a", "123")
        manager.track_issue(12345, "proj_a", "123")
        
        user = manager.users[12345]
        assert len(user.projects["proj_a"].tracked_issues) == 1
    
    def test_untrack_issue(self, tmp_path):
        """Test untracking an issue."""
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)
        
        manager.track_issue(12345, "proj_a", "123")
        assert "123" in manager.users[12345].projects["proj_a"].tracked_issues
        
        result = manager.untrack_issue(12345, "proj_a", "123")
        assert result is True
        assert "123" not in manager.users[12345].projects["proj_a"].tracked_issues
    
    def test_untrack_nonexistent_issue(self, tmp_path):
        """Test untracking an issue that wasn't tracked."""
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)
        
        result = manager.untrack_issue(12345, "proj_a", "999")
        assert result is False
    
    def test_get_user_tracked_issues_all_projects(self, tmp_path):
        """Test getting all tracked issues for a user."""
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)
        
        manager.track_issue(12345, "proj_a", "123")
        manager.track_issue(12345, "proj_a", "456")
        manager.track_issue(12345, "proj_b", "789")
        
        tracked = manager.get_user_tracked_issues(12345)
        
        assert len(tracked) == 2
        assert "proj_a" in tracked
        assert "proj_b" in tracked
        assert tracked["proj_a"] == ["123", "456"]
        assert tracked["proj_b"] == ["789"]
    
    def test_get_user_tracked_issues_specific_project(self, tmp_path):
        """Test getting tracked issues for specific project."""
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)
        
        manager.track_issue(12345, "proj_a", "123")
        manager.track_issue(12345, "proj_b", "789")
        
        tracked = manager.get_user_tracked_issues(12345, project="proj_a")
        
        assert len(tracked) == 1
        assert "proj_a" in tracked
        assert "proj_b" not in tracked
    
    def test_get_issue_trackers(self, tmp_path):
        """Test getting all users tracking an issue."""
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)
        
        manager.track_issue(111, "proj_a", "123")
        manager.track_issue(222, "proj_a", "123")
        manager.track_issue(333, "proj_a", "456")
        
        trackers = manager.get_issue_trackers("proj_a", "123")
        
        assert len(trackers) == 2
        assert 111 in trackers
        assert 222 in trackers
        assert 333 not in trackers
    
    def test_get_user_stats(self, tmp_path):
        """Test getting user statistics."""
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)
        
        manager.track_issue(12345, "proj_a", "123", username="testuser", first_name="Test")
        manager.track_issue(12345, "proj_a", "456")
        manager.track_issue(12345, "proj_b", "789")
        
        stats = manager.get_user_stats(12345)
        
        assert stats['exists'] is True
        assert stats['username'] == "testuser"
        assert stats['first_name'] == "Test"
        assert len(stats['projects']) == 2
        assert stats['total_tracked_issues'] == 3
    
    def test_get_nonexistent_user_stats(self, tmp_path):
        """Test getting stats for user that doesn't exist."""
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)
        
        stats = manager.get_user_stats(99999)
        assert stats['exists'] is False
    
    def test_get_all_users_stats(self, tmp_path):
        """Test getting overall statistics."""
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)
        
        manager.track_issue(111, "proj_a", "123")
        manager.track_issue(222, "proj_b", "456")
        manager.track_issue(333, "proj_c", "789")
        manager.track_issue(333, "proj_a", "999")
        
        stats = manager.get_all_users_stats()
        
        assert stats['total_users'] == 3
        assert stats['total_projects'] == 3
        assert set(stats['projects']) == {'proj_a', 'proj_b', 'proj_c'}
        assert stats['total_tracked_issues'] == 4
    
    def test_save_and_load_users(self, tmp_path):
        """Test persisting and loading user data."""
        data_file = tmp_path / "users.json"
        
        # Create and populate manager
        manager1 = UserManager(data_file)
        manager1.track_issue(12345, "proj_a", "123", username="user1")
        manager1.track_issue(67890, "proj_b", "456", username="user2")
        
        # Create new manager from same file
        manager2 = UserManager(data_file)
        
        assert len(manager2.users) == 2
        assert 12345 in manager2.users
        assert 67890 in manager2.users
        assert manager2.users[12345].username == "user1"
        assert "123" in manager2.users[12345].projects["proj_a"].tracked_issues
    
    def test_multi_user_isolation(self, tmp_path):
        """Test that different users' tracking is isolated."""
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)
        
        manager.track_issue(111, "proj_a", "123")
        manager.track_issue(222, "proj_a", "456")
        
        user1_issues = manager.get_user_tracked_issues(111)
        user2_issues = manager.get_user_tracked_issues(222)
        
        assert user1_issues["proj_a"] == ["123"]
        assert user2_issues["proj_a"] == ["456"]
