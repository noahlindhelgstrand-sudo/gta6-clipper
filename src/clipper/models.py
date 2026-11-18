"""Gemensamma datatyper."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ClipCandidate:
    platform: str            # "twitch" | "kick"
    clip_id: str             # plattformens id
    streamer: str            # login/slug, gemener
    streamer_display: str
    title: str
    views: int
    url: str
    created_at: datetime     # tz-aware UTC
    duration_s: float
    language: str | None = None
    score: float = 0.0

    @property
    def key(self) -> str:
        """Globalt unikt id i databasen."""
        return f"{self.platform}:{self.clip_id}"

    @property
    def streamer_key(self) -> str:
        return f"{self.platform}:{self.streamer}"


@dataclass
class VideoMetadata:
    title: str
    description: str
    hashtags: list[str] = field(default_factory=list)
    hook: str = ""
    generated_by: str = "fallback"   # "claude" | "fallback"
