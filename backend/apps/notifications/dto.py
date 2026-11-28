"""Data transfer objects for the notifications module."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationMessage:
    """Immutable notification payload for sending to Telegram."""

    recipient_chat_id: str
    title: str
    body: str
