"""Tests for state_manager module."""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch


sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from state_manager import HostStateManager


class TestTrackedIssues:
    """Tests for tracked issues persistence."""
    
    def test_load_tracked_issues_empty_file(self):
        """Test loading tracked issues when store is empty."""
        plugin = MagicMock()
        plugin.load_json.return_value = {}
        with patch('state_manager._get_state_store_plugin', return_value=plugin):
            result = HostStateManager.load_tracked_issues()
            assert result == {}
    
    def test_load_tracked_issues_valid_data(self):
        """Test loading valid tracked issues."""
        test_data = {"123": {"project": "test", "status": "active"}}
        plugin = MagicMock()
        plugin.load_json.return_value = test_data
        with patch('state_manager._get_state_store_plugin', return_value=plugin):
            result = HostStateManager.load_tracked_issues()
            assert result == test_data
    
    def test_load_tracked_issues_plugin_missing(self):
        """Test loading when plugin is unavailable."""
        with patch('state_manager._get_state_store_plugin', return_value=None):
            result = HostStateManager.load_tracked_issues()
            assert result == {}
    
    def test_save_tracked_issues(self):
        """Test saving tracked issues."""
        test_data = {"111": {"project": "test", "status": "active"}}
        plugin = MagicMock()
        with patch('state_manager._get_state_store_plugin', return_value=plugin):
            HostStateManager.save_tracked_issues(test_data)
            plugin.save_json.assert_called_once()
            assert plugin.save_json.call_args.args[1] == test_data
    
    def test_add_tracked_issue(self):
        """Test adding a tracked issue."""
        with patch.object(HostStateManager, 'load_tracked_issues', return_value={}):
            with patch.object(HostStateManager, 'save_tracked_issues') as mock_save:
                HostStateManager.add_tracked_issue(123, "test-project", "Test description")
                
                mock_save.assert_called_once()
                saved_data = mock_save.call_args[0][0]
                assert "123" in saved_data
                assert saved_data["123"]["project"] == "test-project"
                assert saved_data["123"]["description"] == "Test description"
                assert saved_data["123"]["status"] == "active"
    
    def test_remove_tracked_issue(self):
        """Test removing a tracked issue."""
        existing_data = {"123": {"project": "test", "status": "active"}}
        with patch.object(HostStateManager, 'load_tracked_issues', return_value=existing_data):
            with patch.object(HostStateManager, 'save_tracked_issues') as mock_save:
                HostStateManager.remove_tracked_issue(123)
                
                mock_save.assert_called_once()
                saved_data = mock_save.call_args[0][0]
                assert "123" not in saved_data


class TestLaunchedAgents:
    """Tests for launched agents tracking."""
    
    def test_load_launched_agents_empty(self):
        """Test loading launched agents when store is empty."""
        plugin = MagicMock()
        plugin.load_json.return_value = {}
        with patch('state_manager._get_state_store_plugin', return_value=plugin):
            result = HostStateManager.load_launched_agents()
            assert result == {}
    
    def test_load_launched_agents_filters_old(self):
        """Test that old entries are filtered during load."""
        old_time = time.time() - 300  # 5 minutes ago
        recent_time = time.time() - 60  # 1 minute ago
        test_data = {
            "123_OldAgent": {"timestamp": old_time, "issue": "123"},
            "456_RecentAgent": {"timestamp": recent_time, "issue": "456"}
        }
        plugin = MagicMock()
        plugin.load_json.return_value = test_data
        with patch('state_manager._get_state_store_plugin', return_value=plugin):
            result = HostStateManager.load_launched_agents()

            # Old entry should be filtered out (>2 minute window)
            assert "123_OldAgent" not in result
            assert "456_RecentAgent" in result
    
    def test_register_launched_agent(self):
        """Test registering a newly launched agent."""
        with patch.object(HostStateManager, 'load_launched_agents', return_value={}):
            with patch.object(HostStateManager, 'save_launched_agents') as mock_save:
                HostStateManager.register_launched_agent("123", "TestAgent", 12345)
                
                mock_save.assert_called_once()
                saved_data = mock_save.call_args[0][0]
                assert "123_TestAgent" in saved_data
                assert saved_data["123_TestAgent"]["pid"] == 12345
    
    def test_was_recently_launched(self):
        """Test checking if agent was recently launched."""
        test_data = {"123_TestAgent": {"issue": "123", "timestamp": time.time()}}
        with patch.object(HostStateManager, 'load_launched_agents', return_value=test_data):
            result = HostStateManager.was_recently_launched("123", "TestAgent")
            assert result is True
    
    def test_was_not_recently_launched(self):
        """Test checking agent that was not recently launched."""
        with patch.object(HostStateManager, 'load_launched_agents', return_value={}):
            result = HostStateManager.was_recently_launched("999", "NonExistentAgent")
            assert result is False
