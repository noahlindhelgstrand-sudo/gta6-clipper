import subprocess
from types import SimpleNamespace

import faster_whisper
import numpy as np

from clipper import transcribe as tr


class FakeModel:
    def __init__(self, *a, **kw):
        self.kw = kw

    def transcribe(self, audio, **kw):
        # vi skickar en färdig numpy-array (ingen PyAV-avkodning i faster-whisper)
        assert isinstance(audio, np.ndarray) and audio.dtype == np.float32
        assert kw["word_timestamps"] is True
        W = lambda s, e, w: SimpleNamespace(start=s, end=e, word=w)  # noqa: E731
        segs = [SimpleNamespace(words=[W(0.0, 0.4, " Hello"), W(0.5, 0.9, " world"), W(50, 51, " late")])]
        return iter(segs), SimpleNamespace(language="en")


def synth(path, seconds=2, video=True):
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if video:
        cmd += ["-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=30:duration={seconds}"]
    cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    if video:
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-shortest", str(path)]
    subprocess.run(cmd, check=True)
    return path


def test_load_audio_real_ffmpeg(tmp_path):
    audio = tr.load_audio(synth(tmp_path / "v.mp4", seconds=2))
    assert audio.dtype == np.float32
    assert abs(len(audio) / tr.SAMPLE_RATE - 2.0) < 0.1
    assert 0.05 < np.abs(audio).max() <= 1.0


def test_load_audio_respects_max_s(tmp_path):
    audio = tr.load_audio(synth(tmp_path / "v.mp4", seconds=3), max_s=1.0)
    assert abs(len(audio) / tr.SAMPLE_RATE - 1.0) < 0.1


def test_transcribe_words_and_cutoff(monkeypatch, tmp_path):
    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)
    tr._MODEL_CACHE.clear()
    t = tr.transcribe(synth(tmp_path / "v.mp4"), "base", "cpu", "int8", max_s=44.5)
    assert [w.text for w in t.words] == ["Hello", "world"]
    assert t.text == "Hello world" and t.language == "en"
