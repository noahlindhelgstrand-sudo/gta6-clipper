"""Transkribering med faster-whisper (CPU, int8) med ordtidsstämplar."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .log import get_logger

log = get_logger("transcribe")


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Transcript:
    text: str
    words: list[Word]
    language: str | None = None


_MODEL_CACHE: dict[tuple, object] = {}


def transcribe(path: str | Path, model: str = "base", device: str = "cpu",
               compute_type: str = "int8", max_s: float | None = None) -> Transcript:
    from faster_whisper import WhisperModel

    key = (model, device, compute_type)
    if key not in _MODEL_CACHE:
        _MODEL_CACHE[key] = WhisperModel(model, device=device, compute_type=compute_type)
    wm = _MODEL_CACHE[key]
    segments, info = wm.transcribe(str(path), word_timestamps=True, vad_filter=True, beam_size=1)
    words: list[Word] = []
    for seg in segments:
        for w in seg.words or []:
            if max_s is not None and w.start >= max_s:
                break
            t = w.word.strip()
            if t:
                words.append(Word(float(w.start), float(min(w.end, max_s or w.end)), t))
    text = " ".join(w.text for w in words)
    log.info("transkriberat", extra={"words": len(words), "lang": info.language})
    return Transcript(text=text, words=words, language=info.language)
