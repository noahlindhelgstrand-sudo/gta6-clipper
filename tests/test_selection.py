from clipper.config import SelectionCfg, StreamersCfg
from clipper.selection import aggregate_streamers, allowed_streamers, rank, select, velocity
from conftest import NOW, make_clip


def test_velocity_views_per_hour():
    assert velocity(make_clip(views=1000, hours_ago=4), NOW) == 250
    # färska klipp: nämnaren minst 0,5 h
    assert velocity(make_clip(views=100, hours_ago=0.01), NOW) == 200


def test_rank_by_velocity_not_total_views():
    old_big = make_clip("a", views=5000, hours_ago=20)    # 250/h
    new_small = make_clip("b", views=1500, hours_ago=2)   # 750/h
    ranked = rank([old_big, new_small], NOW)
    assert [c.clip_id for c in ranked] == ["b", "a"]
    assert ranked[0].score == 750


def test_filters_duration_views_language():
    sel = SelectionCfg(min_views=200, min_duration_s=15, max_duration_s=60, max_candidates_per_run=10,
                       max_per_streamer_per_day=10)
    st = StreamersCfg(policy="auto")
    allowed = {"twitch:alice"}
    clips = [
        make_clip("ok"),
        make_clip("short", dur=10),
        make_clip("long", dur=61),
        make_clip("few", views=199),
        make_clip("de", lang="de"),
        make_clip("edge15", dur=15), make_clip("edge60", dur=60),
    ]
    got = select(clips, sel, st, allowed, set(), {"twitch": ["en"]}, NOW)
    assert sorted(c.clip_id for c in got) == ["edge15", "edge60", "ok"]


def test_allowlist_policy_only_allowlisted():
    st = StreamersCfg(policy="allowlist", allowlist=["alice"])
    allowed = allowed_streamers(st, discovered_top=["twitch:bob"])
    assert allowed == {"twitch:alice"}


def test_auto_policy_union_minus_blocklist():
    st = StreamersCfg(policy="auto", allowlist=["kick:carol"], blocklist=["twitch:bob"])
    allowed = allowed_streamers(st, discovered_top=["twitch:bob", "twitch:alice"])
    assert allowed == {"kick:carol", "twitch:alice"}


def test_blocklist_wins_even_if_allowed():
    sel = SelectionCfg(max_candidates_per_run=10)
    st = StreamersCfg(blocklist=["twitch:alice"])
    got = select([make_clip()], sel, st, {"twitch:alice"}, set(), None, NOW)
    assert got == []


def test_not_allowed_streamer_excluded():
    sel = SelectionCfg(max_candidates_per_run=10)
    got = select([make_clip(streamer="mallory")], sel, StreamersCfg(), {"twitch:alice"}, set(), None, NOW)
    assert got == []


def test_max_per_streamer_and_limit():
    sel = SelectionCfg(max_candidates_per_run=2, max_per_streamer_per_day=1)
    clips = [make_clip("a1", "alice", 5000), make_clip("a2", "alice", 4000), make_clip("b1", "bob", 100 * 3)]
    got = select(clips, sel, StreamersCfg(), {"twitch:alice", "twitch:bob"}, set(), None, NOW)
    assert [c.clip_id for c in got] == ["a1", "b1"]


def test_aggregate_streamers():
    stats = aggregate_streamers([make_clip("1", "alice", 10), make_clip("2", "alice", 5),
                                 make_clip("3", "bob", 7, platform="kick")])
    assert stats["twitch:alice"][1:] == (15, 2)
    assert stats["kick:bob"][1:] == (7, 1)
