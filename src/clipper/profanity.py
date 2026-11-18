"""Censur av svordomar i undertexter och titlar (bättre för YouTube-monetisering).

"fucking" -> "f******", "Shit!" -> "S***!". Första bokstaven och skiljetecken behålls.
"""
from __future__ import annotations

import re

# Stammar som matchar var som helst i ordet (fucking, motherfucker, bullshit, bitches ...)
_ANYWHERE = ("fuck", "shit", "bitch", "nigg")
# Stammar som matchar början av ordet
_PREFIX_EN = ("cunt", "motherf", "asshole", "bastard", "slut", "whore", "pussy", "retard", "dickhead")
# Exakta ord
_EXACT_EN = {"ass", "asses", "dick", "dicks", "cock", "cocks", "damn", "goddamn", "dammit", "piss",
             "pissed", "tits", "wtf", "stfu", "fck", "fk", "fuk", "fag", "fags", "faggot"}
_PREFIX_SV = ("jävl", "javl", "jävel", "helvet", "fitt", "kuksug")
_EXACT_SV = {"fan", "fanken", "skit", "kuk", "kukar", "hora", "horor", "satan"}

_WORD = re.compile(r"[\wåäöÅÄÖ']+", re.UNICODE)


def is_profane(word: str, language: str = "en") -> bool:
    w = word.lower().strip("'")
    if not w:
        return False
    if language == "sv" and w.startswith("slut"):
        return False                      # svenska: "slut" = end
    if any(s in w for s in _ANYWHERE) or w in _EXACT_EN or w.startswith(_PREFIX_EN):
        return True
    if language == "sv" and (w in _EXACT_SV or w.startswith(_PREFIX_SV)):
        return True
    return False


def _mask(word: str) -> str:
    return word[0] + "*" * (len(word) - 1) if len(word) > 1 else "*"


def censor(text: str, language: str = "en") -> str:
    return _WORD.sub(lambda m: _mask(m.group(0)) if is_profane(m.group(0), language) else m.group(0), text)
