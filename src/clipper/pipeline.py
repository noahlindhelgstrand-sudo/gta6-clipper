"""Orkestrering av prepare (steg 1–4) och publish (steg 5–6)."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from . import metadata as meta_mod
from . import scheduler
from .config import Config
from .db import Database
from .downloader import download_clip
from .log import get_logger
from .models import ClipCandidate
from .notify import Notifier
from .publishers.base import AuthExpiredError, PublishError, Publisher, QuotaExceededError
from .render import render_short
from .selection import aggregate_streamers, allowed_streamers, select
from .sources.base import ClipSource, SourceError
from .storage.base import StateStore
from .transcribe import Transcript, transcribe

log = get_logger("pipeline")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Deps:
    """Allt som har sidoeffekter – byts ut i tester och demo."""
    sources: list[ClipSource]
    publishers: list[Publisher]
    store: StateStore
    notifier: Notifier
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    download: Callable = download_clip
    transcribe: Callable = transcribe
    render: Callable = render_short
    metadata: Callable = meta_mod.generate
    anthropic_client: object | None = None
    stats: dict = field(default_factory=dict)


class Pipeline:
    def __init__(self, cfg: Config, deps: Deps):
        self.cfg = cfg
        self.d = deps
        self.work = Path(cfg.work_dir)
        self.work.mkdir(parents=True, exist_ok=True)
        self.db_path = self.work / "state.db"
        self.db: Database | None = None

    # ---------- state sync ----------
    def __enter__(self) -> "Pipeline":
        existed = self.d.store.pull_db(self.db_path)
        if not existed:
            log.info("ingen tidigare databas – startar ny")
        self.db = Database(self.db_path, now_fn=self.d.now)
        return self

    def __exit__(self, *exc) -> None:
        if self.db:
            self.save()
            self.db.close()

    def save(self) -> None:
        self.db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.d.store.push_db(self.db_path)

    # ---------- PREPARE ----------
    def _fetch_all(self, start: datetime, end: datetime, max_clips: int) -> list[ClipCandidate]:
        clips: list[ClipCandidate] = []
        failures = []
        for src in self.d.sources:
            try:
                clips += src.fetch_clips(start, end, max_clips)
            except SourceError as e:
                failures.append(f"{src.platform}: {e}")
                log.warning("källa misslyckades", extra={"platform": src.platform, "error": str(e)})
        if failures and len(failures) == len(self.d.sources):
            self.d.notifier.error("Alla klippkällor misslyckades", "\n".join(failures))
        return clips

    def discover_streamers(self, force: bool = False) -> None:
        last = self.db.kv_get("last_discovery")
        now = self.d.now()
        if not force and last and now - datetime.fromisoformat(last) < timedelta(hours=20):
            return
        days = self.cfg.streamers.discovery_days
        clips = self._fetch_all(now - timedelta(days=days), now, max_clips=1000)
        stats = aggregate_streamers(clips)
        if stats:
            self.db.upsert_streamers(stats)
            self.db.kv_set("last_discovery", _iso(now))
            top = sorted(stats.items(), key=lambda kv: kv[1][1], reverse=True)[:10]
            log.info("streamers upptäckta", extra={"count": len(stats),
                                                   "top": [f"{k} ({v[1]} views)" for k, v in top]})

    def allowed(self) -> set[str]:
        top: list[str] = []
        for src in self.d.sources:
            top += [r["streamer_key"] for r in self.db.top_streamers(src.platform, self.cfg.streamers.top_n)]
        return allowed_streamers(self.cfg.streamers, top)

    def expire_old(self) -> None:
        cutoff = self.d.now() - timedelta(hours=self.cfg.queue.max_age_hours)
        for row in self.db.rendered_queue():
            if row["rendered_at"] and datetime.fromisoformat(row["rendered_at"]) < cutoff:
                self.db.set_status(row["key"], "expired")
                if row["video_file"]:
                    self.d.store.delete_video(row["video_file"])
                log.info("klipp för gammalt – kasseras", extra={"clip": row["key"]})

    MAX_ATTEMPTS = 3

    def known_keys(self) -> set[str]:
        """Klipp som inte ska väljas igen. Misslyckade klipp får nya försök (max MAX_ATTEMPTS)."""
        retry = {r["key"] for r in self.db.conn.execute("SELECT key FROM clips WHERE status='failed'")
                 if int(self.db.kv_get(f"fail:{r['key']}") or 0) < self.MAX_ATTEMPTS}
        return self.db.known_keys() - retry

    def process_clip(self, c: ClipCandidate) -> bool:
        clip_dir = self.work / "clips" / c.key.replace(":", "_")
        try:
            raw = self.d.download(c.url, clip_dir, "raw")
            tr: Transcript = self.d.transcribe(raw, self.cfg.transcribe.model, self.cfg.transcribe.device,
                                               self.cfg.transcribe.compute_type,
                                               self.cfg.render.max_duration_s - self.cfg.render.hook_intro_s)
            meta = self.d.metadata(c, tr.text, self.cfg.metadata, self.cfg.language,
                                   self.cfg.secrets.anthropic_api_key, client=self.d.anthropic_client)
            facecam = self.cfg.streamers.facecam.get(c.streamer_key)
            out_name = f"{c.key.replace(':', '_')}.mp4"
            out = self.d.render(raw, clip_dir / out_name, self.cfg.render, tr.words, meta.hook,
                                meta_mod.credit_overlay(c), facecam,
                                language=tr.language or self.cfg.language)
            self.d.store.put_video(out, out_name)
            self.db.mark_rendered(c.key, out_name, tr.text, meta)
            log.info("klipp klart i kön", extra={"clip": c.key, "title": meta.title, "score": c.score,
                                                 "bytes": Path(out).stat().st_size})
            return True
        except Exception as e:  # noqa: BLE001 – ett trasigt klipp ska inte stoppa resten
            log.exception("klipp misslyckades", extra={"clip": c.key})
            self.db.set_status(c.key, "failed", str(e)[:1000])
            fails = int(self.db.kv_get(f"fail:{c.key}") or 0) + 1
            self.db.kv_set(f"fail:{c.key}", str(fails))
            return False
        finally:
            shutil.rmtree(clip_dir, ignore_errors=True)

    # Höj när renderingen ändras på ett sätt som gör gamla köade videor inaktuella
    RENDER_VERSION = "2-censor"

    def rerender_outdated(self) -> None:
        if self.db.kv_get("render_version") == self.RENDER_VERSION:
            return
        for row in self.db.rendered_queue():
            if row["video_file"]:
                self.d.store.delete_video(row["video_file"])
            # 'failed' utan räknare => väljs om och renderas på nytt
            self.db.set_status(row["key"], "failed", "renderas om (ny renderingsversion)")
            log.info("klipp renderas om", extra={"clip": row["key"]})
        self.db.kv_set("render_version", self.RENDER_VERSION)

    def prepare(self) -> dict:
        self.rerender_outdated()
        self.expire_old()
        self.discover_streamers()
        buffer = self.db.count_status("rendered")
        result = {"buffer_before": buffer, "rendered": 0, "failed": 0}
        need = self.cfg.queue.buffer_target - buffer
        if need <= 0:
            log.info("bufferten är full – inget att göra", extra={"buffer": buffer})
            result["buffer_after"] = buffer
            return result

        now = self.d.now()
        clips = self._fetch_all(now - timedelta(hours=self.cfg.selection.lookback_hours), now, max_clips=500)
        langs = {"twitch": self.cfg.sources.twitch.clip_languages}
        allowed = self.allowed()
        chosen = select(clips, self.cfg.selection, self.cfg.streamers, allowed, self.known_keys(), langs, now)
        chosen = chosen[:need]
        log.info("urval klart", extra={"fetched": len(clips), "allowed_streamers": len(allowed),
                                       "chosen": [f"{c.key} score={c.score}" for c in chosen]})
        for c in chosen:
            self.db.insert_candidate(c)
            if self.process_clip(c):
                result["rendered"] += 1
            else:
                result["failed"] += 1
            self.save()                     # spara efter varje klipp – en krasch tappar max ett

        buffer = self.db.count_status("rendered")
        result["buffer_after"] = buffer
        if result["failed"]:
            self.d.notifier.warning(f"{result['failed']} klipp misslyckades i prepare",
                                    "Se Actions-loggen för detaljer.")
        if buffer < self.cfg.queue.buffer_min:
            self.d.notifier.warning("Lågt buffertläge",
                                    f"{buffer} renderade klipp i kön (min {self.cfg.queue.buffer_min}).")
        return result

    # ---------- PUBLISH ----------
    def _slot_is_used(self, key: str) -> bool:
        return self.db.slot(key) is not None

    def _slot_still_open(self, slot_key: str) -> bool:
        day, hm = slot_key.split(" ")
        s = next(x for x in scheduler.slots_for_day(datetime.fromisoformat(day).date(), [hm], self.cfg.timezone))
        return self.d.now() < s.utc + timedelta(hours=self.cfg.schedule.grace_hours)

    def _upload(self, clip_key: str, slot_key: str | None) -> list[str]:
        row = self.db.get(clip_key)
        meta = self.db.meta_of(row)
        local = self.work / "upload" / row["video_file"]
        if not local.exists():
            self.d.store.get_video(row["video_file"], local)
        urls = []
        for pub in self.d.publishers:
            if self.db.is_posted(clip_key, pub.name):
                continue
            res = pub.upload(local, meta, clip_key)
            self.db.record_post(clip_key, pub.name, res.external_id, res.url, res.privacy, slot_key)
            self.save()
            urls.append(res.url)
            if res.locked_private:
                self.d.notifier.warning("Video låst som privat",
                                        f"{res.url} – API-projektet är inte auditerat än (se README §3).")
        self.d.store.delete_video(row["video_file"])
        local.unlink(missing_ok=True)
        return urls

    def recover(self) -> None:
        """Klipp som fastnat i 'uploading' (förra körningen kraschade/timeout)."""
        for row in self.db.stuck_uploading():
            key = row["key"]
            slot_key = self.db.slot_for_clip(key)
            for pub in self.d.publishers:
                if self.db.is_posted(key, pub.name):
                    continue
                existing = pub.find_existing(key)
                if existing:
                    log.info("återhämtning: klippet var redan uppladdat", extra={"clip": key, "id": existing.external_id})
                    self.db.record_post(key, pub.name, existing.external_id, existing.url, existing.privacy, slot_key)
            if all(self.db.is_posted(key, p.name) for p in self.d.publishers):
                self.d.store.delete_video(row["video_file"])
                self.save()
                continue
            if slot_key and self._slot_still_open(slot_key):
                log.info("återhämtning: försöker ladda upp igen i samma slot", extra={"clip": key, "slot": slot_key})
                try:
                    self._upload(key, slot_key)
                except PublishError as e:
                    if isinstance(e, (AuthExpiredError, QuotaExceededError)):
                        raise
                    self.d.notifier.error("Uppladdning misslyckades igen", f"{key}: {e}")
            else:
                log.warning("återhämtning: sloten har passerat – klippet tillbaka i kön", extra={"clip": key})
                self.db.set_status(key, "rendered")
                if slot_key:
                    self.db.finish_slot(slot_key, "failed")
            self.save()

    def publish(self) -> dict:
        now = self.d.now()
        result: dict = {"posted": None}
        try:
            self.recover()
        except (AuthExpiredError, QuotaExceededError) as e:
            self._fatal(e)
            return result

        sc = self.cfg.schedule
        first_seen = self.db.kv_get("first_publish_run")
        if not first_seen:
            first_seen = _iso(now)
            self.db.kv_set("first_publish_run", first_seen)
        # Slots som passerade mer än 1 h innan systemet startade räknas inte (normal cron-fördröjning tolereras)
        started = datetime.fromisoformat(first_seen) - timedelta(hours=1)
        for m in scheduler.missed_slots(now, sc.slots, self.cfg.timezone, sc.grace_hours, self._slot_is_used):
            if m.utc < started:
                self.db.skip_slot(m.key, "före första körningen")
                continue
            self.db.skip_slot(m.key, "missad (ingen körning/tom kö inom grace)")
            self.d.notifier.warning("Slot missad", f"{m.key} postades inte inom {sc.grace_hours} h.")
        slot = scheduler.due_slot(now, sc.slots, self.cfg.timezone, sc.grace_hours, self._slot_is_used)
        if slot and slot.utc < started:
            self.db.skip_slot(slot.key, "före första körningen")
            slot = None
        if not slot:
            log.info("ingen slot aktuell", extra={"now_local": now.astimezone(ZoneInfo(self.cfg.timezone)).isoformat()})
            self.maybe_daily_summary()
            return result

        queue = self.db.rendered_queue()
        if not queue:
            flag = f"empty_notified:{slot.key}"
            if not self.db.kv_get(flag):
                self.d.notifier.error("Tom kö", f"Slot {slot.key} är aktuell men inga renderade klipp finns.")
                self.db.kv_set(flag, "1")
            return result
        clip = queue[0]
        if not self.db.claim_slot(slot.key, clip["key"]):
            log.warning("kunde inte reservera slot (redan tagen)", extra={"slot": slot.key})
            return result
        self.save()                          # reservationen måste vara sparad INNAN uppladdning
        log.info("postar", extra={"slot": slot.key, "clip": clip["key"], "score": clip["score"]})
        try:
            urls = self._upload(clip["key"], slot.key)
        except (AuthExpiredError, QuotaExceededError) as e:
            self._fatal(e)
            return result
        except PublishError as e:
            self.d.notifier.error("Uppladdning misslyckades", f"{clip['key']}: {e}\nFörsöker igen nästa körning.")
            return result
        result["posted"] = {"slot": slot.key, "clip": clip["key"], "urls": urls}
        remaining = self.db.count_status("rendered")
        if remaining < self.cfg.queue.buffer_min:
            self.d.notifier.warning("Lågt buffertläge", f"{remaining} klipp kvar i kön.")
        self.maybe_daily_summary()
        return result

    def _fatal(self, e: Exception) -> None:
        if isinstance(e, AuthExpiredError):
            self.d.notifier.error("YouTube-token utgången", str(e))
        else:
            self.d.notifier.error("YouTube-kvot överskriden", str(e))

    # ---------- SUMMARY ----------
    def maybe_daily_summary(self, force: bool = False) -> str | None:
        if not self.cfg.notify.daily_summary and not force:
            return None
        tz = ZoneInfo(self.cfg.timezone)
        now_local = self.d.now().astimezone(tz)
        last = scheduler.slots_for_day(now_local.date(), self.cfg.schedule.slots, self.cfg.timezone)[-1]
        day = now_local.date()
        if not force and (self.d.now() < last.utc + timedelta(hours=1) or self.db.kv_get(f"summary:{day}")):
            return None
        start, end = scheduler.local_day_bounds_utc(day, self.cfg.timezone)
        posts = self.db.posts_between(_iso(start), _iso(end))
        lines = [f"• {p['url']} ({p['privacy']}) – {p['streamer_display']}: {p['clip_title']}" for p in posts]
        text = (f"Postade idag: {len(posts)}/{len(self.cfg.schedule.slots)}\n" + "\n".join(lines) +
                f"\n\nI kön: {self.db.count_status('rendered')} klipp.")
        self.d.notifier.info(f"📊 Daglig sammanfattning {day}", text)
        self.db.kv_set(f"summary:{day}", "1")
        return text
