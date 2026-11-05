"""Channel adapters: the conversation engine never depends on a concrete channel."""

from __future__ import annotations

from abc import ABC, abstractmethod


class ChannelAdapter(ABC):
    """Minimal contract every messaging channel implements."""

    @abstractmethod
    def send_text(self, user_id: str, text: str) -> None:
        """Send a plain text message to a user."""

    @abstractmethod
    def send_buttons(self, user_id: str, text: str, buttons: list[str]) -> None:
        """Send tappable options, falling back to text when unsupported."""
