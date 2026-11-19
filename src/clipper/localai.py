"""Gratis, lokal AI (ingen API-nyckel): körs på GitHub-runnerns CPU.

- CLIP (openai/clip-vit-base-patch32) tittar på bildrutor: suggestivt innehåll (strippklubb,
  nakenhet) och tråkiga bilder (menyer, laddningsskärmar), samt beskriver scenen.
- En liten instruktionsmodell (Qwen2.5-1.5B-Instruct) skriver titel/hook/beskrivning/hashtags.

Modellerna laddas ner från Hugging Face första gången och cachas (actions/cache).
Allt här är "best effort": vid fel används mallar (metadata.fallback).
"""
from __future__ import annotations

import io
import json
import re
import time

from .log import get_logger

log = get_logger("localai")

SUGGESTIVE = ["a strip club with dancing women", "a woman dancing in lingerie", "a nearly naked woman",
              "a pole dancer on stage", "a lap dance in a dark club"]
BORING = ["a video game main menu", "a loading screen", "a character creation menu",
          "a pause menu with a map", "a black screen", "a video game settings menu",
          "a game console home screen"]
SCENES = ["a car chase in a video game city", "a police chase with police cars", "a shootout in a video game",
          "a person driving a car in a video game", "a streamer talking to a webcam",
          "two characters talking in a video game", "a city street at night in a video game",
          "an explosion in a video game", "a beach in a sunny video game city", "a robbery in a video game",
          "a motorcycle stunt in a video game", "a helicopter flying over a city"]
LABELS = SUGGESTIVE + BORING + SCENES

_CLIP = None
_LLM = None


def available() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        return True
    except ImportError:
        return False


def _clip(model_name: str):
    global _CLIP
    if _CLIP is None:
        from transformers import CLIPModel, CLIPProcessor
        t = time.time()
        _CLIP = (CLIPModel.from_pretrained(model_name).eval(), CLIPProcessor.from_pretrained(model_name))
        log.info("CLIP laddad", extra={"model": model_name, "s": round(time.time() - t, 1)})
    return _CLIP


def look(frames: list[bytes], model_name: str = "openai/clip-vit-base-patch32") -> dict:
    """Sannolikhetsmassa per kategori, medel över bildrutorna."""
    import torch
    from PIL import Image
    if not frames:
        return {"suggestive": 0.0, "boring": 0.0, "scene": ""}
    model, proc = _clip(model_name)
    images = [Image.open(io.BytesIO(f)).convert("RGB") for f in frames]
    with torch.no_grad():
        inp = proc(text=[f"a screenshot of {l}" for l in LABELS], images=images, return_tensors="pt", padding=True)
        probs = model(**inp).logits_per_image.softmax(dim=-1).mean(dim=0).tolist()
    p = dict(zip(LABELS, probs))
    scene = max(SCENES, key=lambda l: p[l])
    out = {"suggestive": round(sum(p[l] for l in SUGGESTIVE), 3),
           "boring": round(sum(p[l] for l in BORING), 3),
           "scene": scene, "scene_p": round(p[scene], 3)}
    log.info("CLIP-bedömning", extra=out)
    return out


def _llm(model_name: str):
    global _LLM
    if _LLM is None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        t = time.time()
        torch.set_num_threads(max(1, torch.get_num_threads()))
        tok = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForCausalLM.from_pretrained(model_name, torch_dtype=torch.float32).eval()
        _LLM = (model, tok)
        log.info("språkmodell laddad", extra={"model": model_name, "s": round(time.time() - t, 1)})
    return _LLM


PROMPT = """You write metadata for a YouTube Short / Instagram Reel cut from a {streamer} GTA 6 livestream.
What is on screen: {scene}.
Original clip title: "{clip_title}"
What the streamer says: "{transcript}"

Reply with ONLY a JSON object, in {language}:
{{"title": "<punchy, curiosity-driven title, max 55 characters, no hashtags>",
  "hook": "<2-6 words shown on screen at the start, e.g. He did NOT expect this>",
  "description": "<one sentence about the moment>",
  "hashtags": ["#GTA6", "<3 more relevant hashtags>"]}}
Rules: be truthful to the transcript, no profanity, no sexual words, mention {streamer} in the title."""


def write(streamer: str, clip_title: str, transcript: str, scene: str, language: str = "en",
          model_name: str = "Qwen/Qwen2.5-1.5B-Instruct", max_new_tokens: int = 160) -> dict | None:
    import torch
    model, tok = _llm(model_name)
    prompt = PROMPT.format(streamer=streamer, scene=scene or "GTA 6 gameplay", clip_title=clip_title[:120],
                           transcript=transcript[:1200] or "(no speech)",
                           language={"en": "English", "sv": "Swedish"}.get(language, "English"))
    text = tok.apply_chat_template([{"role": "user", "content": prompt}], tokenize=False, add_generation_prompt=True)
    ids = tok(text, return_tensors="pt")
    t = time.time()
    with torch.no_grad():
        out = model.generate(**ids, max_new_tokens=max_new_tokens, do_sample=False, repetition_penalty=1.05)
    reply = tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)
    log.info("språkmodell svarade", extra={"s": round(time.time() - t, 1), "reply": reply[:300]})
    return parse_reply(reply)


def parse_reply(reply: str) -> dict | None:
    m = re.search(r"\{.*\}", reply, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not str(data.get("title", "")).strip():
        return None
    tags = data.get("hashtags") or []
    if isinstance(tags, str):
        tags = tags.split()
    return {"title": str(data["title"]).strip().strip('"'), "hook": str(data.get("hook", "")).strip().strip('"'),
            "description": str(data.get("description", "")).strip(), "hashtags": [str(t) for t in tags][:5]}


EXCITED = re.compile(r"\b(no way|oh my god|what the|bro|lmao|lol|let'?s go|holy|wait|run|crazy|insane)\b", re.I)


def interest_score(vision: dict, transcript: str) -> int:
    """Grov lokal uppskattning 1–10 (Claude gör detta bättre om nyckel finns)."""
    score = 6
    if vision.get("boring", 0) > 0.5:
        score = 2
    elif vision.get("boring", 0) > 0.3:
        score -= 2
    words = len(transcript.split())
    if words < 5:
        score -= 1
    hits = len(EXCITED.findall(transcript))
    score += min(2, hits // 2)
    return max(1, min(10, score))


def smoke(video_paths: list[str], db_path: str | None = None) -> list[dict]:
    """Används av workflowen smoke-ai: hela lokala kedjan (Whisper -> CLIP -> Qwen) på riktiga videor."""
    import sqlite3
    from datetime import datetime, timezone
    from pathlib import Path

    from .config import MetadataCfg
    from .metadata import generate_local
    from .models import ClipCandidate
    from .render import extract_frames
    from .transcribe import transcribe

    info = {}
    if db_path:
        con = sqlite3.connect(db_path)
        info = {r[0]: (r[1], r[2], r[3]) for r in
                con.execute("SELECT video_file, streamer, streamer_display, title FROM clips WHERE video_file IS NOT NULL")}
    results = []
    for p in video_paths:
        t = time.time()
        streamer, display, title = info.get(Path(p).name, ("streamer", "Streamer", ""))
        tr = transcribe(p, "small", "cpu", "int8")
        c = ClipCandidate(platform="twitch", clip_id=Path(p).stem, streamer=streamer, streamer_display=display,
                          title=title or "", views=0, url="https://example.invalid", duration_s=0,
                          created_at=datetime.now(timezone.utc))
        frames = extract_frames(p, n=4)
        vision = look(frames)
        meta = generate_local(c, tr.text, MetadataCfg(), "en", frames)
        results.append({"file": Path(p).name, "streamer": display, "clip_title": title,
                        "transcript": tr.text[:300], "vision": vision, "title": meta.title, "hook": meta.hook,
                        "hashtags": meta.hashtags, "generated_by": meta.generated_by,
                        "suggestive": meta.suggestive, "interest": meta.interest,
                        "seconds": round(time.time() - t, 1)})
    return results


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    ap.add_argument("videos", nargs="+")
    a = ap.parse_args()
    print(json.dumps(smoke(a.videos, a.db), indent=2, ensure_ascii=False))
