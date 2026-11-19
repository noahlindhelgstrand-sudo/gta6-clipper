"""Kampanjstreamers hämtas direkt från sin kanal (för små för kategorins topplista)."""
from datetime import timedelta
from unittest.mock import MagicMock

from clipper.config import CampaignCfg, SelectionCfg, StreamersCfg
from clipper.pipeline import Pipeline
from clipper.selection import select
from clipper.sources.kick import KickSource
from clipper.sources.twitch import TwitchSource
from conftest import NOW, make_clip
from test_e2e_dryrun import make_deps
from test_sources_storage_config import resp


def clip(i, game):
    return {"id": f"c{i}", "url": f"https://clips.twitch.tv/c{i}", "broadcaster_name": "Dasgasd3m", "title": "t",
            "view_count": 15, "created_at": "2026-11-19T10:00:00Z", "duration": 30.0, "language": "en",
            "game_id": game}


def test_twitch_channel_clips_only_gta():
    http = MagicMock()
    http.post.return_value = resp(data={"access_token": "t"})
    http.get.side_effect = [resp(data={"data": [{"id": "42"}]}),          # /users
                            resp(data={"data": [{"id": "999"}]}),         # /games
                            resp(data={"data": [clip(1, "999"), clip(2, "111")]})]
    out = TwitchSource("id", "s", "Grand Theft Auto VI", session=http).fetch_channel_clips(
        "Dasgasd3m", NOW - timedelta(days=1), NOW)
    assert [c.clip_id for c in out] == ["c1"] and out[0].streamer == "dasgasd3m"
    assert http.get.call_args_list[2].kwargs["params"]["broadcaster_id"] == "42"


def test_kick_channel_clips_fill_in_channel():
    http = MagicMock()
    http.get.return_value = resp(data={"clips": [
        {"id": "clip_1", "views": 5, "duration": 20, "created_at": "2026-11-20T09:00:00Z",
         "category": {"slug": "grand-theft-auto-vi"}},
        {"id": "clip_2", "views": 5, "duration": 20, "created_at": "2026-11-20T09:00:00Z",
         "category": {"slug": "just-chatting"}}]})
    out = KickSource("grand-theft-auto-vi", session=http).fetch_channel_clips("Dasgasd3m", NOW - timedelta(days=1), NOW)
    assert [c.clip_id for c in out] == ["clip_1"]
    assert out[0].streamer == "dasgasd3m" and out[0].url == "https://kick.com/dasgasd3m/clips/clip_1"


def test_campaign_min_views_override():
    sel = SelectionCfg(min_views=200, max_candidates_per_run=5)
    small = make_clip("s", "dasgasd3m", views=15)
    got = select([small], sel, StreamersCfg(), {"twitch:dasgasd3m"}, set(), None, NOW, None, {"twitch:dasgasd3m": 10})
    assert [c.clip_id for c in got] == ["s"]
    assert select([small], sel, StreamersCfg(), {"twitch:dasgasd3m"}, set(), None, NOW) == []


def test_pipeline_asks_sources_for_campaign_channels(cfg):
    cfg.campaigns = [CampaignCfg(name="D", streamers=["twitch:dasgasd3m", "kick:dasgasd3m"], min_views=10)]
    deps = make_deps(cfg, {"t": NOW})
    asked = []
    for src in deps.sources:
        src.fetch_channel_clips = lambda ch, s, e, max_clips=100, p=src.platform: asked.append((p, ch)) or []
    with Pipeline(cfg, deps) as p:
        p._fetch_campaign_channels(NOW - timedelta(hours=24), NOW)
    assert sorted(asked) == [("kick", "dasgasd3m"), ("twitch", "dasgasd3m")]
