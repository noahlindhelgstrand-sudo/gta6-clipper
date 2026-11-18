"""Genererar ASS-undertexter: ord-för-ord (aktivt ord markerat), hook överst, credit."""
from __future__ import annotations

from dataclasses import dataclass

from .profanity import censor
from .transcribe import Word


@dataclass
class Layout:
    width: int = 1080
    height: int = 1920
    subs_margin_v: int = 560      # avstånd från nederkant – nedre tredjedelen
    hook_margin_v: int = 150      # från överkant


def _ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def chunk_words(words: list[Word], max_words: int = 3, max_gap: float = 0.6) -> list[list[Word]]:
    """Gruppera ord i korta fraser (max 3 ord) som visas tillsammans."""
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur and (len(cur) >= max_words or w.start - cur[-1].end > max_gap
                    or cur[-1].text.endswith((".", "!", "?", ","))):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[Word], hook: str, credit: str, offset: float, hook_until: float,
              total: float, layout: Layout = Layout(), font: str = "DejaVu Sans",
              censor_lang: str | None = None) -> str:
    clean = (lambda t: censor(t, censor_lang)) if censor_lang else (lambda t: t)
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {layout.width}
PlayResY: {layout.height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,{font},96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,7,3,2,60,60,{layout.subs_margin_v},1
Style: Hook,{font},72,&H00FFFFFF,&H00FFFFFF,&H00000000,&HC0000000,1,0,0,0,100,100,0,0,3,18,0,8,70,70,{layout.hook_margin_v},1
Style: Credit,{font},34,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,40,40,70,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if hook:
        lines.append(f"Dialogue: 2,{_ts(0)},{_ts(hook_until)},Hook,,0,0,0,,{{\\fad(80,200)}}{_esc(clean(hook))}")
    if credit:
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(total)},Credit,,0,0,0,,{_esc(credit)}")
    for chunk in chunk_words(words):
        chunk_end = chunk[-1].end
        for i, w in enumerate(chunk):
            start = w.start + offset
            end = (chunk[i + 1].start if i + 1 < len(chunk) else chunk_end) + offset
            if end - start < 0.05:
                end = start + 0.05
            parts = []
            for j, x in enumerate(chunk):
                t = _esc(clean(x.text).upper())
                parts.append(f"{{\\c&H00E6FF&}}{t}{{\\c&HFFFFFF&}}" if j == i else t)
            pop = "{\\fscx108\\fscy108\\t(0,90,\\fscx100\\fscy100)}"
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(min(end, total))},Word,,0,0,0,,{pop}{' '.join(parts)}")
    return head + "\n".join(lines) + "\n"
