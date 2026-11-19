"""Twitch Helix: app access token (client credentials), Get Games, Get Clips."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

from ..log import get_logger
from ..models import ClipCandidate
from .base import ClipSource, SourceError

log = get_logger("twitch")

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
HELIX = "https://api.twitch.tv/helix"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TwitchSource(ClipSource):
    platform = "twitch"

    def __init__(self, client_id: str, client_secret: str, game_name: str,
                 session: requests.Session | None = None):
        if not client_id or not client_secret:
            raise SourceError("TWITCH_CLIENT_ID/TWITCH_CLIENT_SECRET saknas")
        self.client_id = client_id
        self.client_secret = client_secret
        self.game_name = game_name
        self.http = session or requests.Session()
        self._token: str | None = None
        self._game_id: str | None = None

    # --- auth ---
    def _get_token(self) -> str:
        if self._token:
            return self._token
        r = self.http.post(TOKEN_URL, params={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        }, timeout=20)
        if r.status_code != 200:
            raise SourceError(f"Twitch token misslyckades: {r.status_code} {r.text[:200]}")
        self._token = r.json()["access_token"]
        return self._token

    def _get(self, path: str, params) -> dict:
        for attempt in range(4):
            r = self.http.get(f"{HELIX}{path}", params=params, timeout=20, headers={
                "Client-Id": self.client_id,
                "Authorization": f"Bearer {self._get_token()}",
            })
            if r.status_code == 401 and attempt == 0:
                self._token = None           # token utgången -> hämta ny
                continue
            if r.status_code == 429 or r.status_code >= 500:
                reset = r.headers.get("Ratelimit-Reset")
                wait = max(1, int(reset) - int(time.time())) if reset and reset.isdigit() else 2 ** attempt
                time.sleep(min(wait, 30))
                continue
            if r.status_code != 200:
                raise SourceError(f"Twitch {path} {r.status_code}: {r.text[:200]}")
            return r.json()
        raise SourceError(f"Twitch {path}: gav upp efter retries")

    def game_id(self) -> str:
        if self._game_id:
            return self._game_id
        data = self._get("/games", {"name": self.game_name}).get("data", [])
        if not data:
            raise SourceError(f"Hittade ingen Twitch-kategori med namnet '{self.game_name}'")
        self._game_id = data[0]["id"]
        return self._game_id

    def fetch_clips(self, started_at: datetime, ended_at: datetime, max_clips: int = 500) -> list[ClipCandidate]:
        gid = self.game_id()
        out: list[ClipCandidate] = []
        cursor = None
        while len(out) < max_clips:
            params = {"game_id": gid, "started_at": _iso(started_at), "ended_at": _iso(ended_at), "first": 100}
            if cursor:
                params["after"] = cursor
            payload = self._get("/clips", params)
            for c in payload.get("data", []):
                out.append(self._to_candidate(c))
            cursor = (payload.get("pagination") or {}).get("cursor")
            if not cursor or not payload.get("data"):
                break
        log.info("twitch clips hämtade", extra={"count": len(out), "game_id": gid})
        return out[:max_clips]

    def fetch_channel_clips(self, channel: str, started_at: datetime, ended_at: datetime,
                            max_clips: int = 100) -> list[ClipCandidate]:
        """Get Users (login -> id) + Get Clips med broadcaster_id, filtrerat på GTA 6-kategorin."""
        users = self._get("/users", {"login": channel.lower()}).get("data", [])
        if not users:
            raise SourceError(f"Twitch-kanalen '{channel}' finns inte")
        gid = self.game_id()
        payload = self._get("/clips", {"broadcaster_id": users[0]["id"], "started_at": _iso(started_at),
                                       "ended_at": _iso(ended_at), "first": min(100, max_clips)})
        out = [self._to_candidate(c) for c in payload.get("data", []) if c.get("game_id") == gid]
        log.info("twitch kanalklipp", extra={"channel": channel, "count": len(out)})
        return out

    @staticmethod
    def _to_candidate(c: dict) -> ClipCandidate:
        name = c.get("broadcaster_name") or ""
        return ClipCandidate(
            platform="twitch",
            clip_id=c["id"],
            streamer=name.lower(),
            streamer_display=name,
            title=c.get("title") or "",
            views=int(c.get("view_count") or 0),
            url=c.get("url") or f"https://clips.twitch.tv/{c['id']}",
            created_at=datetime.fromisoformat(c["created_at"].replace("Z", "+00:00")),
            duration_s=float(c.get("duration") or 0),
            language=c.get("language"),
        )
