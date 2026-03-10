"""Tests for built-in Telegram notification plugin."""

import json
from io import BytesIO
from urllib.error import HTTPError

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


def test_send_message_sync_retries_without_parse_mode_on_parse_error(monkeypatch, caplog):
    captured_bodies: list[dict] = []
    calls = {"count": 0}

    def _fake_urlopen(req, timeout):
        del timeout
        calls["count"] += 1
        payload = json.loads(req.data.decode("utf-8"))
        captured_bodies.append(payload)
        if calls["count"] == 1:
            raise HTTPError(
                req.full_url,
                400,
                "Bad Request",
                hdrs=None,
                fp=BytesIO(
                    (
                        '{"ok":false,"error_code":400,'
                        '"description":"Bad Request: can\'t parse entities: '
                        'Can\'t find end of the entity"}'
                    ).encode("utf-8")
                ),
            )
        return _Response({"ok": True, "result": {"message_id": 456}})

    monkeypatch.setattr(
        "nexus.plugins.builtin.telegram_notification_plugin.request.urlopen",
        _fake_urlopen,
    )

    plugin = TelegramNotificationPlugin(
        {"bot_token": "token123", "chat_id": "999", "parse_mode": "Markdown"}
    )
    sent = plugin.send_message_sync("⚠️ **Alert** with _broken markdown")

    assert sent is True
    assert calls["count"] == 2
    assert captured_bodies[0].get("parse_mode") == "Markdown"
    assert "parse_mode" not in captured_bodies[1]
    assert "retrying without parse_mode after initial failure" in caplog.text
    assert "Telegram API HTTP error for sendMessage" not in caplog.text


def test_send_message_sync_without_parse_mode_does_not_retry(monkeypatch):
    calls = {"count": 0}

    def _fake_urlopen(req, timeout):
        del req, timeout
        calls["count"] += 1
        raise HTTPError(
            "https://api.telegram.org/fake",
            500,
            "Server Error",
            hdrs=None,
            fp=BytesIO(b'{"ok":false,"error_code":500,"description":"Internal Error"}'),
        )

    monkeypatch.setattr(
        "nexus.plugins.builtin.telegram_notification_plugin.request.urlopen",
        _fake_urlopen,
    )

    plugin = TelegramNotificationPlugin(
        {"bot_token": "token123", "chat_id": "999", "parse_mode": ""}
    )
    sent = plugin.send_message_sync("plain text", parse_mode=None)

    assert sent is False
    assert calls["count"] == 1
