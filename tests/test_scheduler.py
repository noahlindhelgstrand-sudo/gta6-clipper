from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from clipper.config import ScheduleCfg
from clipper.scheduler import due_slot, slots_for_day

TZ = "Europe/Stockholm"
SLOTS = ["12:00", "17:00", "20:30"]


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def simulate_day(day_start_utc, hours=30, step_min=60, offset_min=7, slots=SLOTS, grace=3):
    """Kör 'publish' som cron gör (var step_min minut) och returnera postade slots."""
    used: set[str] = set()
    posted = []
    t = day_start_utc + timedelta(minutes=offset_min)
    end = day_start_utc + timedelta(hours=hours)
    while t < end:
        s = due_slot(t, slots, TZ, grace, lambda k: k in used)
        if s:
            used.add(s.key)
            posted.append((s.key, t))
        t += timedelta(minutes=step_min)
    return posted


def test_winter_time_cet_offset():
    s = slots_for_day(date(2026, 1, 15), SLOTS, TZ)
    assert s[0].utc == utc(2026, 1, 15, 11, 0)     # CET = UTC+1


def test_summer_time_cest_offset():
    s = slots_for_day(date(2026, 7, 15), SLOTS, TZ)
    assert s[0].utc == utc(2026, 7, 15, 10, 0)     # CEST = UTC+2


@pytest.mark.parametrize("day", [date(2026, 1, 15), date(2026, 7, 15),
                                 date(2026, 3, 29), date(2026, 10, 25)])   # inkl. DST-skiftesdagar
def test_exactly_three_per_local_day(day):
    start = slots_for_day(day, ["00:00"], TZ)[0].utc
    posted = simulate_day(start, hours=24, step_min=60)
    keys = [k for k, _ in posted if k.startswith(day.isoformat())]
    assert keys == [f"{day} 12:00", f"{day} 17:00", f"{day} 20:30"]


@pytest.mark.parametrize("day", [date(2026, 1, 15), date(2026, 7, 15)])
def test_posts_at_correct_local_hour(day):
    start = slots_for_day(day, ["00:00"], TZ)[0].utc
    for key, t in simulate_day(start, hours=24):
        slot = next(s for s in slots_for_day(day, SLOTS, TZ) if s.key == key)
        assert timedelta(0) <= t - slot.utc < timedelta(hours=1)


def test_frequent_runs_never_double_post():
    start = slots_for_day(date(2026, 7, 15), ["00:00"], TZ)[0].utc
    posted = simulate_day(start, hours=72, step_min=5, offset_min=0)
    keys = [k for k, _ in posted]
    assert len(keys) == len(set(keys)) == 9


def test_before_first_slot_nothing_due():
    assert due_slot(utc(2026, 7, 15, 9, 59), SLOTS, TZ, 3, lambda k: False) is None


def test_missed_slot_outside_grace_is_skipped():
    # 12:00 CEST = 10:00 UTC; kl 13:30 UTC är grace (3h) slut och 17:00 inte nådd
    assert due_slot(utc(2026, 7, 15, 13, 30), SLOTS, TZ, 3, lambda k: False) is None


def test_late_cron_within_grace_still_posts():
    s = due_slot(utc(2026, 7, 15, 12, 40), SLOTS, TZ, 3, lambda k: False)
    assert s and s.key == "2026-07-15 12:00"


def test_no_catch_up_of_older_slot():
    # 20:30 lokal (18:30 UTC) med grace 4h: 17:00 är fortfarande "inom grace" men ska inte tas
    used = {"2026-07-15 20:30"}
    assert due_slot(utc(2026, 7, 15, 18, 45), SLOTS, TZ, 4, lambda k: k in used) is None


def test_slot_just_before_midnight_taken_after_midnight():
    s = due_slot(utc(2026, 1, 15, 23, 10), ["23:45"], TZ, 3, lambda k: False)  # 00:10 lokal 16 jan
    assert s and s.key == "2026-01-15 23:45"


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        due_slot(datetime(2026, 1, 1), SLOTS, TZ, 3, lambda k: False)


def test_dst_gap_slot_rejected():
    with pytest.raises(ValidationError):
        ScheduleCfg(slots=["02:30"])


def test_db_backed_idempotency(db):
    from clipper.models import VideoMetadata
    from conftest import make_clip
    for i in range(5):
        c = make_clip(f"c{i}")
        db.insert_candidate(c)
        db.mark_rendered(c.key, "f.mp4", "", VideoMetadata("t", "d"))
    t = utc(2026, 7, 15, 10, 7)
    posts = 0
    for _ in range(4):                       # retry/omstart samma timme
        s = due_slot(t, SLOTS, TZ, 3, lambda k: db.slot(k) is not None)
        if s:
            q = db.rendered_queue()
            if db.claim_slot(s.key, q[0]["key"]):
                posts += 1
    assert posts == 1
