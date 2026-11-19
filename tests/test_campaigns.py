"""Kampanjläge: kampanjstreamers prioriteras och får kampanjens hashtags/omnämnande."""
import shutil
from datetime import datetime, timedelta, timezone

import pytest

from clipper.config import CampaignCfg, MetadataCfg, SelectionCfg, StreamersCfg, campaign_for
from clipper.metadata import apply_campaign, fallback
from clipper.pipeline import Pipeline
from clipper.selection import select
from conftest import NOW, make_clip
from test_e2e_dryrun import make_deps

LACY = CampaignCfg(name="Lacy GTA 6", streamers=["lacy"], hashtags=["#lacy", "contentrewards"],
                   mention="Clip from @Lacy", boost=3.0)


def test_streamer_name_normalized_and_lookup(cfg):
    assert LACY.streamers == ["twitch:lacy"]
    cfg.campaigns = [LACY]
    assert campaign_for(cfg, "twitch:lacy") is LACY
    assert campaign_for(cfg, "twitch:xqc") is None


def test_campaign_boost_beats_higher_velocity():
    sel = SelectionCfg(max_candidates_per_run=2, max_per_streamer_per_day=1)
    clips = [make_clip("x", "xqc", views=2000, hours_ago=1), make_clip("l", "lacy", views=1000, hours_ago=1)]
    got = select(clips, sel, StreamersCfg(), {"twitch:xqc", "twitch:lacy"}, set(), None, NOW, {"twitch:lacy": 3.0})
    assert [c.streamer for c in got] == ["lacy", "xqc"]


def test_apply_campaign_adds_required_hashtags_and_mention():
    m = apply_campaign(fallback(make_clip("l", "lacy"), MetadataCfg()), LACY)
    assert m.hashtags[:2] == ["#lacy", "#contentrewards"]
    assert m.description.startswith("Clip from @Lacy")
    assert "#lacy" in m.description and "#contentrewards" in m.description
    assert "Original clip:" in m.description                    # credit finns kvar


def test_campaign_streamer_allowed_even_if_not_top(cfg):
    cfg.campaigns = [LACY]
    cfg.streamers.policy = "allowlist"
    with Pipeline(cfg, make_deps(cfg, {"t": NOW})) as p:
        assert "twitch:lacy" in p.allowed()
        cfg.streamers.blocklist = ["twitch:lacy"]                # blocklist vinner alltid
        assert "twitch:lacy" not in p.allowed()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg saknas")
def test_campaign_upload_writes_actions_summary(cfg, tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    cfg.campaigns = [CampaignCfg(name="Alpha camp", streamers=["alpha_streams"], url="https://example.invalid/c",
                                 hashtags=["#alpha"], boost=50)]
    clock = {"t": datetime(2026, 11, 20, 0, 7, tzinfo=timezone.utc)}
    deps = make_deps(cfg, clock)
    with Pipeline(cfg, deps) as p:
        p.prepare()
        top = p.db.rendered_queue()[0]
        assert top["streamer"] == "alpha_streams"                # kampanjen först i kön
        assert "#alpha" in p.db.meta_of(top).description
    clock["t"] = datetime(2026, 11, 20, 11, 7, tzinfo=timezone.utc)
    with Pipeline(cfg, deps) as p:
        assert p.publish()["posted"]
    text = summary.read_text()
    assert "Alpha camp" in text and "youtube.com/shorts/" in text
    up = [e for e in deps.notifier.sent if "Ny video uppladdad" in e["embeds"][0]["title"]]
    assert up and "kampanj" in up[0]["embeds"][0]["title"]
    assert up[0]["embeds"][0]["url"].startswith("https://studio.youtube.com/video/")


def test_stuck_manual_upload_goes_back_to_queue(cfg):
    clock = {"t": datetime(2026, 11, 20, 0, 7, tzinfo=timezone.utc)}
    deps = make_deps(cfg, clock)
    with Pipeline(cfg, deps) as p:
        assert p._slot_still_open("manual 2026-11-20 09:30:00") is False
