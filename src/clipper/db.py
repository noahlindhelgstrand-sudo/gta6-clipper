"""SQLite-lager. Allt tillstånd (kö, slots, postningar) bor här.

Statusflöde för clips:
    discovered -> rendered -> uploading -> posted
                 \\-> failed / skipped / expired
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .models import ClipCandidate, VideoMetadata

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    key            TEXT PRIMARY KEY,          -- platform:clip_id
    platform       TEXT NOT NULL,
    clip_id        TEXT NOT NULL,
    streamer       TEXT NOT NULL,
    streamer_display TEXT,
    title          TEXT,
    views          INTEGER,
    url            TEXT,
    created_at     TEXT,
    duration_s     REAL,
    score          REAL,
    status         TEXT NOT NULL DEFAULT 'discovered',
    error          TEXT,
    video_file     TEXT,                      -- filnamn i lagringen
    transcript     TEXT,
    meta_json      TEXT,
    discovered_at  TEXT NOT NULL,
    rendered_at    TEXT,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status);

CREATE TABLE IF NOT EXISTS slots (
    slot_key   TEXT PRIMARY KEY,               -- 'YYYY-MM-DD HH:MM' lokal tid
    clip_key   TEXT,
    status     TEXT NOT NULL,                  -- claimed | done | skipped | failed
    claimed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    clip_key     TEXT NOT NULL,
    platform     TEXT NOT NULL,
    external_id  TEXT,
    url          TEXT,
    privacy      TEXT,
    slot_key     TEXT,
    posted_at    TEXT NOT NULL,
    PRIMARY KEY (clip_key, platform)           -- samma klipp aldrig två gånger per plattform
);

CREATE TABLE IF NOT EXISTS streamers (
    streamer_key TEXT PRIMARY KEY,             -- platform:login
    display      TEXT,
    total_views  INTEGER NOT NULL,
    clip_count   INTEGER NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""

ACTIVE_STATUSES = ("discovered", "rendered", "uploading", "posted")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Database:
    def __init__(self, path: str | Path, now_fn=_utcnow):
        self._now_fn = now_fn
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def _now(self) -> str:
        return self._now_fn().astimezone(timezone.utc).isoformat(timespec="seconds")

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    # ---------- clips ----------
    def known_keys(self) -> set[str]:
        return {r["key"] for r in self.conn.execute("SELECT key FROM clips")}

    def insert_candidate(self, c: ClipCandidate) -> bool:
        """Returnerar False om klippet redan fanns (deduplicering)."""
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO clips
            (key, platform, clip_id, streamer, streamer_display, title, views, url,
             created_at, duration_s, score, status, discovered_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,'discovered',?,?)""",
            (c.key, c.platform, c.clip_id, c.streamer, c.streamer_display, c.title, c.views,
             c.url, c.created_at.isoformat(), c.duration_s, c.score, self._now(), self._now()),
        )
        return cur.rowcount == 1

    def get(self, key: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM clips WHERE key=?", (key,)).fetchone()

    def set_status(self, key: str, status: str, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE clips SET status=?, error=?, updated_at=? WHERE key=?",
            (status, error, self._now(), key),
        )

    def mark_rendered(self, key: str, video_file: str, transcript: str, meta: VideoMetadata) -> None:
        self.conn.execute(
            """UPDATE clips SET status='rendered', video_file=?, transcript=?, meta_json=?,
               rendered_at=?, updated_at=?, error=NULL WHERE key=?""",
            (video_file, transcript, json.dumps(meta.__dict__, ensure_ascii=False), self._now(), self._now(), key),
        )

    def rendered_queue(self) -> list[sqlite3.Row]:
        """Renderade klipp, högst rankade först."""
        return list(self.conn.execute(
            "SELECT * FROM clips WHERE status='rendered' ORDER BY score DESC, rendered_at ASC"
        ))

    def count_status(self, status: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM clips WHERE status=?", (status,)).fetchone()[0]

    def stuck_uploading(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM clips WHERE status='uploading'"))

    def streamer_posts_since(self, streamer_key: str, since_iso: str) -> int:
        platform, streamer = streamer_key.split(":", 1)
        return self.conn.execute(
            """SELECT COUNT(*) FROM posts p JOIN clips c ON c.key=p.clip_key
               WHERE c.platform=? AND c.streamer=? AND p.posted_at>=?""",
            (platform, streamer, since_iso),
        ).fetchone()[0]

    @staticmethod
    def meta_of(row: sqlite3.Row) -> VideoMetadata:
        return VideoMetadata(**json.loads(row["meta_json"]))

    # ---------- slots ----------
    def slot(self, slot_key: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM slots WHERE slot_key=?", (slot_key,)).fetchone()

    def claim_slot(self, slot_key: str, clip_key: str) -> bool:
        """Atomiskt: reserverar sloten OCH sätter klippet i 'uploading'.
        Returnerar False om sloten redan är tagen eller klippet inte längre är 'rendered'."""
        with self.tx() as c:
            if c.execute("SELECT 1 FROM slots WHERE slot_key=?", (slot_key,)).fetchone():
                return False
            row = c.execute("SELECT status FROM clips WHERE key=?", (clip_key,)).fetchone()
            if not row or row["status"] != "rendered":
                return False
            if c.execute("SELECT 1 FROM posts WHERE clip_key=?", (clip_key,)).fetchone():
                return False
            c.execute(
                "INSERT INTO slots (slot_key, clip_key, status, claimed_at, updated_at) VALUES (?,?,?,?,?)",
                (slot_key, clip_key, "claimed", self._now(), self._now()),
            )
            c.execute("UPDATE clips SET status='uploading', updated_at=? WHERE key=?", (self._now(), clip_key))
        return True

    def skip_slot(self, slot_key: str, reason: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO slots (slot_key, clip_key, status, claimed_at, updated_at) VALUES (?,?,?,?,?)",
            (slot_key, None, "skipped", self._now(), self._now()),
        )
        self.kv_set(f"slot_skip_reason:{slot_key}", reason)

    def finish_slot(self, slot_key: str, status: str) -> None:
        self.conn.execute("UPDATE slots SET status=?, updated_at=? WHERE slot_key=?", (status, self._now(), slot_key))

    def slot_for_clip(self, clip_key: str) -> str | None:
        r = self.conn.execute(
            "SELECT slot_key FROM slots WHERE clip_key=? ORDER BY claimed_at DESC", (clip_key,)
        ).fetchone()
        return r["slot_key"] if r else None

    # ---------- posts ----------
    def record_post(self, clip_key: str, platform: str, external_id: str, url: str,
                    privacy: str, slot_key: str | None) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT OR IGNORE INTO posts (clip_key, platform, external_id, url, privacy, slot_key, posted_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (clip_key, platform, external_id, url, privacy, slot_key, self._now()),
            )
            c.execute("UPDATE clips SET status='posted', updated_at=?, error=NULL WHERE key=?", (self._now(), clip_key))
            if slot_key:
                c.execute("UPDATE slots SET status='done', updated_at=? WHERE slot_key=?", (self._now(), slot_key))

    def is_posted(self, clip_key: str, platform: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM posts WHERE clip_key=? AND platform=?", (clip_key, platform)
        ).fetchone() is not None

    def posts_between(self, start_iso: str, end_iso: str) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            """SELECT p.*, c.streamer_display, c.title AS clip_title, c.url AS clip_url
               FROM posts p JOIN clips c ON c.key=p.clip_key
               WHERE p.posted_at>=? AND p.posted_at<? ORDER BY p.posted_at""",
            (start_iso, end_iso),
        ))

    # ---------- streamers (auto-discovery) ----------
    def upsert_streamers(self, stats: dict[str, tuple[str, int, int]]) -> None:
        with self.tx() as c:
            for key, (display, views, count) in stats.items():
                c.execute(
                    """INSERT INTO streamers (streamer_key, display, total_views, clip_count, updated_at)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(streamer_key) DO UPDATE SET display=excluded.display,
                         total_views=excluded.total_views, clip_count=excluded.clip_count,
                         updated_at=excluded.updated_at""",
                    (key, display, views, count, self._now()),
                )

    def top_streamers(self, platform: str, n: int) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM streamers WHERE streamer_key LIKE ? ORDER BY total_views DESC LIMIT ?",
            (f"{platform}:%", n),
        ))

    # ---------- kv ----------
    def kv_get(self, k: str) -> str | None:
        r = self.conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r["v"] if r else None

    def kv_set(self, k: str, v: str) -> None:
        self.conn.execute("INSERT INTO kv (k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
