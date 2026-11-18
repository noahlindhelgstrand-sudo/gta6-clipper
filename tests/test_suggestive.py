"""Högst 1 suggestivt klipp (strippklubb o.d.) i kön och per dag."""
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from clipper import mocks
from clipper.config import MetadataCfg
from clipper.metadata import fallback, generate, looks_suggestive
from clipper.models import VideoMetadata
from clipper.pipeline import Pipeline
from clipper.render import extract_frames
from conftest import make_clip
from test_e2e_dryrun import make_deps
from test_render import synth_video

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg saknas")


def test_keyword_fallback_detection():
    assert looks_suggestive("insane lap dance in GTA 6")
    assert looks_suggestive("", "bro we're in the strip club")
    assert not looks_suggestive("police chase on the highway", "no way bro")
    assert not looks_suggestive("Essex is crazy")             # "sex" bara som helt ord


def test_claude_receives_frames_and_flag():
    client = mocks.FakeAnthropic(suggestive=True)
    m = generate(make_clip(), "hi", MetadataCfg(), "en", None, client=client, frames=[b"\xff\xd8jpeg1", b"\xff\xd8jpeg2"])
    assert m.suggestive is True
    content = client.last_kwargs["messages"][0]["content"]
    assert [b["type"] for b in content] == ["image", "image", "text"]


def test_fallback_title_ignores_junk_clip_titles():
    c = make_clip("x", "zoil")
    c.title = "wtf"
    assert fallback(c, MetadataCfg()).title.startswith("Zoil in GTA 6")
    c.title = "Angry Hooker in the car"
    assert fallback(c, MetadataCfg()).title.startswith("Zoil in GTA 6")
    c.title = "he crashed the police helicopter"
    assert fallback(c, MetadataCfg()).title.startswith("he crashed the police helicopter – Zoil")


def test_extract_frames(tmp_path):
    frames = extract_frames(synth_video(tmp_path / "v.mp4", seconds=4), n=3)
    assert len(frames) == 3 and all(f[:2] == b"\xff\xd8" for f in frames)


def _deps_with_suggestive(cfg, clock, suggestive_streamers):
    deps = make_deps(cfg, clock)

    def meta(c, transcript, mcfg, lang, key, client=None, frames=None):
        m = fallback(c, mcfg, lang)
        m.suggestive = c.streamer in suggestive_streamers
        return m
    deps.metadata = meta
    deps.frames = lambda p: []
    return deps


def test_queue_holds_max_one_suggestive(cfg):
    clock = {"t": datetime(2026, 11, 20, 0, 7, tzinfo=timezone.utc)}
    deps = _deps_with_suggestive(cfg, clock, {"alpha_streams", "bravo_tv", "echo_kick"})
    for _ in range(4):
        with Pipeline(cfg, deps) as p:
            p.prepare()
        clock["t"] += timedelta(hours=1)
    with Pipeline(cfg, deps) as p:
        assert p.suggestive_in_queue() <= 1
        assert p.db.count_status("rendered") >= 3            # kön fylls med annat innehåll


def test_max_one_suggestive_upload_per_day(cfg, db):
    with Pipeline(cfg, make_deps(cfg, {"t": datetime(2026, 11, 20, 10, 7, tzinfo=timezone.utc)})) as p:
        for i, sug in enumerate([True, True, False, True]):
            c = make_clip(f"c{i}")
            c.score = 100 - i                                  # suggestiva rankas högst
            p.db.insert_candidate(c)
            p.db.mark_rendered(c.key, f"c{i}.mp4", "", VideoMetadata("t", "d", suggestive=sug))
        first = p.pick_for_upload()
        assert first["key"] == "twitch:c0"                    # ett suggestivt är okej
        p.db.claim_slot("s1", first["key"])
        p.db.record_post(first["key"], "youtube", "v", "u", "private", "s1")
        assert p.pick_for_upload()["key"] == "twitch:c2"      # sedan bara icke-suggestiva
