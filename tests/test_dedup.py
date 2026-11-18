from clipper.config import SelectionCfg, StreamersCfg
from clipper.models import VideoMetadata
from clipper.selection import select
from conftest import NOW, make_clip


def test_insert_is_idempotent(db):
    c = make_clip("x")
    assert db.insert_candidate(c) is True
    assert db.insert_candidate(c) is False
    assert db.known_keys() == {"twitch:x"}


def test_same_id_different_platform_is_distinct(db):
    assert db.insert_candidate(make_clip("x", platform="twitch"))
    assert db.insert_candidate(make_clip("x", platform="kick"))
    assert len(db.known_keys()) == 2


def test_known_clips_are_ignored_by_selection(db):
    db.insert_candidate(make_clip("seen"))
    clips = [make_clip("seen"), make_clip("new")]
    got = select(clips, SelectionCfg(max_candidates_per_run=10, max_per_streamer_per_day=10),
                 StreamersCfg(), {"twitch:alice"}, db.known_keys(), None, NOW)
    assert [c.clip_id for c in got] == ["new"]


def test_duplicates_within_one_batch_removed():
    got = select([make_clip("d"), make_clip("d")],
                 SelectionCfg(max_candidates_per_run=10, max_per_streamer_per_day=10),
                 StreamersCfg(), {"twitch:alice"}, set(), None, NOW)
    assert len(got) == 1


def _render(db, cid, score=1.0):
    c = make_clip(cid)
    c.score = score
    db.insert_candidate(c)
    db.mark_rendered(c.key, f"{cid}.mp4", "hi", VideoMetadata("t", "d"))
    return c.key


def test_claim_slot_never_twice(db):
    k1 = _render(db, "a")
    k2 = _render(db, "b")
    assert db.claim_slot("2026-11-20 12:00", k1) is True
    # samma slot igen (retry/omstart) -> nej
    assert db.claim_slot("2026-11-20 12:00", k2) is False
    # samma klipp på ny slot -> nej (är inte längre 'rendered')
    assert db.claim_slot("2026-11-20 17:00", k1) is False


def test_posted_clip_cannot_be_claimed_again(db):
    k = _render(db, "a")
    db.claim_slot("s1", k)
    db.record_post(k, "youtube", "vid1", "https://youtu.be/vid1", "private", "s1")
    db.record_post(k, "youtube", "vid1", "https://youtu.be/vid1", "private", "s1")  # idempotent
    assert db.is_posted(k, "youtube")
    db.set_status(k, "rendered")  # även om någon "återställer" status
    assert db.claim_slot("s2", k) is False
    assert len(db.posts_between("0", "9")) == 1
