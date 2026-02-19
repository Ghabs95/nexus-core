"""Tests for built-in Telegram notification plugin."""

import json

from nexus.plugins.builtin.telegram_notification_plugin import TelegramNotificationPlugin


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_send_alert_sync_posts_to_telegram(monkeypatch):
    captured = {"url": "", "body": {}}

    def _fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Response({"ok": True, "result": {"message_id": 123}})

    monkeypatch.setattr(
        "nexus.plugins.builtin.telegram_notification_plugin.request.urlopen",
        _fake_urlopen,
    )

    plugin = TelegramNotificationPlugin(
        {"bot_token": "token123", "chat_id": "999", "parse_mode": "Markdown"}
    )
    sent = plugin.send_alert_sync("System ready", severity="info")

    assert sent is True
    assert "bottoken123/sendMessage" in captured["url"]
    assert captured["body"]["chat_id"] == "999"
    assert "System ready" in captured["body"]["text"]
