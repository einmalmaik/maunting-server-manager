"""Google AI Studio (Gemini & Gemma) — Chatzugang, Einbettungen und Live-API.

Alles, was MSM über Google AI Studio weiß, steht in dieser Datei: Adressen,
Wortschatz, Katalogleser und Modellmerkmale.

Katalog:
Über die OpenAI-kompatible Schnittstelle von Google AI Studio
(``GET https://generativelanguage.googleapis.com/v1beta/openai/models``)
liefert Google alle freigegebenen Gemini-, Gemma- und Einbettungsmodelle.
Gemini-Modelle sind multimodal (Bild, Audio, Text), unterstützen hohe
Kontextfenster (bis zu 1M+ Tokens) und besitzen bei 2.0/2.5 Thinking-Stufen.
Gemma-Modelle sind Open-Source-Textmodelle mit 8.192 Tokens Kontextfenster.
Einbettungsmodelle wie ``text-embedding-004`` und ``gemini-embedding-001``
dienen der semantischen Vektorsuche.
"""

from __future__ import annotations

from services.ai_provider_registry.basis import Anbieter, Modell, positive_zahl


ANBIETER = Anbieter(
    kind="google",
    label="Google AI Studio",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    catalog_url="https://generativelanguage.googleapis.com/v1beta/openai/models",
    key_url="https://aistudio.google.com/app/apikey",
    key_prefix="AIza",
    katalog_braucht_schluessel=True,
    schluessel_kopf="Authorization",
    schluessel_praefix="Bearer ",
    katalog_liste_feld="data",
    empfehlung="gemini-2.5-flash",
    gehoer_wege=("chat",),
    gehoer_form="json",
    anfrage_erweiterungen=frozenset({"safety_settings"}),
    protokoll_chat="chat_completions",
    realtime_tauglich=True,
)


def get_safety_settings(disable_safety: bool = False) -> list[dict[str, str]]:
    """Gibt die Safety-Settings für Google AI Studio (Gemini und Gemma) zurück.

    Wird disable_safety=True gesetzt, werden alle 5 Schutzkategorien auf BLOCK_NONE gesetzt,
    sodass keine Filterung / Blockierung stattfindet.
    """
    threshold = "BLOCK_NONE" if disable_safety else "BLOCK_MEDIUM_AND_ABOVE"
    return [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": threshold},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": threshold},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": threshold},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": threshold},
        {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": threshold},
    ]


def katalog_lesen(rohdaten: dict) -> Modell | None:
    """Liest einen Modellkatalog-Eintrag von Google AI Studio.

    Erkennt Gemini-, Gemma- und Einbettungsmodelle und bildet deren
    Fähigkeiten (Kontextfenster, Ausgabelimit, Bildsicht, Denkstufen) ab.
    """
    model_id = rohdaten.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        return None

    # Normalisiere models/ Präfix von Google
    if model_id.startswith("models/"):
        model_id = model_id[len("models/"):]

    model_lower = model_id.lower()

    # Kontext und Ausgabetokens aus den Rohdaten, falls vorhanden
    # Berücksichtigt sowohl OpenAI-kompatible Felder als auch native Google REST-Felder
    kontext = (
        positive_zahl(rohdaten.get("context_length"))
        or positive_zahl(rohdaten.get("input_token_limit"))
        or positive_zahl(rohdaten.get("inputTokenLimit"))
    )
    max_ausgabe = (
        positive_zahl(rohdaten.get("max_completion_tokens"))
        or positive_zahl(rohdaten.get("output_token_limit"))
        or positive_zahl(rohdaten.get("outputTokenLimit"))
    )

    # Gemma Open-Source-Modelle (Textmodelle, 8k Fenster)
    if "gemma" in model_lower:
        return Modell(
            model_id=model_id,
            name=model_id,
            denkt=False,
            stufen=(),
            standard_stufe=None,
            zwingend=False,
            kontext_tokens=kontext or 8_192,
            max_ausgabe_tokens=max_ausgabe or 4_096,
            sieht=False,
        )

    # Einbettungsmodelle (text-embedding-004, gemini-embedding-001)
    if "embedding" in model_lower:
        return Modell(
            model_id=model_id,
            name=model_id,
            denkt=False,
            stufen=(),
            standard_stufe=None,
            zwingend=False,
            kontext_tokens=kontext or 2_048,
            max_ausgabe_tokens=max_ausgabe or 1,
            sieht=False,
        )

    # Gemini-Modelle (Multimodal, bis 1M+ Kontext, Thinking für neuere Versionen)
    if "gemini" in model_lower:
        ist_denkend = any(
            t in model_lower for t in ("2.5", "2.0-flash-thinking", "thinking")
        )
        stufen = ("low", "medium", "high") if ist_denkend else ()

        return Modell(
            model_id=model_id,
            name=model_id,
            denkt=ist_denkend,
            stufen=stufen,
            standard_stufe="medium" if ist_denkend else None,
            zwingend=False,
            kontext_tokens=kontext or 1_048_576,
            max_ausgabe_tokens=max_ausgabe or 8_192,
            sieht=True,
        )

    # Generischer Rückfall für beliebige andere Google-Modelle
    return Modell(
        model_id=model_id,
        name=model_id,
        denkt=False,
        stufen=(),
        kontext_tokens=kontext or 32_768,
        max_ausgabe_tokens=max_ausgabe,
        sieht=None,
    )
