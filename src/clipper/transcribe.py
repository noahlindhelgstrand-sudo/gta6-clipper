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
SAMPLE_RATE = 16000


def load_audio(path: str | Path, max_s: float | None = None):
    """Avkoda ljud med ffmpeg till 16 kHz mono float32.

    Vi undviker faster-whispers egen avkodning (PyAV), eftersom nya PyAV-versioner
    har tagit bort parametrar som faster-whisper använder."""
    import subprocess

    import numpy as np

    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path)]
    if max_s:
        cmd += ["-t", f"{max_s:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg kunde inte avkoda ljud: {r.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


def transcribe(path: str | Path, model: str = "base", device: str = "cpu",
               compute_type: str = "int8", max_s: float | None = None) -> Transcript:
    from faster_whisper import WhisperModel

    key = (model, device, compute_type)
    if key not in _MODEL_CACHE:
        _MODEL_CACHE[key] = WhisperModel(model, device=device, compute_type=compute_type)
    wm = _MODEL_CACHE[key]
    audio = load_audio(path, max_s)
    if audio.size == 0:
        log.info("inget ljud att transkribera", extra={"file": str(path)})
        return Transcript(text="", words=[], language=None)
    segments, info = wm.transcribe(audio, word_timestamps=True, vad_filter=True, beam_size=1)
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
