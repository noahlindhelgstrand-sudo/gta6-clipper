"""ffmpeg-rendering till vertikal 9:16.

Layout (1080x1920):
  ┌──────────────┐
  │  HOOK-TEXT   │  (första sekunderna)
  │ ┌──────────┐ │
  │ │ gameplay │ │  skarp, något inzoomad 16:9
  │ └──────────┘ │
  │  [facecam]   │  om position finns i config
  │  UNDERTEXTER │  nedre tredjedelen, ord-för-ord
  │   credit     │
  └──────────────┘
  bakgrund: samma video, förstorad + blurrad
Ett kort intro (hook_intro_s) visar en fryst första bildruta med hooken.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import FacecamRect, RenderCfg
from .log import get_logger
from .subtitles import Layout, build_ass
from .transcribe import Word

log = get_logger("render")


class RenderError(RuntimeError):
    pass


@dataclass
class ProbeInfo:
    width: int
    height: int
    duration: float
    has_audio: bool


def probe(path: str | Path) -> ProbeInfo:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RenderError(f"ffprobe misslyckades: {r.stderr[-500:]}")
    data = json.loads(r.stdout)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        raise RenderError("ingen videoström")
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    return ProbeInfo(int(v["width"]), int(v["height"]), float(data["format"]["duration"]), has_audio)


def _even(x: float) -> int:
    return int(x) // 2 * 2


def _ass_path(p: Path) -> str:
    # escape för ffmpeg-filtersyntax
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def build_filtergraph(src: ProbeInfo, cfg: RenderCfg, ass_file: Path,
                      facecam: FacecamRect | None, gameplay_zoom: float = 1.15) -> tuple[str, Layout]:
    W, H = cfg.width, cfg.height
    # Gameplay: beskär lite på sidorna (zoom) och skala till full bredd
    crop_w = _even(src.width / gameplay_zoom)
    fg_h = _even(W * src.height / crop_w)
    if facecam:
        fg_y = 260
        cam_w = 640
        cam_h = _even(cam_w * (facecam.h * src.height) / (facecam.w * src.width))
        cam_h = min(cam_h, 520)
        cam_y = fg_y + fg_h + 40
        subs_margin = max(120, H - (cam_y + cam_h) - 160)
    else:
        fg_y = _even((H - fg_h) / 2 - 140)
        subs_margin = max(120, H - (fg_y + fg_h) - 260)
    layout = Layout(W, H, subs_margin_v=subs_margin, hook_margin_v=max(60, fg_y - 200) if fg_y > 260 else 70)

    parts = [
        "[0:v]setpts=PTS-STARTPTS,fps={fps},split={n}[bgsrc][fgsrc]{camsplit}".format(
            fps=cfg.fps, n=3 if facecam else 2, camsplit="[camsrc]" if facecam else ""),
        # billig blur: skala ner, blurra, skala upp
        f"[bgsrc]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
        f"boxblur=10:2,eq=brightness=-0.08,scale={W}:{H}[bg]",
        f"[fgsrc]crop={crop_w}:ih,scale={W}:{fg_h}[fg]",
        f"[bg][fg]overlay=0:{fg_y}[base0]",
    ]
    last = "base0"
    if facecam:
        cx, cy = _even(facecam.x * src.width), _even(facecam.y * src.height)
        cw, ch = _even(facecam.w * src.width), _even(facecam.h * src.height)
        parts.append(f"[camsrc]crop={cw}:{ch}:{cx}:{cy},scale={cam_w}:{cam_h}[cam]")
        parts.append(f"[{last}][cam]overlay={(W - cam_w) // 2}:{cam_y}[base1]")
        last = "base1"
    parts.append(f"[{last}]tpad=start_duration={cfg.hook_intro_s}:start_mode=clone,"
                 f"ass='{_ass_path(ass_file)}',format=yuv420p[vout]")
    return ";".join(parts), layout


def render_short(src_path: str | Path, out_path: str | Path, cfg: RenderCfg, words: list[Word],
                 hook: str, credit: str, facecam: FacecamRect | None = None,
                 language: str = "en") -> Path:
    src_path, out_path = Path(src_path), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    info = probe(src_path)
    clip_len = min(info.duration, cfg.max_duration_s - cfg.hook_intro_s)
    total = clip_len + cfg.hook_intro_s
    ass_file = out_path.with_suffix(".ass")

    graph, layout = build_filtergraph(info, cfg, ass_file, facecam)
    ass_file.write_text(
        build_ass([w for w in words if w.start < clip_len], hook, credit, offset=cfg.hook_intro_s,
                  hook_until=cfg.hook_intro_s + cfg.hook_overlay_s, total=total, layout=layout, font=cfg.font,
                  censor_lang=language if cfg.censor_profanity else None),
        encoding="utf-8",
    )
    delay_ms = int(cfg.hook_intro_s * 1000)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-t", f"{clip_len:.3f}", "-i", str(src_path)]
    if info.has_audio:
        graph += f";[0:a]aresample=48000,adelay={delay_ms}:all=1,apad[aout]"
    else:
        cmd += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        graph += ";[1:a]anull[aout]"
    cmd += [
        "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
        "-t", f"{total:.3f}",
        "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf), "-profile:v", "high",
        "-r", str(cfg.fps), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", cfg.audio_bitrate, "-ar", "48000",
        "-movflags", "+faststart", str(out_path),
    ]
    log.info("renderar", extra={"src": str(src_path), "out": str(out_path), "seconds": round(total, 2)})
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RenderError(f"ffmpeg misslyckades: {r.stderr[-1500:]}")
    ass_file.unlink(missing_ok=True)
    return out_path
