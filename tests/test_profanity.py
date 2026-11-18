from clipper.config import MetadataCfg
from clipper.metadata import fallback
from clipper.profanity import censor, is_profane
from clipper.subtitles import build_ass
from clipper.transcribe import Word
from conftest import make_clip


def test_censor_english():
    assert censor("What the fuck, that's bullshit!") == "What the f***, that's b*******!"
    assert censor("Motherfucker") == "M***********"
    assert censor("wtf bro") == "w** bro"


def test_innocent_words_untouched():
    for w in ["fan", "fantastic", "class", "assist", "cocktail", "Dickens", "shipping", "skit", "Scunthorpe"]:
        assert not is_profane(w), w


def test_swedish_only_in_swedish():
    assert censor("fan vad jävligt", "sv") == "f** vad j******"
    assert censor("big fan", "en") == "big fan"
    assert censor("nu är det slut", "sv") == "nu är det slut"


def test_subtitles_censored_but_not_when_disabled():
    words = [Word(0, 0.4, "holy"), Word(0.5, 0.9, "shit")]
    on = build_ass(words, "", "", 0.5, 3, 10, censor_lang="en")
    off = build_ass(words, "", "", 0.5, 3, 10, censor_lang=None)
    assert "S***" in on and "SHIT" not in on
    assert "SHIT" in off


def test_titles_censored_but_credit_urls_intact():
    c = make_clip("x", "fuckboy99")
    c.title = "wtf is this shit"
    m = fallback(c, MetadataCfg())
    assert "shit" not in m.title and "wtf" not in m.title
    assert "https://twitch.tv/fuckboy99" in m.description   # länkar får aldrig förstöras
