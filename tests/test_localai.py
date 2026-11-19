"""Gratis lokal AI: logiken runt modellerna (modellerna själva testas i workflowen smoke-ai)."""
from clipper.config import MetadataCfg
from clipper.localai import interest_score, parse_reply
from clipper.metadata import generate, generate_local
from conftest import make_clip


class FakeAI:
    def __init__(self, vision=None, reply=None, fail_write=False, installed=True):
        self.vision = vision or {"suggestive": 0.1, "boring": 0.05, "scene": "a police chase with police cars"}
        self.reply = reply if reply is not None else {
            "title": "Lacy escapes the cops at full speed", "hook": "No way he made it",
            "description": "A wild chase.", "hashtags": ["#GTA6", "#Lacy", "#ViceCity"]}
        self.fail_write = fail_write
        self.installed = installed
        self.calls = []

    def available(self):
        return self.installed

    def look(self, frames, model):
        self.calls.append(("look", len(frames)))
        return self.vision

    def write(self, streamer, clip_title, transcript, scene, language, model):
        self.calls.append(("write", scene))
        if self.fail_write:
            raise RuntimeError("OOM")
        return self.reply

    interest_score = staticmethod(interest_score)


def test_local_generates_title_hook_and_flags():
    ai = FakeAI()
    m = generate_local(make_clip("l", "lacy"), "bro no way let's go run", MetadataCfg(), "en", [b"jpg"], ai=ai)
    assert m.generated_by == "local"
    assert m.title.startswith("Lacy escapes the cops") and m.title.endswith("#Shorts")
    assert m.hook == "No way he made it"
    assert not m.suggestive and m.interest >= 6
    assert ("write", "a police chase with police cars") in ai.calls       # scenen skickas till språkmodellen


def test_clip_flags_strip_club_and_boring_menu():
    sug = generate_local(make_clip(), "hi", MetadataCfg(), "en", [b"j"], ai=FakeAI({"suggestive": 0.7, "boring": 0.0,
                                                                                    "scene": "x"}))
    assert sug.suggestive
    menu = generate_local(make_clip(), "", MetadataCfg(), "en", [b"j"], ai=FakeAI({"suggestive": 0.0, "boring": 0.8,
                                                                                    "scene": "x"}))
    assert menu.interest <= 3


def test_llm_failure_falls_back_but_keeps_vision():
    m = generate_local(make_clip(), "", MetadataCfg(), "en", [b"j"],
                       ai=FakeAI({"suggestive": 0.9, "boring": 0, "scene": ""}, fail_write=True))
    assert m.generated_by == "fallback" and m.suggestive


def test_not_installed_uses_template():
    m = generate_local(make_clip(), "", MetadataCfg(), "en", [], ai=FakeAI(installed=False))
    assert m.generated_by == "fallback"


def test_local_output_is_censored():
    ai = FakeAI(reply={"title": "Lacy loses his shit", "hook": "wtf bro", "description": "x", "hashtags": []})
    m = generate_local(make_clip(), "", MetadataCfg(), "en", [b"j"], ai=ai)
    assert "shit" not in m.title and "wtf" not in m.hook.lower()


def test_parse_reply_variants():
    assert parse_reply('Sure! {"title": "A", "hook": "B", "description": "C", "hashtags": "#a #b"}')["hashtags"] == ["#a", "#b"]
    assert parse_reply("no json here") is None
    assert parse_reply('{"title": ""}') is None
    assert parse_reply('```json\n{"title": "T", "hook": "H"}\n```')["title"] == "T"


def test_without_key_generate_routes_to_local(monkeypatch):
    import clipper.metadata as md
    seen = {}
    monkeypatch.setattr(md, "generate_local", lambda *a, **k: seen.setdefault("local", True) or md.fallback(a[0], a[2]))
    generate(make_clip(), "", MetadataCfg(), "en", api_key=None)
    assert seen.get("local")
