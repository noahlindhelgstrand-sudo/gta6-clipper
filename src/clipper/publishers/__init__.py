from __future__ import annotations

from ..config import Config
from .base import AuthExpiredError, PublishError, Publisher, QuotaExceededError, UploadResult  # noqa: F401
from .dryrun import DryRunPublisher


def build_publishers(cfg: Config, dry_run: bool) -> list[Publisher]:
    pubs: list[Publisher] = []
    for name in cfg.publish.platforms:
        if dry_run:
            pubs.append(DryRunPublisher(name, cfg.publish.privacy_status))
        elif name == "youtube":
            from .youtube import YouTubePublisher, build_service
            s = cfg.secrets
            pubs.append(YouTubePublisher(
                build_service(s.youtube_client_id, s.youtube_client_secret, s.youtube_refresh_token),
                cfg.publish))
        else:
            # Framtida: "tiktok", "instagram" – implementera Publisher och lägg till här.
            raise ValueError(f"okänd/ej implementerad plattform: {name}")
    return pubs
