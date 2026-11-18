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
    fps: float = 30.0


def probe(path: str | Path) -> ProbeInfo:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,avg_frame_rate:format=duration",
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
    fps = 30.0
    try:
        num, den = (v.get("avg_frame_rate") or "30/1").split("/")
        fps = float(num) / float(den) if float(den) else 30.0
    except (ValueError, ZeroDivisionError):
        pass
    return ProbeInfo(int(v["width"]), int(v["height"]), float(data["format"]["duration"]), has_audio, fps or 30.0)


def _even(x: float) -> int:
    return int(x) // 2 * 2


def _double_rate(rate: str) -> str:
    """'12M' -> '24M' (bufsize = 2 x maxrate)."""
    num = "".join(ch for ch in rate if ch.isdigit() or ch == ".")
    unit = rate[len(num):] or ""
    return f"{float(num) * 2:g}{unit}"


def _ass_path(p: Path) -> str:
    # escape för ffmpeg-filtersyntax
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def output_fps(src: ProbeInfo, cfg: RenderCfg) -> int:
    """Behåll källans bildfrekvens (60 fps ser mycket mjukare ut) men max cfg.fps."""
    return max(24, min(cfg.fps, round(src.fps))) if src.fps else min(cfg.fps, 30)


def build_filtergraph(src: ProbeInfo, cfg: RenderCfg, ass_file: Path,
                      facecam: FacecamRect | None) -> tuple[str, Layout]:
    """Två layouter:
      facecam:  [ facecam överst (W x facecam_height) ] + [ gameplay fyller resten ]  – undertexter i skarven
      utan cam: [ blurrad bakgrund ] med stor gameplay i mitten (W x nocam_gameplay_height)
    """
    W, H = cfg.width, cfg.height
    fps = output_fps(src, cfg)
    parts = []
    if facecam:
        cam_h = _even(cfg.facecam_height)
        gp_h = H - cam_h
        gp_y = cam_h
        parts.append(f"[0:v]setpts=PTS-STARTPTS,fps={fps},split=2[gpsrc][camsrc]")
    else:
        gp_h = _even(cfg.nocam_gameplay_height)
        gp_y = _even((H - gp_h) / 2 - 40)
        parts.append(f"[0:v]setpts=PTS-STARTPTS,fps={fps},split=2[bgsrc][gpsrc]")
        # billig blur: skala ner, blurra, skala upp
        parts.append(f"[bgsrc]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
                     f"boxblur=10:2,eq=brightness=-0.12,scale={W}:{H}[bg]")
    # Gameplay: mittutsnitt med samma bildformat som ytan, skalat med lanczos för skärpa
    crop_w = min(src.width, _even(src.height * W / gp_h))
    crop_h = min(src.height, _even(crop_w * gp_h / W))
    parts.append(f"[gpsrc]crop={crop_w}:{crop_h},scale={W}:{gp_h}:flags=lanczos,unsharp=5:5:0.4[gp]")
    if facecam:
        cx, cy = _even(facecam.x * src.width), _even(facecam.y * src.height)
        cw, ch = max(2, _even(facecam.w * src.width)), max(2, _even(facecam.h * src.height))
        parts.append(f"[camsrc]crop={cw}:{ch}:{cx}:{cy},scale={W}:{cam_h}:flags=lanczos,unsharp=5:5:0.5[cam]")
        parts.append(f"color=c=black:s={W}x{H}:r={fps}[canvas]")
        parts.append("[canvas][cam]overlay=0:0:shortest=1[c1]")
        parts.append(f"[c1][gp]overlay=0:{gp_y}[base]")
        # undertexterna centreras kring skarven mellan kamera och gameplay
        layout = Layout(W, H, subs_margin_v=max(120, H - cam_h - 70), hook_margin_v=60)
    else:
        parts.append(f"[bg][gp]overlay=0:{gp_y}[base]")
        layout = Layout(W, H, subs_margin_v=max(120, H - (gp_y + gp_h) + 110),
                        hook_margin_v=max(60, gp_y // 2 - 60))
    intro = f"tpad=start_duration={cfg.hook_intro_s}:start_mode=clone," if cfg.hook_intro_s > 0 else ""
    parts.append(f"[base]{intro}ass='{_ass_path(ass_file)}',format=yuv420p[vout]")
    return ";".join(parts), layout


def render_short(src_path: str | Path, out_path: str | Path, cfg: RenderCfg, words: list[Word],
                 hook: str, credit: str, facecam: FacecamRect | None = None,
                 language: str = "en") -> Path:
    src_path, out_path = Path(src_path), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    info = probe(src_path)
    if facecam is None and cfg.auto_facecam:
        from .facecam import detect_facecam
        facecam = detect_facecam(src_path, info.width, info.height, info.duration,
                                 target_aspect=cfg.width / cfg.facecam_height)
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
        # loudnorm till -14 LUFS (YouTubes uppspelningsnivå) – annars låter klippen mycket tystare än andra Shorts
        delay = f"adelay={delay_ms}:all=1," if delay_ms else ""
        graph += f";[0:a]aresample=48000,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,{delay}apad[aout]"
    else:
        cmd += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        graph += ";[1:a]anull[aout]"
    cmd += [
        "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
        "-t", f"{total:.3f}",
        "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf), "-profile:v", "high",
        "-maxrate", cfg.max_bitrate, "-bufsize", _double_rate(cfg.max_bitrate),
        "-r", str(output_fps(info, cfg)), "-g", str(output_fps(info, cfg) * 2), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", cfg.audio_bitrate, "-ar", "48000",
        "-movflags", "+faststart", str(out_path),
    ]
    log.info("renderar", extra={"src": str(src_path), "out": str(out_path), "seconds": round(total, 2),
                                "fps": output_fps(info, cfg), "layout": "facecam" if facecam else "gameplay"})
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RenderError(f"ffmpeg misslyckades: {r.stderr[-1500:]}")
    ass_file.unlink(missing_ok=True)
    return out_path


def extract_frames(path: str | Path, n: int = 4, width: int = 512) -> list[bytes]:
    """n JPEG-bildrutor jämnt fördelade i klippet (för Claude-granskning)."""
    try:
        dur = probe(path).duration
    except RenderError:
        return []
    frames = []
    for i in range(n):
        t = dur * (i + 1) / (n + 1)
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                            "-vf", f"scale={width}:-2", "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                           capture_output=True)
        if r.returncode == 0 and r.stdout:
            frames.append(r.stdout)
    return frames
