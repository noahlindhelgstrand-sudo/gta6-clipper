"""Gemensamt interface för publicerare. YouTube nu; TikTok/Instagram kan läggas till
genom att implementera samma klass och registrera den i publishers.__init__.build_publishers."""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..models import VideoMetadata


@dataclass
class UploadResult:
    external_id: str
    url: str
    privacy: str
    locked_private: bool = False


class PublishError(RuntimeError):
    """Generellt fel vid publicering."""


class AuthExpiredError(PublishError):
    """Refresh token är ogiltig/utgången – kräver manuell åtgärd."""


class QuotaExceededError(PublishError):
    """API-kvoten slut för dagen – försök inte igen förrän kvoten nollställs."""


def clip_ref(clip_key: str) -> str:
    """Kort, stabil markör som sparas på den uppladdade videon (för idempotens efter krasch)."""
    return "clipref" + hashlib.sha1(clip_key.encode()).hexdigest()[:12]


class Publisher(ABC):
    name: str

    @abstractmethod
    def upload(self, video: Path, meta: VideoMetadata, clip_key: str) -> UploadResult:
        ...

    @abstractmethod
    def find_existing(self, clip_key: str) -> UploadResult | None:
        """Kolla om ett klipp redan laddats upp (t.ex. om förra körningen kraschade mitt i)."""
