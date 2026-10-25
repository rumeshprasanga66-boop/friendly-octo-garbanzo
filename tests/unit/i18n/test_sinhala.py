"""Sinhala (``si``) is a first-class recognised language, but not a phrase locale.

Two different things are asserted here, and the distinction matters:

* Sinhala TEXT must be recognised, so a Sinhala turn is answered in Sinhala
  (``detect_conversation_language`` -> ``"si"``) and a Sinhala STT tag from
  Whisper resolves to ``si``.
* Sinhala must NOT join ``SUPPORTED_LOCALES``: that tuple means "has a
  translated catalog of canned system phrases", and adding ``si`` there
  without a ``si.json`` would fail ``tests/unit/i18n/test_catalog.py``.
  Canned phrases fall back to English, which is the documented behaviour.
"""

from __future__ import annotations

import pytest

from dodaios.core.turn_language import (
    detect_conversation_language,
    detect_text_language,
    normalize_any_language_tag,
)
from dodaios.i18n.locales import (
    SUPPORTED_LOCALES,
    is_known_language,
    language_name,
    phrase_locale,
)

#: Real Sinhala sentences, not transliteration — the script is the signal.
SINHALA_SENTENCES = (
    "ආයුබෝවන්, මට අද කාලගුණය කියන්න පුළුවන්ද?",
    "මම හෙට කොළඹ යනවා. රැස්වීමක් තියෙනවා.",
    "මට උදව් කරන්න පුළුවන්ද?",
)


@pytest.mark.parametrize("text", SINHALA_SENTENCES)
def test_sinhala_text_is_recognised_as_sinhala(text: str) -> None:
    assert detect_conversation_language(text) == "si"


@pytest.mark.parametrize("text", SINHALA_SENTENCES)
def test_sinhala_is_not_claimed_as_a_phrase_locale(text: str) -> None:
    """Recognised, but with no translated catalog — the phrases stay English."""
    assert detect_conversation_language(text) == "si"
    assert detect_text_language(text) == "unknown", (
        "detect_text_language only names the four catalog languages"
    )
    assert phrase_locale("si") == "en"


def test_sinhala_is_a_known_language_with_a_name() -> None:
    assert is_known_language("si")
    assert language_name("si") == "Sinhala"


def test_sinhala_did_not_join_the_phrase_catalog() -> None:
    """Adding 'si' to SUPPORTED_LOCALES needs an si.json; do not half-add it."""
    assert "si" not in SUPPORTED_LOCALES


@pytest.mark.parametrize(
    "tag",
    ["si", "SI", "sin", "Sinhala", "sinhalese", "si-LK"],
)
def test_sinhala_stt_and_bcp47_tags_resolve(tag: str) -> None:
    """Whisper emits ISO codes and names; the UI emits BCP-47."""
    resolved = normalize_any_language_tag(tag)
    assert resolved == "si", f"{tag!r} resolved to {resolved!r}"


def test_sinhala_does_not_hijack_other_languages() -> None:
    """The script signal must not turn neighbouring languages into Sinhala."""
    assert detect_conversation_language("Hello, how are you today?") == "en"
    assert detect_conversation_language("Wie geht es dir heute?") == "de"
    # Tamil is its own script and must not be reported as Sinhala.
    assert detect_conversation_language("வணக்கம், இன்று வானிலை எப்படி?") != "si"
