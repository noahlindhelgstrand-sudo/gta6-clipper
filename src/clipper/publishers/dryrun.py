"""Dry-run: loggar vad som skulle laddas upp, laddar inte upp något."""
from __future__ import annotations

from pathlib import Path

from ..log import get_logger
from ..models import VideoMetadata
from .base import Publisher, UploadResult, VideoHealth, clip_ref

log = get_logger("dryrun")


class DryRunPublisher(Publisher):
    def __init__(self, name: str = "youtube", privacy: str = "private"):
        self.name = name
        self.privacy = privacy
        self.uploaded: list[tuple[str, VideoMetadata]] = []

    def upload(self, video: Path, meta: VideoMetadata, clip_key: str) -> UploadResult:
        size = Path(video).stat().st_size if Path(video).exists() else 0
        log.info("[DRY-RUN] skulle ladda upp", extra={
            "platform": self.name, "clip": clip_key, "file": str(video), "bytes": size,
            "title": meta.title, "privacy": self.privacy, "description": meta.description})
        self.uploaded.append((clip_key, meta))
        fake = "dry_" + clip_ref(clip_key)[7:]
        return UploadResult(fake, f"https://youtube.com/shorts/{fake}", self.privacy)

    def find_existing(self, clip_key: str) -> UploadResult | None:
        return None

    def check_health(self, external_ids: list[str]) -> dict[str, VideoHealth]:
        return {vid: VideoHealth("ok", privacy=self.privacy) for vid in external_ids}
