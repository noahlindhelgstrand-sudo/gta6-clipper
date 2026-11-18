from clipper.config import MetadataCfg
from clipper.metadata import fallback, generate
from clipper.mocks import FakeAnthropic
from conftest import make_clip


def test_claude_metadata_has_credit_and_shorts():
    c = make_clip("abc", "alice")
    m = generate(c, "no way", MetadataCfg(), "en", None, client=FakeAnthropic())
    assert m.generated_by == "claude"
    assert m.title.endswith("#Shorts") and len(m.title) <= 70
    assert c.url in m.description and "Alice" in m.description
    assert 3 <= len(m.hashtags) <= 5 and "#GTA6" in m.hashtags


def test_api_failure_uses_fallback():
    m = generate(make_clip(), "x", MetadataCfg(), "en", "key", client=FakeAnthropic(fail=True))
    assert m.generated_by == "fallback" and m.title.endswith("#Shorts")


def test_missing_key_uses_fallback():
    assert generate(make_clip(), "x", MetadataCfg(), "en", None).generated_by == "fallback"


def test_long_title_truncated():
    c = make_clip()
    c.title = "x" * 200
    m = fallback(c, MetadataCfg(max_title_chars=70))
    assert len(m.title) <= 70


def test_swedish_fallback():
    c = make_clip()
    c.title = ""
    assert "i GTA 6" in fallback(c, MetadataCfg(), "sv").title
