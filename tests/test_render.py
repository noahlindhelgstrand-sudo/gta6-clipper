import shutil
import subprocess

import pytest

from clipper.config import FacecamRect, RenderCfg
from clipper.render import probe, render_short
from clipper.subtitles import build_ass, chunk_words
from clipper.transcribe import Word

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg saknas")


def synth_video(path, seconds=8, w=1280, h=720, audio=True):
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=30:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    return path


WORDS = [Word(0.2, 0.5, "no"), Word(0.5, 0.9, "way"), Word(1.0, 1.4, "bro!"), Word(2.0, 2.6, "look")]


def fast_cfg(**kw):
    return RenderCfg(preset="ultrafast", crf=32, **kw)


def test_render_dimensions_and_duration(tmp_path):
    src = synth_video(tmp_path / "src.mp4", seconds=8)
    out = render_short(src, tmp_path / "out.mp4", fast_cfg(), WORDS, "He did WHAT?", "🎥 twitch.tv/alice")
    info = probe(out)
    assert (info.width, info.height) == (1080, 1920)
    assert info.has_audio
    assert info.duration == pytest.approx(8.5, abs=0.15)   # 8 s + 0,5 s intro


def test_render_caps_at_max_duration(tmp_path):
    src = synth_video(tmp_path / "long.mp4", seconds=50)
    cfg = fast_cfg(max_duration_s=45)
    info = probe(render_short(src, tmp_path / "out.mp4", cfg, WORDS, "hook", "credit"))
    assert (info.width, info.height) == (1080, 1920)
    assert info.duration <= 45.1
    assert info.duration == pytest.approx(45, abs=0.15)


def test_render_with_facecam_and_no_audio(tmp_path):
    src = synth_video(tmp_path / "src.mp4", seconds=4, w=1920, h=1080, audio=False)
    cam = FacecamRect(x=0.0, y=0.0, w=0.25, h=0.28)
    info = probe(render_short(src, tmp_path / "out.mp4", fast_cfg(), [], "", "", facecam=cam))
    assert (info.width, info.height) == (1080, 1920)
    assert info.has_audio      # tyst spår läggs till (krävs av vissa plattformar)
    assert info.duration == pytest.approx(4.5, abs=0.15)


def test_chunk_words_groups_max_three():
    chunks = chunk_words([Word(i * 0.3, i * 0.3 + 0.25, f"w{i}") for i in range(7)])
    assert [len(c) for c in chunks] == [3, 3, 1]


def test_ass_has_word_events_shifted_by_intro():
    ass = build_ass(WORDS, "HOOK", "credit", offset=0.5, hook_until=3.5, total=10)
    events = [l for l in ass.splitlines() if l.startswith("Dialogue: 0,")]
    assert len(events) == len(WORDS)
    assert events[0].startswith("Dialogue: 0,0:00:00.70,")
    assert "Style: Hook" in ass and ",Hook,,0,0,0,," in ass
