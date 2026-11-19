"""Kick-klipp (best effort).

Kick har ännu inget officiellt publikt API för klipp, så vi använder den
endpoint som kick.com själv använder. Den kan ändras eller blockeras av
Cloudflare från datacenter-IP:n (t.ex. GitHub Actions). Fel här är aldrig
fatala – pipelinen fortsätter med övriga källor.
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from ..log import get_logger
from ..models import ClipCandidate
from .base import ClipSource, SourceError

log = get_logger("kick")

API = "https://kick.com/api/v2/categories/{slug}/clips"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def _parse_dt(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class KickSource(ClipSource):
    platform = "kick"

    def __init__(self, category_slug: str, session: requests.Session | None = None):
        self.slug = category_slug
        self.http = session or requests.Session()

    def fetch_clips(self, started_at: datetime, ended_at: datetime, max_clips: int = 500) -> list[ClipCandidate]:
        hours = (ended_at - started_at).total_seconds() / 3600
        period = "day" if hours <= 24 else "week" if hours <= 24 * 7 else "month"
        out: list[ClipCandidate] = []
        cursor = "0"
        for _ in range(10):
            try:
                r = self.http.get(API.format(slug=self.slug),
                                  params={"cursor": cursor, "sort": "view", "time": period},
                                  headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20)
            except requests.RequestException as e:
                raise SourceError(f"Kick nätverksfel: {e}") from e
            if r.status_code != 200:
                raise SourceError(f"Kick {r.status_code} (troligen Cloudflare-blockering)")
            try:
                payload = r.json()
            except ValueError as e:
                raise SourceError("Kick svarade inte med JSON (troligen Cloudflare-utmaning)") from e
            for c in payload.get("clips", []):
                cand = self._to_candidate(c)
                if started_at <= cand.created_at <= ended_at:
                    out.append(cand)
            cursor = payload.get("nextCursor")
            if not cursor or len(out) >= max_clips:
                break
        log.info("kick clips hämtade", extra={"count": len(out)})
        return out[:max_clips]

    def fetch_channel_clips(self, channel: str, started_at: datetime, ended_at: datetime,
                            max_clips: int = 100) -> list[ClipCandidate]:
        try:
            r = self.http.get(f"https://kick.com/api/v2/channels/{channel.lower()}/clips",
                              params={"cursor": "0", "sort": "date", "time": "week"},
                              headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20)
            payload = r.json() if r.status_code == 200 else None
        except (requests.RequestException, ValueError) as e:
            raise SourceError(f"Kick kanalklipp {channel}: {e}") from e
        if payload is None:
            raise SourceError(f"Kick kanalklipp {channel}: HTTP {r.status_code}")
        out = []
        for c in payload.get("clips", []):
            cat = (c.get("category") or {}).get("slug", "")
            c.setdefault("channel", {"slug": channel.lower(), "username": channel})
            cand = self._to_candidate(c)
            if (not cat or cat == self.slug) and started_at <= cand.created_at <= ended_at:
                out.append(cand)
        log.info("kick kanalklipp", extra={"channel": channel, "count": len(out)})
        return out[:max_clips]

    @staticmethod
    def _to_candidate(c: dict) -> ClipCandidate:
        ch = c.get("channel") or {}
        slug = (ch.get("slug") or ch.get("username") or "").lower()
        cid = c["id"]
        return ClipCandidate(
            platform="kick",
            clip_id=cid,
            streamer=slug,
            streamer_display=ch.get("username") or slug,
            title=c.get("title") or "",
            views=int(c.get("views") or c.get("view_count") or 0),
            url=f"https://kick.com/{slug}/clips/{cid}",
            created_at=_parse_dt(c.get("created_at")),
            duration_s=float(c.get("duration") or 0),
            language=None,   # Kick exponerar inte språk per klipp
        )
