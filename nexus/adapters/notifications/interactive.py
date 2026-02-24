"""Interactive client plugin interface for two-way chat communications."""
from abc import ABC, abstractmethod
from collections.abc import Callable

from nexus.adapters.notifications.base import Message


class InteractiveClientPlugin(ABC):
    """Abstract interface for interactive chat clients (Telegram, Slack, Discord)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Channel name (e.g., 'telegram-interactive', 'slack-interactive')."""
        pass

    @abstractmethod
    async def start(self) -> None:
        """Begin listening for events from the provider (polling or webhooks)."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shutdown the listener."""
        pass

    @abstractmethod
    def register_command(self, command: str, callback: Callable) -> None:
        """
        Bind a slash command to a framework action.

        Args:
            command: The slash command string (e.g., '/ideate', '/direct')
            callback: The async function to execute when the command is received.
        """
        pass

    @abstractmethod
    def register_message_handler(self, callback: Callable) -> None:
        """
        Bind general text messages to a framework handler.

        Args:
            callback: The async function to execute for plain text messages.
        """
        pass

    @abstractmethod
    async def send_interactive(self, user_id: str, message: Message) -> str:
        """
        Send a message with potential interactive buttons/actions.

        Args:
            user_id: The recipient identifier.
            message: The Message object containing text and optional buttons.

        Returns:
            The message ID of the sent message for future updates.
        """
        pass

    @abstractmethod
    async def edit_interactive(self, user_id: str, message_id: str, message: Message) -> None:
        """
        Edit a previously sent message.

        Args:
            user_id: The recipient identifier.
            message_id: The ID returned by `send_interactive`.
            message: The new message content.
        """
        pass
