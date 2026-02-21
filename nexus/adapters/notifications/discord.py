"""Discord notification channel adapter.

Requires the ``discord`` optional extra::

    pip install nexus-core[discord]
"""
import logging
from typing import Optional

from nexus.adapters.notifications.base import Button, Message, NotificationChannel
from nexus.core.models import Severity

try:
    import aiohttp

    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False

logger = logging.getLogger(__name__)

# Severity → Discord embed colour (decimal)
_SEVERITY_COLOUR = {
    Severity.CRITICAL: 0xFF0000,
    Severity.ERROR: 0xFF6B35,
    Severity.WARNING: 0xFFB347,
    Severity.INFO: 0x36A64F,
}


def _require_aiohttp() -> None:
    if not _AIOHTTP_AVAILABLE:
        raise ImportError(
            "aiohttp is required for DiscordNotificationChannel. "
            "Install it with: pip install nexus-core[discord]"
        )


def _severity_emoji(severity: Severity) -> str:
    return {
        Severity.CRITICAL: "🔴",
        Severity.ERROR: "🟠",
        Severity.WARNING: "🟡",
        Severity.INFO: "ℹ️",
    }.get(severity, "ℹ️")


class DiscordNotificationChannel(NotificationChannel):
    """Discord notification channel using Discord's Webhook and REST APIs.

    Two usage modes are supported:

    1. **Webhook-only** (simpler): supply ``webhook_url``. Works for
       ``send_message``, ``send_alert``, and partial ``update_message``
       support (webhook message editing).

    2. **Bot token** (full): supply ``bot_token`` and optionally
       ``webhook_url``. Required for ``request_input`` (polls the channel
       for a reply).

    Args:
        webhook_url: Discord incoming webhook URL for posting messages.
        bot_token: Discord bot token for full REST API access.
        alert_channel_id: Channel ID to broadcast system alerts to (required
            when ``webhook_url`` is not set).
    """

    _BASE = "https://discord.com/api/v10"

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        bot_token: Optional[str] = None,
        alert_channel_id: Optional[str] = None,
    ):
        _require_aiohttp()
        if not webhook_url and not bot_token:
            raise ValueError("At least one of webhook_url or bot_token must be provided.")
        self._webhook_url = webhook_url
        self._bot_token = bot_token
        self._alert_channel_id = alert_channel_id

    # ------------------------------------------------------------------
    # NotificationChannel interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "discord"

    async def send_message(self, user_id: str, message: Message) -> str:
        """Post a message to a Discord channel.

        Args:
            user_id: Discord channel ID (or thread ID) to post to.
            message: Message to send.

        Returns:
            Discord message ID (``snowflake`` string).
        """
        payload = self._build_payload(message)
        if self._webhook_url:
            return await self._post_webhook(payload)
        return await self._post_channel(user_id, payload)

    async def update_message(self, message_id: str, new_text: str) -> None:
        """Edit a previously sent Discord message.

        ``message_id`` should be encoded as ``channel_id:message_id`` when
        using the bot token path, or as the bare webhook message ID when
        using a webhook.
        """
        if self._webhook_url and ":" not in message_id:
            await self._patch_webhook_message(message_id, {"content": new_text})
            return

        if ":" in message_id:
            channel_id, mid = message_id.split(":", 1)
        else:
            raise ValueError(
                "message_id must be 'channel_id:message_id' when using bot token path."
            )
        await self._patch_channel_message(channel_id, mid, {"content": new_text})

    async def send_alert(self, message: str, severity: Severity) -> None:
        """Broadcast a system alert embed to the configured alert channel."""
        emoji = _severity_emoji(severity)
        colour = _SEVERITY_COLOUR.get(severity, 0x36A64F)
        payload: dict = {
            "embeds": [
                {
                    "title": f"{emoji} [{severity.value.upper()}] System Alert",
                    "description": message,
                    "color": colour,
                }
            ]
        }

        if self._webhook_url:
            await self._post_webhook(payload)
            return

        if not self._alert_channel_id:
            raise ValueError(
                "alert_channel_id is required to send alerts without a webhook_url."
            )
        await self._post_channel(self._alert_channel_id, payload)

    async def request_input(self, user_id: str, prompt: str) -> str:
        """Send a prompt to a channel and wait for the next human reply (up to 60 s).

        Requires ``bot_token``.
        """
        import asyncio

        if not self._bot_token:
            raise ValueError("bot_token is required for request_input.")

        msg = Message(text=prompt)
        raw_id = await self._post_channel(user_id, self._build_payload(msg))

        deadline = asyncio.get_event_loop().time() + 60
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(3)
            messages = await self._fetch_messages_after(user_id, raw_id)
            for m in messages:
                if not m.get("author", {}).get("bot", False):
                    return m.get("content", "")

        raise TimeoutError(f"No reply in Discord channel {user_id} within 60s")

    # ------------------------------------------------------------------
    # Internal helpers — payload building
    # ------------------------------------------------------------------

    def _build_payload(self, message: Message) -> dict:
        """Build a Discord message payload from a ``Message``."""
        colour = _SEVERITY_COLOUR.get(message.severity, 0x36A64F)
        emoji = _severity_emoji(message.severity)
        payload: dict = {
            "embeds": [
                {
                    "description": f"{emoji} {message.text}",
                    "color": colour,
                }
            ]
        }
        if message.buttons:
            # Discord components require an application command context;
            # fall back to including button labels as plain text links.
            links = []
            for btn in message.buttons:
                if btn.url:
                    links.append(f"[{btn.label}]({btn.url})")
                else:
                    links.append(f"`{btn.label}`")
            payload["content"] = " | ".join(links)
        return payload

    # ------------------------------------------------------------------
    # Internal helpers — HTTP
    # ------------------------------------------------------------------

    async def _post_webhook(self, payload: dict) -> str:
        """POST payload to the webhook URL; returns the message ID."""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._webhook_url}?wait=true",
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return str(data.get("id", ""))

    async def _post_channel(self, channel_id: str, payload: dict) -> str:
        """POST payload to a channel via the REST API; returns the message ID."""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._BASE}/channels/{channel_id}/messages",
                json=payload,
                headers=self._auth_headers(),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return str(data.get("id", ""))

    async def _patch_webhook_message(self, message_id: str, payload: dict) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.patch(
                f"{self._webhook_url}/messages/{message_id}",
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as resp:
                resp.raise_for_status()

    async def _patch_channel_message(
        self, channel_id: str, message_id: str, payload: dict
    ) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.patch(
                f"{self._BASE}/channels/{channel_id}/messages/{message_id}",
                json=payload,
                headers=self._auth_headers(),
            ) as resp:
                resp.raise_for_status()

    async def _fetch_messages_after(self, channel_id: str, after_id: str) -> list:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self._BASE}/channels/{channel_id}/messages",
                params={"after": after_id, "limit": "10"},
                headers=self._auth_headers(),
            ) as resp:
                if resp.status != 200:
                    return []
                return await resp.json()

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bot {self._bot_token}",
            "Content-Type": "application/json",
        }
