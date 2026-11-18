"""Slot-schemaläggning i lokal tidszon.

GitHub Actions-cron körs i UTC och förskjuts en timme vid sommar-/vintertid.
Vi kör därför publish-jobbet varje timme och räknar ut här, i Europe/Stockholm,
om en slot är "aktuell": den har passerats, ligger inom grace-fönstret och
finns inte redan i databasen.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Slot:
    key: str                 # 'YYYY-MM-DD HH:MM' (lokal tid) – unik nyckel i DB
    local: datetime          # tz-aware lokal tid
    utc: datetime

    def __str__(self) -> str:
        return self.key


def slots_for_day(day: date, slots: list[str], tz: str) -> list[Slot]:
    z = ZoneInfo(tz)
    out = []
    for s in sorted(slots):
        hh, mm = (int(x) for x in s.split(":"))
        local = datetime.combine(day, time(hh, mm), tzinfo=z)
        out.append(Slot(f"{day.isoformat()} {s}", local, local.astimezone(timezone.utc)))
    return out


def due_slot(
    now_utc: datetime,
    slots: list[str],
    tz: str,
    grace_hours: float,
    is_used,
) -> Slot | None:
    """Senaste slot som har passerat, inom grace, och inte är använd.

    `is_used(slot_key) -> bool` frågar databasen. Vi tittar på idag och igår
    (lokal tid) så att en slot strax före midnatt fortfarande kan tas efter midnatt.
    Äldre slots än grace hoppas över – vi "kommer aldrig ikapp", vilket garanterar
    högst len(slots) postningar per lokalt dygn.
    """
    if now_utc.tzinfo is None:
        raise ValueError("now_utc måste vara tz-aware")
    local_today = now_utc.astimezone(ZoneInfo(tz)).date()
    candidates = slots_for_day(local_today - timedelta(days=1), slots, tz) + slots_for_day(local_today, slots, tz)
    grace = timedelta(hours=grace_hours)
    passed = [s for s in candidates if s.utc <= now_utc < s.utc + grace]
    # Den senaste passerade sloten gäller; tidigare inom grace ignoreras om en senare finns
    for s in sorted(passed, key=lambda s: s.utc, reverse=True):
        if not is_used(s.key):
            return s
        return None   # senaste sloten redan använd => inget att göra (ingen ikappkörning)
    return None


def missed_slots(now_utc: datetime, slots: list[str], tz: str, grace_hours: float, is_used) -> list[Slot]:
    """Slots idag/igår vars grace har gått ut utan att de använts (för loggning/skip-markering)."""
    local_today = now_utc.astimezone(ZoneInfo(tz)).date()
    candidates = slots_for_day(local_today - timedelta(days=1), slots, tz) + slots_for_day(local_today, slots, tz)
    grace = timedelta(hours=grace_hours)
    return [s for s in candidates if s.utc + grace <= now_utc and not is_used(s.key)]


def local_day_bounds_utc(day: date, tz: str) -> tuple[datetime, datetime]:
    z = ZoneInfo(tz)
    start = datetime.combine(day, time(0, 0), tzinfo=z)
    end = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=z)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)
