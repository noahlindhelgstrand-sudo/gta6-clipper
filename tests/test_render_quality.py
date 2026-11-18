"""Kvalitet: intro-frysning, 60 fps behålls, ljud normaliseras till ca -14 LUFS."""
import shutil
import subprocess

import pytest

from clipper.render import probe, render_short
from test_render import WORDS, fast_cfg, synth_video

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg saknas")


def test_intro_freeze_frame_adds_length(tmp_path):
    src = synth_video(tmp_path / "src.mp4", seconds=4)
    info = probe(render_short(src, tmp_path / "o.mp4", fast_cfg(hook_intro_s=0.5), WORDS, "hook", "c"))
    assert info.duration == pytest.approx(4.5, abs=0.15)


def test_keeps_60fps_source_and_normalizes_loudness(tmp_path):
    src = tmp_path / "s60.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=60:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-af", "volume=-25dB",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(src)], check=True)
    out = render_short(src, tmp_path / "o.mp4", fast_cfg(), WORDS, "hook", "c")
    assert probe(out).fps == pytest.approx(60, abs=1)
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(out), "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                       capture_output=True, text=True)
    lufs = float(r.stderr.split("I:")[-1].split("LUFS")[0])
    assert -17 < lufs < -11            # ungefär -14 LUFS (YouTube-nivå)
