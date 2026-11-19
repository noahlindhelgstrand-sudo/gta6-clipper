"""YouTube Data API v3 – videos.insert med resumable upload och OAuth refresh token."""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

from ..config import PublishCfg
from ..log import get_logger
from ..models import VideoMetadata
from .base import (AuthExpiredError, PublishError, Publisher, QuotaExceededError, UploadResult,
                   VideoHealth, clip_ref)

log = get_logger("youtube")

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]
RETRIABLE_STATUS = {500, 502, 503, 504}
QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded", "rateLimitExceeded"}


def _error_reason(err) -> str:
    try:
        data = json.loads(err.content.decode() if isinstance(err.content, bytes) else err.content)
        errs = data.get("error", {}).get("errors") or [{}]
        return errs[0].get("reason") or data.get("error", {}).get("status", "")
    except Exception:  # noqa: BLE001
        return ""


def build_service(client_id: str, client_secret: str, refresh_token: str):
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not (client_id and client_secret and refresh_token):
        raise AuthExpiredError("YOUTUBE_CLIENT_ID/SECRET/REFRESH_TOKEN saknas")
    creds = Credentials(None, refresh_token=refresh_token, client_id=client_id,
                        client_secret=client_secret, token_uri="https://oauth2.googleapis.com/token",
                        scopes=SCOPES)
    try:
        creds.refresh(Request())
    except RefreshError as e:
        raise AuthExpiredError(
            f"YouTube refresh token ogiltig/utgången ({e}). Kör scripts/get_youtube_refresh_token.py "
            "igen och uppdatera secret YOUTUBE_REFRESH_TOKEN. Tips: sätt OAuth-appen till 'In production' "
            "– i 'Testing' går token ut efter 7 dagar.") from e
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


class YouTubePublisher(Publisher):
    name = "youtube"

    def __init__(self, service, cfg: PublishCfg, media_factory=None, sleep=time.sleep):
        self.yt = service
        self.cfg = cfg
        self.sleep = sleep
        if media_factory is None:
            from googleapiclient.http import MediaFileUpload

            def media_factory(path: Path):
                return MediaFileUpload(str(path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
        self.media_factory = media_factory

    def _classify(self, e) -> Exception:
        from googleapiclient.errors import HttpError
        if isinstance(e, HttpError):
            status = e.resp.status
            reason = _error_reason(e)
            if status in (401,) or reason in ("authError", "invalid_grant"):
                return AuthExpiredError(f"YouTube auth-fel: {reason or status}")
            if status in (403, 429) and reason in QUOTA_REASONS:
                return QuotaExceededError(f"YouTube-kvot överskriden ({reason}). Nollställs midnatt Pacific time.")
            if status in RETRIABLE_STATUS:
                return e
            return PublishError(f"YouTube HTTP {status}: {reason or e}")
        from google.auth.exceptions import RefreshError
        if isinstance(e, RefreshError):
            return AuthExpiredError(f"YouTube refresh token ogiltig: {e}")
        return e

    def upload(self, video: Path, meta: VideoMetadata, clip_key: str) -> UploadResult:
        body = {
            "snippet": {
                "title": meta.title[:100],
                "description": meta.description[:5000],
                "tags": [t.lstrip("#") for t in meta.hashtags] + ["GTA 6", "Shorts", clip_ref(clip_key)],
                "categoryId": self.cfg.category_id,
            },
            "status": {
                "privacyStatus": self.cfg.privacy_status,
                "selfDeclaredMadeForKids": self.cfg.made_for_kids,
                "embeddable": True,
            },
        }
        request = self.yt.videos().insert(part="snippet,status", body=body,
                                          media_body=self.media_factory(video), notifySubscribers=True)
        response = None
        attempt = 0
        while response is None:
            try:
                _, response = request.next_chunk()
            except Exception as e:  # noqa: BLE001
                err = self._classify(e)
                retriable = err is e and (
                    getattr(getattr(e, "resp", None), "status", None) in RETRIABLE_STATUS
                    or isinstance(e, (ConnectionError, TimeoutError, OSError))
                )
                if not retriable:
                    raise err from e
                attempt += 1
                if attempt > self.cfg.max_retries:
                    raise PublishError(f"YouTube-uppladdning gav upp efter {attempt - 1} försök: {e}") from e
                delay = min(2 ** attempt + random.random(), 64)
                log.warning("uppladdningsfel, försöker igen", extra={"attempt": attempt, "delay": round(delay, 1),
                                                                      "error": str(e)[:200]})
                self.sleep(delay)
        vid = response.get("id")
        if not vid:
            raise PublishError(f"oväntat svar från YouTube: {response}")
        privacy = (response.get("status") or {}).get("privacyStatus", self.cfg.privacy_status)
        locked = self.cfg.privacy_status != "private" and privacy == "private"
        if locked:
            log.warning("VIDEON BLEV LÅST SOM PRIVAT – API-projektet är inte godkänt i YouTube API "
                        "Compliance Audit ännu. Se README avsnitt 3.", extra={"video_id": vid})
        log.info("uppladdad till youtube", extra={"video_id": vid, "privacy": privacy, "clip": clip_key})
        return UploadResult(vid, f"https://youtube.com/shorts/{vid}", privacy, locked)

    def find_existing(self, clip_key: str) -> UploadResult | None:
        ref = clip_ref(clip_key)
        try:
            ch = self.yt.channels().list(part="contentDetails", mine=True).execute()
            uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
            items = self.yt.playlistItems().list(part="contentDetails", playlistId=uploads,
                                                 maxResults=25).execute().get("items", [])
            ids = [i["contentDetails"]["videoId"] for i in items]
            if not ids:
                return None
            vids = self.yt.videos().list(part="snippet,status", id=",".join(ids)).execute().get("items", [])
        except Exception as e:  # noqa: BLE001
            err = self._classify(e)
            if isinstance(err, (AuthExpiredError, QuotaExceededError)):
                raise err from e
            raise PublishError(f"kunde inte kontrollera befintliga uppladdningar: {e}") from e
        for v in vids:
            if ref in (v.get("snippet", {}).get("tags") or []):
                privacy = v.get("status", {}).get("privacyStatus", "private")
                return UploadResult(v["id"], f"https://youtube.com/shorts/{v['id']}", privacy)
        return None

    # rejectionReason som tyder på upphovsrätt/policy (allvarligt). "duplicate"/"length" är tekniska.
    SEVERE_REJECTIONS = {"copyright", "legal", "trademark", "termsOfUse", "inappropriate",
                         "uploaderAccountSuspended", "uploaderAccountClosed", "claim"}

    def check_health(self, external_ids: list[str]) -> dict[str, VideoHealth]:
        out: dict[str, VideoHealth] = {}
        for i in range(0, len(external_ids), 50):
            batch = external_ids[i:i + 50]
            try:
                items = self.yt.videos().list(part="status,contentDetails", id=",".join(batch),
                                              maxResults=50).execute().get("items", [])
            except Exception as e:  # noqa: BLE001
                err = self._classify(e)
                if isinstance(err, (AuthExpiredError, QuotaExceededError)):
                    raise err from e
                raise PublishError(f"kunde inte kontrollera videostatus: {e}") from e
            found = {v["id"]: v for v in items}
            for vid in batch:
                v = found.get(vid)
                if v is None:
                    out[vid] = VideoHealth("removed", "videon finns inte längre (borttagen av YouTube eller manuellt)")
                    continue
                st, cd = v.get("status", {}), v.get("contentDetails", {})
                privacy = st.get("privacyStatus", "")
                if st.get("uploadStatus") == "rejected":
                    reason = st.get("rejectionReason", "okänd")
                    out[vid] = VideoHealth("rejected" if reason in self.SEVERE_REJECTIONS else "ok", f"avvisad: {reason}", privacy)
                elif (cd.get("regionRestriction") or {}).get("blocked"):
                    n = len(cd["regionRestriction"]["blocked"])
                    out[vid] = VideoHealth("claimed", f"blockerad i {n} länder (troligen musik-/upphovsrättsanspråk)", privacy)
                elif (cd.get("contentRating") or {}).get("ytRating") == "ytAgeRestricted":
                    out[vid] = VideoHealth("age_restricted", "åldersbegränsad av YouTube", privacy)
                else:
                    out[vid] = VideoHealth("ok", privacy=privacy)
        return out
