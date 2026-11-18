"""Discord-webhook för fel, låg buffert, utgången token och daglig sammanfattning."""
from __future__ import annotations

import requests

from .log import get_logger

log = get_logger("notify")

COLORS = {"error": 0xE74C3C, "warning": 0xF1C40F, "info": 0x3498DB, "success": 0x2ECC71}


class Notifier:
    def __init__(self, webhook_url: str | None, dry_run: bool = False, session=None):
        self.url = webhook_url
        self.dry_run = dry_run
        self.http = session or requests.Session()
        self.sent: list[dict] = []      # för tester/dry-run

    def send(self, level: str, title: str, text: str = "") -> None:
        payload = {"username": "gta6-clipper", "embeds": [{
            "title": title[:250], "description": text[:3900], "color": COLORS.get(level, 0x95A5A6)}]}
        self.sent.append(payload)
        log.log(40 if level == "error" else 30 if level == "warning" else 20,
                f"notify: {title}", extra={"notify_level": level, "text": text[:500]})
        if self.dry_run or not self.url:
            return
        try:
            r = self.http.post(self.url, json=payload, timeout=10)
            if r.status_code >= 300:
                log.warning("discord-webhook misslyckades", extra={"status": r.status_code})
        except requests.RequestException as e:
            log.warning("discord-webhook nätverksfel", extra={"error": str(e)})

    def error(self, title: str, text: str = "") -> None:
        self.send("error", f"❌ {title}", text)

    def warning(self, title: str, text: str = "") -> None:
        self.send("warning", f"⚠️ {title}", text)

    def info(self, title: str, text: str = "") -> None:
        self.send("info", title, text)
