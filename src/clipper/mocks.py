"""Mockade källor/verktyg för dry-run end-to-end (`clipper demo`) och tester.
Kräver inga API-nycklar och inget nätverk."""
from __future__ import annotations

import random
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from .models import ClipCandidate
from .sources.base import ClipSource
from .transcribe import Transcript, Word

FAKE_STREAMERS = {
    "twitch": ["alpha_streams", "bravo_tv", "charlie_rp", "delta_plays"],
    "kick": ["echo_kick", "foxtrot_live"],
}
FAKE_LINES = ["no way he just did that", "bro look at the police", "this is insane chat",
              "wait wait wait", "Lucia is crazy", "we are so back"]


class FakeSource(ClipSource):
    def __init__(self, platform: str, now_fn, seed: int = 1):
        self.platform = platform
        self.now_fn = now_fn
        self.rng = random.Random(f"{platform}{seed}")
        self.calls = 0

    def fetch_clips(self, started_at: datetime, ended_at: datetime, max_clips: int = 500) -> list[ClipCandidate]:
        self.calls += 1
        now = self.now_fn()
        out = []
        # nya klipp dyker upp varje timme: id beror på timme
        hour_id = int(now.timestamp() // 3600)
        for h in range(0, 24):
            for i, s in enumerate(FAKE_STREAMERS[self.platform]):
                cid = f"{self.platform[:2]}{hour_id - h}_{i}"
                created = now - timedelta(hours=h, minutes=self.rng.randint(0, 59))
                if not (started_at <= created <= ended_at):
                    continue
                out.append(ClipCandidate(
                    platform=self.platform, clip_id=cid, streamer=s, streamer_display=s.replace("_", " ").title(),
                    title=self.rng.choice(FAKE_LINES), views=self.rng.randint(150, 20000),
                    url=f"https://example.invalid/{self.platform}/{cid}", created_at=created,
                    duration_s=self.rng.choice([12, 20, 28, 35, 44, 58, 75]), language="en"))
        return out[:max_clips]


def fake_download(url: str, out_dir, name: str, seconds: int = 6) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def fake_transcribe(path, *args, **kwargs) -> Transcript:
    words = [Word(0.3 + i * 0.45, 0.3 + i * 0.45 + 0.4, w) for i, w in enumerate("no way he just did that bro".split())]
    return Transcript(" ".join(w.text for w in words), words, "en")


class FakeAnthropic:
    """Minimal ersättare för anthropic.Anthropic() som returnerar giltig JSON."""

    def __init__(self, fail: bool = False, suggestive: bool = False):
        self.fail = fail
        self.suggestive = suggestive
        self.last_kwargs: dict = {}
        self.calls = 0
        self.beta = self
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        if self.fail:
            raise RuntimeError("simulerat API-fel")
        import json
        from types import SimpleNamespace
        text = json.dumps({"title": "He did NOT expect the cops to do this 😳", "hook": "Wait for it…",
                           "description": "Chaos in Vice City.", "hashtags": ["#GTA6", "#GTAVI", "#Gaming", "#ViceCity"],
                           "suggestive": self.suggestive})
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])
