"""Kommandorad: clipper prepare | publish | summary | status | discover | demo"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config, load_config
from .log import get_logger, setup_logging
from .notify import Notifier
from .pipeline import Deps, Pipeline
from .publishers import build_publishers
from .sources.base import SourceError
from .storage import build_store
from .storage.local import LocalStore

log = get_logger("cli")


def build_sources(cfg: Config):
    sources = []
    if cfg.sources.twitch.enabled:
        from .sources.twitch import TwitchSource
        try:
            sources.append(TwitchSource(cfg.secrets.twitch_client_id, cfg.secrets.twitch_client_secret,
                                        cfg.sources.twitch.game_name))
        except SourceError as e:
            log.warning("Twitch inaktiverad", extra={"error": str(e)})
    if cfg.sources.kick.enabled:
        from .sources.kick import KickSource
        sources.append(KickSource(cfg.sources.kick.category_slug))
    if not sources:
        raise SystemExit("Inga klippkällor konfigurerade/tillgängliga.")
    return sources


def build_deps(cfg: Config, dry_run: bool, need_publishers: bool) -> Deps:
    notifier = Notifier(cfg.secrets.discord_webhook_url, ntfy_topic=cfg.secrets.ntfy_topic)
    if dry_run:
        # dry-run rör aldrig riktiga tillståndet
        store = LocalStore(Path(cfg.work_dir) / "dryrun-data")
    else:
        store = build_store(cfg)
    publishers = build_publishers(cfg, dry_run) if need_publishers else []
    return Deps(sources=build_sources(cfg) if not need_publishers else [], publishers=publishers,
                store=store, notifier=notifier)


def run_demo(cfg: Config, days: int) -> int:
    """Dry-run end-to-end med mockad Twitch/Kick, mockad Claude och mockad YouTube."""
    from . import mocks
    from .publishers.dryrun import DryRunPublisher

    cfg.render.preset = "ultrafast"
    cfg.storage.backend = "local"
    start = datetime.now(timezone.utc).replace(minute=7, second=0, microsecond=0)
    clock = {"t": start}
    now = lambda: clock["t"]  # noqa: E731
    store = LocalStore(Path(cfg.work_dir) / "demo-data")
    pub = DryRunPublisher("youtube", cfg.publish.privacy_status)
    deps = Deps(sources=[mocks.FakeSource("twitch", now), mocks.FakeSource("kick", now)], publishers=[pub],
                store=store, notifier=Notifier(None, dry_run=True), now=now, download=mocks.fake_download,
                transcribe=mocks.fake_transcribe, anthropic_client=mocks.FakeAnthropic())
    posted = []
    for _ in range(days * 24):
        with Pipeline(cfg, deps) as p:
            p.prepare()
        with Pipeline(cfg, deps) as p:
            r = p.publish()
            if r["posted"]:
                posted.append(r["posted"])
        clock["t"] += timedelta(hours=1)
    print(json.dumps({"simulated_days": days, "posted": len(posted), "uploads": posted,
                      "notifications": [e["embeds"][0]["title"] for e in deps.notifier.sent]},
                     indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="clipper")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dry-run", action="store_true", help="rendera men ladda inte upp; rör inte riktiga tillståndet")
    ap.add_argument("--log-level", default="INFO")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare", help="hämta, rendera och fyll bufferten")
    pp = sub.add_parser("publish", help="posta om en slot är aktuell")
    pp.add_argument("--now", action="store_true", help="ladda upp bästa klippet direkt (test), utanför schemat")
    sub.add_parser("summary", help="skicka daglig sammanfattning nu")
    sub.add_parser("status", help="visa kö och senaste postningar")
    sub.add_parser("discover", help="uppdatera listan över toppstreamers nu")
    sub.add_parser("resume", help="återuppta uppladdningar efter automatisk paus")
    ub = sub.add_parser("unblock", help="ta bort en automatiskt blockerad streamer")
    ub.add_argument("streamer", help="t.ex. twitch:xqc")
    d = sub.add_parser("demo", help="dry-run end-to-end med mockade API:er")
    d.add_argument("--days", type=int, default=2)
    args = ap.parse_args(argv)

    setup_logging(args.log_level)
    cfg = load_config(args.config)

    if args.cmd == "demo":
        return run_demo(cfg, args.days)

    need_pub = args.cmd == "publish"
    if args.cmd in ("resume", "unblock"):
        args.dry_run = False
    try:
        deps = build_deps(cfg, args.dry_run, need_publishers=need_pub)
    except Exception as e:  # noqa: BLE001
        Notifier(cfg.secrets.discord_webhook_url, ntfy_topic=cfg.secrets.ntfy_topic).error(f"{args.cmd}: uppstart misslyckades", str(e))
        log.exception("uppstart misslyckades")
        return 1

    try:
        with Pipeline(cfg, deps) as p:
            if args.cmd == "prepare":
                out = p.prepare()
            elif args.cmd == "publish":
                out = p.publish(now_upload=getattr(args, "now", False))
            elif args.cmd == "summary":
                out = {"summary": p.maybe_daily_summary(force=True)}
            elif args.cmd == "resume":
                out = {"resumed": p.resume()}
            elif args.cmd == "unblock":
                out = {"unblocked": p.unblock_streamer(args.streamer.lower())}
            elif args.cmd == "discover":
                p.discover_streamers(force=True)
                out = {"allowed": sorted(p.allowed())}
            else:
                out = {
                    "paused": p.paused(),
                    "auto_blocked": sorted(p.auto_blocked()),
                    "queue": [dict(key=r["key"], score=r["score"], title=json.loads(r["meta_json"])["title"])
                              for r in p.db.rendered_queue()],
                    "posts": [dict(r) for r in p.db.posts_between("0", "9")][-10:],
                    "top_streamers": [dict(r) for r in p.db.top_streamers("twitch", 10)]
                                     + [dict(r) for r in p.db.top_streamers("kick", 10)],
                }
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        return 0
    except Exception as e:  # noqa: BLE001
        log.exception(f"{args.cmd} kraschade")
        deps.notifier.error(f"{args.cmd} kraschade", f"{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
