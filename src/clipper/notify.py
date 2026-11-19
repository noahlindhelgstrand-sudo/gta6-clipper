"""Notiser via ntfy (push till mobilen, ingen inloggning) och/eller Discord-webhook."""
from __future__ import annotations

import requests

from .log import get_logger

log = get_logger("notify")

COLORS = {"error": 0xE74C3C, "warning": 0xF1C40F, "info": 0x3498DB, "success": 0x2ECC71}


class Notifier:
    NTFY_PRIORITY = {"error": 5, "warning": 4, "info": 3, "success": 4}

    def __init__(self, webhook_url: str | None, dry_run: bool = False, session=None,
                 ntfy_topic: str | None = None, ntfy_server: str = "https://ntfy.sh"):
        self.url = webhook_url
        self.ntfy_topic = ntfy_topic
        self.ntfy_server = ntfy_server.rstrip("/")
        self.dry_run = dry_run
        self.http = session or requests.Session()
        self.sent: list[dict] = []      # för tester/dry-run

    def send(self, level: str, title: str, text: str = "", click: str | None = None,
             priority: int | None = None) -> None:
        payload = {"username": "gta6-clipper", "embeds": [{
            "title": title[:250], "description": text[:3900], "color": COLORS.get(level, 0x95A5A6)}]}
        if click:
            payload["embeds"][0]["url"] = click
        self.sent.append(payload)
        log.log(40 if level == "error" else 30 if level == "warning" else 20,
                f"notify: {title}", extra={"notify_level": level, "text": text[:500]})
        if self.dry_run:
            return
        if self.ntfy_topic:
            self._ntfy(level, title, text, click, priority)
        if not self.url:
            return
        try:
            r = self.http.post(self.url, json=payload, timeout=10)
            if r.status_code >= 300:
                log.warning("discord-webhook misslyckades", extra={"status": r.status_code})
        except requests.RequestException as e:
            log.warning("discord-webhook nätverksfel", extra={"error": str(e)})

    def _ntfy(self, level: str, title: str, text: str, click: str | None, priority: int | None) -> None:
        body = {"topic": self.ntfy_topic, "title": title[:250], "message": text[:3900] or title,
                "priority": priority or self.NTFY_PRIORITY.get(level, 3)}
        if click:
            body["click"] = click
            body["actions"] = [{"action": "view", "label": "Öppna", "url": click}]
        try:
            r = self.http.post(self.ntfy_server, json=body, timeout=10)
            if r.status_code >= 300:
                log.warning("ntfy misslyckades", extra={"status": r.status_code})
        except requests.RequestException as e:
            log.warning("ntfy nätverksfel", extra={"error": str(e)})

    def error(self, title: str, text: str = "") -> None:
        self.send("error", f"❌ {title}", text)

    def warning(self, title: str, text: str = "") -> None:
        self.send("warning", f"⚠️ {title}", text)

    def info(self, title: str, text: str = "") -> None:
        self.send("info", title, text)
