"""Gemensamt interface för klippkällor (Twitch, Kick, ...)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..models import ClipCandidate


class SourceError(RuntimeError):
    pass


class ClipSource(ABC):
    platform: str

    @abstractmethod
    def fetch_clips(self, started_at: datetime, ended_at: datetime, max_clips: int = 500) -> list[ClipCandidate]:
        """Mest sedda klipp i spelkategorin inom tidsfönstret."""

    def fetch_channel_clips(self, channel: str, started_at: datetime, ended_at: datetime,
                            max_clips: int = 100) -> list[ClipCandidate]:
        """Klipp från en viss kanal (bara spelkategorin). Plattformar utan stöd returnerar []."""
        return []
