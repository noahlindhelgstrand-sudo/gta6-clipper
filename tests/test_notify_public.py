"""Push-notiser: direkt vid uppladdning och när en kampanjvideo blivit publik."""
import shutil
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from clipper.config import CampaignCfg
from clipper.notify import Notifier
from clipper.pipeline import Pipeline
from clipper.publishers.base import VideoHealth
from clipper.publishers.dryrun import DryRunPublisher
from test_e2e_dryrun import make_deps


def test_ntfy_payload_has_click_and_priority():
    http = MagicMock()
    http.post.return_value = MagicMock(status_code=200)
    n = Notifier(None, session=http, ntfy_topic="gta6clip-test")
    n.send("success", "📹 Ny video", "Gör den publik", click="https://studio.youtube.com/video/x/edit", priority=5)
    (url,), kw = http.post.call_args
    assert url == "https://ntfy.sh"
    body = kw["json"]
    assert body["topic"] == "gta6clip-test" and body["priority"] == 5
    assert body["click"] == "https://studio.youtube.com/video/x/edit" and body["title"] == "📹 Ny video"


def test_no_network_when_nothing_configured():
    http = MagicMock()
    Notifier(None, session=http).send("info", "x")
    http.post.assert_not_called()


class FlipPublisher(DryRunPublisher):
    """Laddar upp privat; 'användaren' gör sedan videon publik."""
    def __init__(self):
        super().__init__(privacy="private")
        self.public: set[str] = set()

    def check_health(self, ids):
        return {i: VideoHealth("ok", privacy="public" if i in self.public else "private") for i in ids}


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg saknas")
def test_notifies_on_upload_and_once_when_public(cfg):
    cfg.campaigns = [CampaignCfg(name="Alpha camp", streamers=["alpha_streams"], url="https://camp.invalid", boost=50)]
    pub = FlipPublisher()
    clock = {"t": datetime(2026, 11, 20, 0, 7, tzinfo=timezone.utc)}
    deps = make_deps(cfg, clock, pub)
    with Pipeline(cfg, deps) as p:
        p.prepare()
    clock["t"] = datetime(2026, 11, 20, 11, 7, tzinfo=timezone.utc)
    with Pipeline(cfg, deps) as p:
        p.publish()
        vid = p.db.conn.execute("SELECT external_id FROM posts").fetchone()[0]
    titles = lambda: [e["embeds"][0]["title"] for e in deps.notifier.sent]  # noqa: E731
    assert any("Ny video uppladdad" in t for t in titles())
    pub.public.add(vid)                                       # användaren gör den publik
    for _ in range(2):
        clock["t"] += timedelta(minutes=60)
        with Pipeline(cfg, deps) as p:
            p.publish()
    assert sum("Publik – skicka in till Alpha camp" in t for t in titles()) == 1

