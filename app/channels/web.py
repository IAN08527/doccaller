"""Local web chat channel adapter (pull-based HTTP)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.channels.base import ChannelAdapter


@dataclass(slots=True)
class OutboundMessage:
    user_id: str
    text: str
    buttons: list[str] = field(default_factory=list)


class WebChannel(ChannelAdapter):
    """In-memory outbox for the local HTTP channel.

    The HTTP endpoints return replies directly; this adapter exists for parity
    with push-based channels and for tests that need to capture outbound copy.
    """

    def __init__(self) -> None:
        self._outbox: dict[str, list[OutboundMessage]] = defaultdict(list)

    def send_text(self, user_id: str, text: str) -> None:
        self._outbox[user_id].append(OutboundMessage(user_id=user_id, text=text))

    def send_buttons(self, user_id: str, text: str, buttons: list[str]) -> None:
        self._outbox[user_id].append(
            OutboundMessage(user_id=user_id, text=text, buttons=list(buttons))
        )

    def drain(self, user_id: str) -> list[OutboundMessage]:
        return self._outbox.pop(user_id, [])
