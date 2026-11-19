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


@dataclass
class VideoHealth:
    """Status för en redan uppladdad video.
    state: ok | removed | rejected | claimed | age_restricted"""
    state: str
    reason: str = ""
    privacy: str = ""            # private | unlisted | public (om känt)

    @property
    def severe(self) -> bool:
        """Allvarligt = trolig copyright-strike eller policybrott → pausa uppladdningar."""
        return self.state in ("removed", "rejected")


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

    def check_health(self, external_ids: list[str]) -> dict[str, VideoHealth]:
        """Status för redan uppladdade videor. Plattformar utan stöd returnerar {} (ingen kontroll)."""
        return {}
