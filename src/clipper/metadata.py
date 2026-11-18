"""Titel, beskrivning, hashtags och hook via Claude API – med fallback."""
from __future__ import annotations

import json
import re

from .config import MetadataCfg
from .log import get_logger
from .models import ClipCandidate, VideoMetadata

log = get_logger("metadata")

PLATFORM_HOST = {"twitch": "twitch.tv", "kick": "kick.com"}

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "hook": {"type": "string"},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "hook", "description", "hashtags"],
    "additionalProperties": False,
}

SYSTEM = """You write metadata for YouTube Shorts made from GTA 6 (Grand Theft Auto VI) livestream clips.
The Short is a re-edit of a streamer's clip, credited to them.

Output fields:
- title: a short, punchy hook title. At most {max_title} characters INCLUDING the trailing " #Shorts". No clickbait lies; it must match what actually happens in the transcript/clip title. No ALL-CAPS sentences, at most one emoji.
- hook: 2–6 words shown on screen during the first seconds, creating curiosity (e.g. "He did NOT expect this").
- description: 1–2 sentences about the moment, written in {language}. Do NOT include links or credit – they are added automatically.
- hashtags: 3–5 relevant hashtags, each starting with #, no spaces. Always include #GTA6.

Write everything in {language}. Never invent events that are not supported by the transcript or clip title."""


def credit_block(c: ClipCandidate) -> str:
    host = PLATFORM_HOST.get(c.platform, c.platform)
    return (f"🎥 Streamer: {c.streamer_display} – https://{host}/{c.streamer}\n"
            f"🔗 Original clip: {c.url}\n"
            f"All credit for the original stream goes to {c.streamer_display}.")


def credit_overlay(c: ClipCandidate) -> str:
    return f"{PLATFORM_HOST.get(c.platform, c.platform)}/{c.streamer}"


def _clean_title(title: str, max_chars: int) -> str:
    title = re.sub(r"\s+", " ", title.replace("#Shorts", "").replace("#shorts", "")).strip()
    suffix = " #Shorts"
    room = max_chars - len(suffix)
    if len(title) > room:
        title = title[: room - 1].rstrip() + "…"
    return f"{title}{suffix}"


def _clean_tags(tags: list[str]) -> list[str]:
    out = []
    for t in tags:
        t = "#" + re.sub(r"[^\w]", "", t.lstrip("#"))
        if len(t) > 1 and t.lower() not in [x.lower() for x in out]:
            out.append(t)
    if "#gta6" not in [x.lower() for x in out]:
        out.insert(0, "#GTA6")
    return out[:5] if len(out) >= 3 else (out + ["#Gaming", "#Shorts", "#GTAVI"])[:3]


def assemble(c: ClipCandidate, title: str, hook: str, desc: str, tags: list[str],
             cfg: MetadataCfg, source: str) -> VideoMetadata:
    tags = _clean_tags(tags)
    description = f"{desc.strip()}\n\n{credit_block(c)}\n\n{' '.join(tags)}\n#Shorts"
    return VideoMetadata(
        title=_clean_title(title, cfg.max_title_chars),
        description=description[:4900],
        hashtags=tags,
        hook=hook.strip()[:60],
        generated_by=source,
    )


def fallback(c: ClipCandidate, cfg: MetadataCfg, language: str = "en") -> VideoMetadata:
    if language == "sv":
        title, hook, desc = f"{c.streamer_display} i GTA 6 😳", "Vänta på det…", f"Galet ögonblick från {c.streamer_display}s GTA 6-stream."
    else:
        title, hook, desc = f"{c.streamer_display} in GTA 6 😳", "Wait for it…", f"Wild moment from {c.streamer_display}'s GTA 6 stream."
    if c.title and len(c.title) < 45:
        title = f"{c.title.strip()} – {c.streamer_display}"
    return assemble(c, title, hook, desc, ["#GTA6", "#GTAVI", "#Gaming"], cfg, "fallback")


def generate(c: ClipCandidate, transcript: str, cfg: MetadataCfg, language: str,
             api_key: str | None, client=None) -> VideoMetadata:
    """Försök med Claude; vid alla fel -> fallback (pipelinen ska aldrig stanna här)."""
    if client is None:
        if not api_key:
            log.warning("ANTHROPIC_API_KEY saknas – använder fallback-titel")
            return fallback(c, cfg, language)
        import anthropic
        client = anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=60)

    lang_name = {"en": "English", "sv": "Swedish"}[language]
    user = json.dumps({
        "streamer": c.streamer_display,
        "platform": c.platform,
        "clip_title": c.title,
        "views": c.views,
        "transcript": transcript[:4000],
    }, ensure_ascii=False)
    try:
        resp = client.beta.messages.create(
            model=cfg.model,
            max_tokens=2000,
            system=SYSTEM.format(max_title=cfg.max_title_chars, language=lang_name),
            messages=[{"role": "user", "content": f"Clip data:\n{user}"}],
            output_config={"effort": cfg.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if resp.stop_reason == "refusal":
            raise ValueError("modellen avböjde")
        if resp.stop_reason == "max_tokens":
            raise ValueError("svaret trunkerades")
        text = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(text)
        meta = assemble(c, data["title"], data["hook"], data["description"], data["hashtags"], cfg, "claude")
        log.info("metadata från claude", extra={"clip": c.key, "title": meta.title})
        return meta
    except Exception as e:  # noqa: BLE001 – medvetet bred: fallback vid alla API-/parsefel
        log.warning("Claude misslyckades – fallback-titel", extra={"clip": c.key, "error": str(e)[:300]})
        return fallback(c, cfg, language)
