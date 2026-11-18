"""End-to-end i dry-run: mockad Twitch/Kick, mockad Claude, mockad YouTube, riktig ffmpeg."""
import shutil
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from clipper import mocks
from clipper.notify import Notifier
from clipper.pipeline import Deps, Pipeline
from clipper.publishers.base import PublishError
from clipper.publishers.dryrun import DryRunPublisher
from clipper.render import probe
from clipper.storage.local import LocalStore

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg saknas")


def make_deps(cfg, clock, publisher=None):
    now = lambda: clock["t"]  # noqa: E731
    cfg.render.preset = "ultrafast"
    cfg.render.crf = 34
    return Deps(sources=[mocks.FakeSource("twitch", now), mocks.FakeSource("kick", now)],
                publishers=[publisher or DryRunPublisher()], store=LocalStore(cfg.storage.local_dir),
                notifier=Notifier(None, dry_run=True), now=now,
                download=lambda url, d, n: mocks.fake_download(url, d, n, seconds=3),
                transcribe=mocks.fake_transcribe, anthropic_client=mocks.FakeAnthropic())


def test_two_days_end_to_end(cfg):
    cfg.queue.buffer_target = 4
    cfg.queue.buffer_min = 2
    clock = {"t": datetime(2026, 11, 20, 0, 7, tzinfo=timezone.utc)}
    deps = make_deps(cfg, clock)
    for _ in range(48):
        with Pipeline(cfg, deps) as p:
            p.prepare()
        with Pipeline(cfg, deps) as p:
            p.publish()
        clock["t"] += timedelta(hours=1)

    pub = deps.publishers[0]
    keys = [k for k, _ in pub.uploaded]
    assert len(keys) == len(set(keys)), "samma klipp postat två gånger"
    with Pipeline(cfg, deps) as p:
        posts = p.db.posts_between("0", "9")
        tz = ZoneInfo(cfg.timezone)
        per_day = Counter(datetime.fromisoformat(r["posted_at"]).astimezone(tz).date() for r in posts)
        assert per_day[datetime(2026, 11, 20).date()] == 3
        assert per_day[datetime(2026, 11, 21).date()] == 3
        assert p.db.count_status("rendered") >= 1
        # renderade videor i lagringen är rätt format och postade har tagits bort
        vids = deps.store.list_videos()
        assert len(vids) == p.db.count_status("rendered")
        info = probe(deps.store.root / "videos" / vids[0])
        assert (info.width, info.height) == (1080, 1920)
    # daglig sammanfattning skickades
    assert any("Daglig sammanfattning" in e["embeds"][0]["title"] for e in deps.notifier.sent)
    # alla metadata har credit
    assert all("Original clip:" in m.description for _, m in pub.uploaded)


class CrashingPublisher(DryRunPublisher):
    """Laddar upp men 'kraschar' innan svaret kommer fram – första gången."""
    def __init__(self):
        super().__init__()
        self.remote: dict[str, str] = {}
        self.crash_next = True

    def upload(self, video, meta, clip_key):
        res = super().upload(video, meta, clip_key)
        self.remote[clip_key] = res.external_id
        if self.crash_next:
            self.crash_next = False
            raise PublishError("timeout efter att uppladdningen gått igenom")
        return res

    def find_existing(self, clip_key):
        from clipper.publishers.base import UploadResult
        vid = self.remote.get(clip_key)
        return UploadResult(vid, f"https://youtube.com/shorts/{vid}", "private") if vid else None


def test_crash_after_upload_does_not_double_post(cfg):
    clock = {"t": datetime(2026, 11, 20, 0, 7, tzinfo=timezone.utc)}
    pub = CrashingPublisher()
    deps = make_deps(cfg, clock, pub)
    with Pipeline(cfg, deps) as p:
        p.prepare()
    clock["t"] = datetime(2026, 11, 20, 11, 7, tzinfo=timezone.utc)   # 12:07 lokal (CET)
    with Pipeline(cfg, deps) as p:
        assert p.publish()["posted"] is None                           # "kraschade"
    clock["t"] += timedelta(minutes=30)
    with Pipeline(cfg, deps) as p:
        p.publish()                                                    # återhämtning
        assert len(p.db.posts_between("0", "9")) == 1
    assert len(pub.uploaded) == 1, "återhämtningen laddade upp igen trots att videon fanns"


def test_empty_queue_notifies_once(cfg):
    clock = {"t": datetime(2026, 11, 20, 11, 7, tzinfo=timezone.utc)}
    deps = make_deps(cfg, clock)
    for _ in range(3):
        with Pipeline(cfg, deps) as p:
            p.publish()
        clock["t"] += timedelta(minutes=20)
    titles = [e["embeds"][0]["title"] for e in deps.notifier.sent]
    assert titles.count("❌ Tom kö") == 1
