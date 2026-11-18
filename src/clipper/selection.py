"""Urval och rankning – rena funktioner utan I/O (lätta att testa)."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable

from .config import SelectionCfg, StreamersCfg
from .models import ClipCandidate


def velocity(clip: ClipCandidate, now: datetime) -> float:
    """Views per timme sedan klippet skapades. Minst 0,5 h i nämnaren så att
    helt färska klipp inte får orimligt höga värden."""
    hours = max((now - clip.created_at).total_seconds() / 3600.0, 0.5)
    return clip.views / hours


def aggregate_streamers(clips: Iterable[ClipCandidate]) -> dict[str, tuple[str, int, int]]:
    """streamer_key -> (display, summa views, antal klipp). Används för auto-discovery."""
    acc: dict[str, list] = defaultdict(lambda: ["", 0, 0])
    for c in clips:
        a = acc[c.streamer_key]
        a[0] = c.streamer_display or c.streamer
        a[1] += c.views
        a[2] += 1
    return {k: (v[0], v[1], v[2]) for k, v in acc.items()}


def allowed_streamers(cfg: StreamersCfg, discovered_top: Iterable[str]) -> set[str]:
    """Mängden streamer_keys vi får posta från.

    - policy=allowlist: endast cfg.allowlist
    - policy=auto: cfg.allowlist ∪ automatiskt upptäckta toppstreamers
    Blocklist dras alltid bort.
    """
    allowed = set(cfg.allowlist)
    if cfg.policy == "auto":
        allowed |= {k.lower() for k in discovered_top}
    return allowed - set(cfg.blocklist)


def filter_clips(
    clips: Iterable[ClipCandidate],
    sel: SelectionCfg,
    streamers: StreamersCfg,
    allowed: set[str],
    known_keys: set[str],
    languages: dict[str, list[str]] | None = None,
) -> list[ClipCandidate]:
    languages = languages or {}
    blocked = set(streamers.blocklist)
    out = []
    for c in clips:
        if c.key in known_keys:
            continue                                  # redan sett/postat
        if c.streamer_key in blocked:
            continue
        if c.streamer_key not in allowed:
            continue
        if c.views < sel.min_views:
            continue
        if not (sel.min_duration_s <= c.duration_s <= sel.max_duration_s):
            continue
        langs = languages.get(c.platform) or []
        if langs and c.language and c.language.lower() not in [l.lower() for l in langs]:
            continue
        out.append(c)
    return out


def rank(clips: Iterable[ClipCandidate], now: datetime | None = None) -> list[ClipCandidate]:
    now = now or datetime.now(timezone.utc)
    ranked = []
    for c in clips:
        c.score = round(velocity(c, now), 3)
        ranked.append(c)
    ranked.sort(key=lambda c: (c.score, c.views), reverse=True)
    return ranked


def dedupe(clips: Iterable[ClipCandidate]) -> list[ClipCandidate]:
    """Ta bort dubbletter inom samma körning (samma key, behåll första)."""
    seen: set[str] = set()
    out = []
    for c in clips:
        if c.key not in seen:
            seen.add(c.key)
            out.append(c)
    return out


def pick(
    ranked: list[ClipCandidate], limit: int, max_per_streamer: int
) -> list[ClipCandidate]:
    """Välj de bästa men högst max_per_streamer per streamer, för variation."""
    per: dict[str, int] = defaultdict(int)
    out = []
    for c in ranked:
        if per[c.streamer_key] >= max_per_streamer:
            continue
        per[c.streamer_key] += 1
        out.append(c)
        if len(out) >= limit:
            break
    return out


def select(
    clips: Iterable[ClipCandidate],
    sel: SelectionCfg,
    streamers: StreamersCfg,
    allowed: set[str],
    known_keys: set[str],
    languages: dict[str, list[str]] | None = None,
    now: datetime | None = None,
) -> list[ClipCandidate]:
    filtered = filter_clips(dedupe(clips), sel, streamers, allowed, known_keys, languages)
    return pick(rank(filtered, now), sel.max_candidates_per_run, sel.max_per_streamer_per_day)
