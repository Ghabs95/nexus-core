"""Built-in plugin: Telegram notification channel via Bot HTTP API."""

import json
import logging
from typing import Any
from urllib import request
from urllib.error import HTTPError

from nexus.adapters.notifications.base import Message, NotificationChannel
from nexus.core.models import Severity

logger = logging.getLogger(__name__)


class TelegramNotificationPlugin(NotificationChannel):
    """Telegram notification channel using direct HTTP API calls."""

    def __init__(self, config: dict[str, Any]):
        self.bot_token = config.get("bot_token", "")
        self.chat_id = str(config.get("chat_id", ""))
        self.parse_mode = config.get("parse_mode", "Markdown")
        self.timeout = int(config.get("timeout", 10))

    @property
    def name(self) -> str:
        return "telegram-notification-http"

    async def send_message(self, user_id: str, message: Message) -> str:
        payload = {
            "chat_id": str(user_id),
            "text": message.text,
            "parse_mode": self.parse_mode,
        }
        response = self._post("sendMessage", payload)
        if not response:
            return ""
        result = response.get("result", {})
        return str(result.get("message_id", ""))

    def send_message_sync(
        self,
        message: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """Send a message synchronously to configured chat id."""
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram plugin missing credentials, skipping send_message_sync")
            return False

        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": message,
        }
        effective_parse_mode = parse_mode if parse_mode is not None else self.parse_mode
        if effective_parse_mode:
            payload["parse_mode"] = effective_parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup

        sent = bool(self._post("sendMessage", payload))
        if sent:
            return True

        # Fallback: retry as plain text when markdown/html parsing fails.
        plain_payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": message,
        }
        if reply_markup:
            plain_payload["reply_markup"] = reply_markup

        logger.warning(
            "Telegram send_message_sync retrying without parse_mode after initial failure"
        )
        return bool(self._post("sendMessage", plain_payload))

    async def update_message(self, message_id: str, new_text: str) -> None:
        payload = {
            "chat_id": self.chat_id,
            "message_id": int(message_id),
            "text": new_text,
            "parse_mode": self.parse_mode,
        }
        self._post("editMessageText", payload)

    async def send_alert(self, message: str, severity: Severity) -> None:
        self.send_alert_sync(message=message, severity=severity.value)

    async def request_input(self, user_id: str, prompt: str) -> str:
        await self.send_message(user_id, Message(text=prompt, severity=Severity.INFO))
        return ""

    def send_alert_sync(self, message: str, severity: str = "info") -> bool:
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram plugin missing credentials, skipping alert")
            return False

        icon = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "❌",
            "critical": "🚨",
        }.get((severity or "info").lower(), "ℹ️")

        return self.send_message_sync(f"{icon} {message}", parse_mode=self.parse_mode)

    def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.bot_token:
            return None

        url = f"https://api.telegram.org/bot{self.bot_token}/{method}"
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if not data.get("ok"):
                logger.warning("Telegram API returned non-ok for %s: %s", method, data)
                return None
            return data
        except HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8")
            except Exception:
                body = ""
            logger.error(
                "Telegram API HTTP error for %s: status=%s reason=%s body=%s",
                method,
                getattr(exc, "code", "?"),
                getattr(exc, "reason", ""),
                body,
            )
            return None
        except Exception as exc:
            logger.error("Telegram API call failed for %s: %s", method, exc)
            return None


def register_plugins(registry) -> None:
    """Register built-in Telegram notification plugin."""
    from nexus.plugins import PluginKind

    registry.register_factory(
        kind=PluginKind.NOTIFICATION_CHANNEL,
        name="telegram-notification-http",
        version="0.1.0",
        factory=lambda config: TelegramNotificationPlugin(config),
        description="Telegram Bot API notification channel plugin",
    )
