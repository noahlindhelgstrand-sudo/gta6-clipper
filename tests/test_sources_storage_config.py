from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from clipper.config import load_config
from clipper.sources.base import SourceError
from clipper.sources.kick import KickSource
from clipper.sources.twitch import TwitchSource
from clipper.storage.github_release import GitHubReleaseStore
from clipper.storage.local import LocalStore

T0 = datetime(2026, 11, 20, tzinfo=timezone.utc)


def resp(status=200, data=None, headers=None):
    r = MagicMock(status_code=status, headers=headers or {}, text=str(data))
    r.json.return_value = data
    return r


def twitch_clip(i):
    return {"id": f"c{i}", "url": f"https://clips.twitch.tv/c{i}", "broadcaster_name": "Alice",
            "title": "t", "view_count": 100 * i, "created_at": "2026-11-19T10:00:00Z",
            "duration": 30.0, "language": "en"}


def test_twitch_token_game_and_paginated_clips():
    http = MagicMock()
    http.post.return_value = resp(data={"access_token": "tok"})
    http.get.side_effect = [
        resp(data={"data": [{"id": "999"}]}),
        resp(data={"data": [twitch_clip(1), twitch_clip(2)], "pagination": {"cursor": "abc"}}),
        resp(data={"data": [twitch_clip(3)], "pagination": {}}),
    ]
    src = TwitchSource("id", "secret", "Grand Theft Auto VI", session=http)
    clips = src.fetch_clips(T0 - timedelta(days=1), T0)
    assert [c.clip_id for c in clips] == ["c1", "c2", "c3"]
    assert clips[0].streamer == "alice" and clips[0].key == "twitch:c1"
    params = http.get.call_args_list[2].kwargs["params"]
    assert params["after"] == "abc" and params["game_id"] == "999"
    assert params["started_at"] == "2026-11-19T00:00:00Z"
    assert http.get.call_args_list[1].kwargs["headers"]["Authorization"] == "Bearer tok"


def test_twitch_refreshes_expired_token():
    http = MagicMock()
    http.post.side_effect = [resp(data={"access_token": "old"}), resp(data={"access_token": "new"})]
    http.get.side_effect = [resp(401, {}), resp(data={"data": [{"id": "1"}]})]
    assert TwitchSource("id", "s", "g", session=http).game_id() == "1"
    assert http.post.call_count == 2


def test_twitch_unknown_game():
    http = MagicMock()
    http.post.return_value = resp(data={"access_token": "t"})
    http.get.return_value = resp(data={"data": []})
    with pytest.raises(SourceError):
        TwitchSource("id", "s", "Nope", session=http).game_id()


def test_twitch_requires_secrets():
    with pytest.raises(SourceError):
        TwitchSource("", "", "g")


def test_kick_parses_and_raises_on_cloudflare():
    http = MagicMock()
    http.get.return_value = resp(data={"clips": [{
        "id": "clip_01X", "title": "wow", "views": 500, "duration": 30,
        "created_at": "2026-11-19T12:00:00.000000Z", "channel": {"slug": "bob", "username": "Bob"}}],
        "nextCursor": None})
    clips = KickSource("grand-theft-auto-vi", session=http).fetch_clips(T0 - timedelta(days=1), T0)
    assert clips[0].url == "https://kick.com/bob/clips/clip_01X" and clips[0].key == "kick:clip_01X"
    http.get.return_value = resp(403, None)
    with pytest.raises(SourceError):
        KickSource("x", session=http).fetch_clips(T0 - timedelta(days=1), T0)


def test_github_store_uploads_new_db_before_deleting_old(tmp_path):
    http = MagicMock()
    http.headers = {}
    calls = []
    assets = [{"id": 1, "name": "state-000000000000001.db"}, {"id": 2, "name": "a.mp4"}]

    def request(method, url, **kw):
        calls.append((method, url.split("/")[-1], kw.get("params")))
        if method == "GET":
            return resp(200, assets)
        return resp(201 if method == "POST" else 204, {})

    http.get.return_value = resp(200, {"id": 42})
    http.request.side_effect = request
    store = GitHubReleaseStore("me/repo", "tok", session=http)
    db = tmp_path / "state.db"
    db.write_bytes(b"x")
    store.push_db(db)
    methods = [c[0] for c in calls]
    assert methods.index("POST") < methods.index("DELETE")
    assert ("DELETE", "1", None) in calls
    assert store.list_videos() == ["a.mp4"]


def test_local_store_roundtrip(tmp_path):
    s = LocalStore(tmp_path / "d")
    assert s.pull_db(tmp_path / "x.db") is False
    f = tmp_path / "v.mp4"
    f.write_bytes(b"123")
    s.put_video(f, "v.mp4")
    assert s.list_videos() == ["v.mp4"]
    s.delete_video("v.mp4")
    assert s.list_videos() == []


def test_config_env_override(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("publish:\n  privacy_status: private\n")
    cfg = load_config(p, environ={"CLIPPER__PUBLISH__PRIVACY_STATUS": "public",
                                  "CLIPPER__SCHEDULE__SLOTS": '["09:00","13:00","19:00"]'})
    assert cfg.publish.privacy_status == "public"
    assert cfg.schedule.slots == ["09:00", "13:00", "19:00"]


def test_repo_config_is_valid():
    cfg = load_config("config.yaml", environ={})
    assert len(cfg.schedule.slots) == 3 and cfg.language == "en"
    assert cfg.publish.privacy_status == "private"
