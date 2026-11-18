from types import SimpleNamespace

import faster_whisper

from clipper import transcribe as tr


class FakeModel:
    def __init__(self, *a, **kw):
        self.kw = kw

    def transcribe(self, path, **kw):
        assert kw["word_timestamps"] is True
        W = lambda s, e, w: SimpleNamespace(start=s, end=e, word=w)  # noqa: E731
        segs = [SimpleNamespace(words=[W(0.0, 0.4, " Hello"), W(0.5, 0.9, " world"), W(50, 51, " late")])]
        return iter(segs), SimpleNamespace(language="en")


def test_transcribe_words_and_cutoff(monkeypatch):
    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)
    tr._MODEL_CACHE.clear()
    t = tr.transcribe("x.mp4", "base", "cpu", "int8", max_s=44.5)
    assert [w.text for w in t.words] == ["Hello", "world"]
    assert t.text == "Hello world" and t.language == "en"
