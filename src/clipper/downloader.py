"""Nedladdning av klipp med yt-dlp (stöder Twitch- och Kick-klipp)."""
from __future__ import annotations

from pathlib import Path

from .log import get_logger

log = get_logger("download")


class DownloadError(RuntimeError):
    pass


def download_clip(url: str, out_dir: str | Path, name: str) -> Path:
    import yt_dlp

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "outtmpl": str(out_dir / f"{name}.%(ext)s"),
        "format": "best[ext=mp4][height<=1080]/best[height<=1080]/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 3,
        "merge_output_format": "mp4",
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info))
    except Exception as e:  # yt-dlp kastar många olika typer
        raise DownloadError(f"yt-dlp misslyckades för {url}: {e}") from e
    if not path.exists():
        mp4 = path.with_suffix(".mp4")
        if mp4.exists():
            path = mp4
        else:
            raise DownloadError(f"nedladdad fil saknas: {path}")
    log.info("nedladdat", extra={"url": url, "file": str(path), "bytes": path.stat().st_size})
    return path
