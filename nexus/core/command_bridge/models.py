"""Typed request and response models for the Nexus command bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RequesterContext:
    """Metadata about the caller that originated a command."""

    source_platform: str = "openclaw"
    sender_id: str = ""
    sender_name: str = ""
    channel_id: str = ""
    channel_name: str = ""
    is_authorized_sender: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "RequesterContext":
        data = payload if isinstance(payload, dict) else {}
        return cls(
            source_platform=str(data.get("source_platform", "openclaw") or "openclaw"),
            sender_id=str(data.get("sender_id", "") or ""),
            sender_name=str(data.get("sender_name", "") or ""),
            channel_id=str(data.get("channel_id", "") or ""),
            channel_name=str(data.get("channel_name", "") or ""),
            is_authorized_sender=(
                bool(data["is_authorized_sender"])
                if "is_authorized_sender" in data and data.get("is_authorized_sender") is not None
                else None
            ),
            metadata=dict(data.get("metadata", {}) or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_platform": self.source_platform,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "is_authorized_sender": self.is_authorized_sender,
            "metadata": dict(self.metadata or {}),
        }

    def to_audit_context(self) -> dict[str, Any]:
        payload = {
            "platform": self.source_platform,
            "platform_user_id": self.sender_id,
            "platform_user_name": self.sender_name,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "authorized_sender": self.is_authorized_sender,
        }
        if self.metadata:
            payload["source_metadata"] = dict(self.metadata)
        return payload


@dataclass
class CommandRequest:
    """Normalized command payload accepted by the router and HTTP bridge."""

    command: str = ""
    args: list[str] = field(default_factory=list)
    raw_text: str = ""
    requester: RequesterContext = field(default_factory=RequesterContext)
    context: dict[str, Any] = field(default_factory=dict)
    attachments: list[Any] = field(default_factory=list)
    correlation_id: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "CommandRequest":
        data = payload if isinstance(payload, dict) else {}
        args = data.get("args", [])
        attachments = data.get("attachments", [])
        return cls(
            command=str(data.get("command", "") or ""),
            args=[str(item or "") for item in args] if isinstance(args, list) else [],
            raw_text=str(data.get("raw_text", "") or ""),
            requester=RequesterContext.from_dict(data.get("requester")),
            context=dict(data.get("context", {}) or {}),
            attachments=list(attachments or []) if isinstance(attachments, list) else [],
            correlation_id=str(data.get("correlation_id", "") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "args": list(self.args or []),
            "raw_text": self.raw_text,
            "requester": self.requester.to_dict(),
            "context": dict(self.context or {}),
            "attachments": list(self.attachments or []),
            "correlation_id": self.correlation_id,
        }


@dataclass
class CommandResult:
    """Structured command execution result returned by the bridge."""

    status: str
    message: str
    workflow_id: str | None = None
    issue_number: str | None = None
    project_key: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    suggested_next_commands: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "workflow_id": self.workflow_id,
            "issue_number": self.issue_number,
            "project_key": self.project_key,
            "data": dict(self.data or {}),
            "suggested_next_commands": list(self.suggested_next_commands or []),
        }
