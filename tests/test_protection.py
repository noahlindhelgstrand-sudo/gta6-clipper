"""Copyright-skydd: borttagen/avvisad video => paus + blockerad streamer."""
import shutil
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from clipper.config import PublishCfg
from clipper.pipeline import Pipeline
from clipper.publishers.base import VideoHealth
from clipper.publishers.dryrun import DryRunPublisher
from clipper.publishers.youtube import YouTubePublisher
from test_e2e_dryrun import make_deps

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg saknas")


def test_youtube_check_health_states():
    yt = MagicMock()
    yt.videos.return_value.list.return_value.execute.return_value = {"items": [
        {"id": "ok1", "status": {"uploadStatus": "processed"}, "contentDetails": {}},
        {"id": "cr", "status": {"uploadStatus": "rejected", "rejectionReason": "copyright"}, "contentDetails": {}},
        {"id": "dup", "status": {"uploadStatus": "rejected", "rejectionReason": "duplicate"}, "contentDetails": {}},
        {"id": "music", "status": {"uploadStatus": "processed"},
         "contentDetails": {"regionRestriction": {"blocked": ["DE", "US"]}}},
        {"id": "age", "status": {"uploadStatus": "processed"},
         "contentDetails": {"contentRating": {"ytRating": "ytAgeRestricted"}}},
    ]}
    pub = YouTubePublisher(yt, PublishCfg(), media_factory=lambda p: None)
    h = pub.check_health(["ok1", "cr", "dup", "music", "age", "gone"])
    assert {k: v.state for k, v in h.items()} == {
        "ok1": "ok", "cr": "rejected", "dup": "ok", "music": "claimed", "age": "age_restricted", "gone": "removed"}
    assert h["cr"].severe and h["gone"].severe and not h["music"].severe


class StrikePublisher(DryRunPublisher):
    """Laddar upp som vanligt, men första videon 'tas bort' av YouTube efteråt."""
    def __init__(self):
        super().__init__()
        self.removed: set[str] = set()

    def check_health(self, ids):
        return {i: VideoHealth("removed", "borttagen") if i in self.removed else VideoHealth("ok") for i in ids}


def test_strike_pauses_uploads_and_blocks_streamer(cfg):
    clock = {"t": datetime(2026, 11, 20, 0, 7, tzinfo=timezone.utc)}
    pub = StrikePublisher()
    deps = make_deps(cfg, clock, pub)
    with Pipeline(cfg, deps) as p:
        p.prepare()
    clock["t"] = datetime(2026, 11, 20, 11, 7, tzinfo=timezone.utc)     # 12:07 lokal
    with Pipeline(cfg, deps) as p:
        first = p.publish()["posted"]
    assert first
    with Pipeline(cfg, deps) as p:
        row = p.db.conn.execute("SELECT external_id, clip_key FROM posts").fetchone()
        streamer = p.db.conn.execute("SELECT platform||':'||streamer FROM clips WHERE key=?",
                                     (row["clip_key"],)).fetchone()[0]
    pub.removed.add(row["external_id"])                                  # "copyright-strike"
    clock["t"] = datetime(2026, 11, 20, 16, 7, tzinfo=timezone.utc)     # 17:07 lokal
    with Pipeline(cfg, deps) as p:
        r = p.publish()
        assert r["posted"] is None and r["paused"]
        assert streamer in p.auto_blocked() and streamer not in p.allowed()
        assert all(f"{q['platform']}:{q['streamer']}" != streamer for q in p.db.rendered_queue())
    titles = [e["embeds"][0]["title"] for e in deps.notifier.sent]
    assert any("PAUSADE" in t for t in titles)
    assert len(pub.uploaded) == 1

    # samma problem rapporteras inte igen, och resume släpper pausen
    clock["t"] += timedelta(minutes=30)
    with Pipeline(cfg, deps) as p:
        p.publish()
        assert p.resume() is True
    n_before = sum("PAUSADE" in e["embeds"][0]["title"] for e in deps.notifier.sent)
    assert n_before == 1
    clock["t"] += timedelta(minutes=30)
    with Pipeline(cfg, deps) as p:
        assert p.publish()["posted"] is not None                          # igång igen
        assert p.unblock_streamer(streamer) is True
        assert streamer not in p.auto_blocked()


def test_music_claim_only_warns(cfg):
    clock = {"t": datetime(2026, 11, 20, 0, 7, tzinfo=timezone.utc)}

    class ClaimPub(DryRunPublisher):
        def check_health(self, ids):
            return {i: VideoHealth("claimed", "blockerad i 2 länder") for i in ids}
    deps = make_deps(cfg, clock, ClaimPub())
    with Pipeline(cfg, deps) as p:
        p.prepare()
    clock["t"] = datetime(2026, 11, 20, 11, 7, tzinfo=timezone.utc)
    with Pipeline(cfg, deps) as p:
        p.publish()
    clock["t"] = datetime(2026, 11, 20, 16, 7, tzinfo=timezone.utc)
    with Pipeline(cfg, deps) as p:
        assert p.publish()["posted"] is not None and not p.paused()
    assert any("anspråk" in e["embeds"][0]["title"] for e in deps.notifier.sent)
