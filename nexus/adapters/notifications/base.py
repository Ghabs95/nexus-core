"""Base interface for notification channels."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from nexus.core.models import Severity


@dataclass
class Button:
    """Interactive button for notifications."""

    label: str
    callback_data: str
    url: str | None = None


@dataclass
class Message:
    """Notification message."""

    text: str
    severity: Severity = Severity.INFO
    buttons: list[Button] | None = None


class NotificationChannel(ABC):
    """Abstract notification channel (Telegram, Slack, Email, etc.)."""

    @abstractmethod
    async def send_message(self, user_id: str, message: Message) -> str:
        """
        Send a message to a user.

        Returns: Message ID for later updates
        """
        pass

    @abstractmethod
    async def update_message(self, message_id: str, new_text: str) -> None:
        """Update an existing message."""
        pass

    @abstractmethod
    async def send_alert(self, message: str, severity: Severity) -> None:
        """Send a system alert (broadcasts to admins)."""
        pass

    @abstractmethod
    async def request_input(self, user_id: str, prompt: str) -> str:
        """Request input from user and wait for response."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Channel name (e.g., 'telegram', 'slack')."""
        pass
