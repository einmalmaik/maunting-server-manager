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
    catalog_url="https://generativelanguage.googleapis.com/v1beta/models",
    key_url="https://aistudio.google.com/app/apikey",
    key_prefix=None,
    katalog_braucht_schluessel=True,
    schluessel_kopf="Authorization",
    schluessel_praefix="Bearer ",
    katalog_schluessel_kopf="x-goog-api-key",
    katalog_schluessel_praefix="",
    katalog_liste_feld="models",
    empfehlung="gemini-2.5-flash",
    gehoer_wege=("chat",),
    gehoer_form="json",
    anfrage_erweiterungen=frozenset({"reasoning_effort"}),
    protokoll_chat="chat_completions",
    sprachwege=("gemini_live",),
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
    Fähigkeiten (Kontextfenster, Ausgabelimit, Bildsicht, Denkstufen)
    dynamisch anhand der von Google AI Studio gelieferten Metadaten ab.
    """
    raw_id = rohdaten.get("name") or rohdaten.get("id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        return None

    # Normalisiere models/ Präfix von Google
    if raw_id.startswith("models/"):
        model_id = raw_id[len("models/"):]
    else:
        model_id = raw_id

    # Display Name für hübsche Darstellung im Panel
    disp_name = rohdaten.get("displayName") or rohdaten.get("display_name")
    name = disp_name.strip() if isinstance(disp_name, str) and disp_name.strip() else model_id

    model_lower = model_id.lower()

    # Kontext und Ausgabetokens dynamisch aus den Rohdaten
    # Berücksichtigt sowohl native Google REST-Felder (inputTokenLimit / outputTokenLimit)
    # als auch OpenAI-kompatible Felder
    kontext = (
        positive_zahl(rohdaten.get("inputTokenLimit"))
        or positive_zahl(rohdaten.get("input_token_limit"))
        or positive_zahl(rohdaten.get("context_length"))
    )
    max_ausgabe = (
        positive_zahl(rohdaten.get("outputTokenLimit"))
        or positive_zahl(rohdaten.get("output_token_limit"))
        or positive_zahl(rohdaten.get("max_completion_tokens"))
    )

    supported_methods = (
        rohdaten.get("supportedGenerationMethods")
        or rohdaten.get("supported_generation_methods")
        or []
    )
    if not isinstance(supported_methods, (list, tuple)):
        supported_methods = []

    # Prüfe dynamisches 'thinking'-Flag von Google AI Studio
    api_thinking = rohdaten.get("thinking")
    if api_thinking is True:
        ist_denkend = True
    elif api_thinking is False:
        ist_denkend = False
    else:
        # Fallback-Heuristik wenn das Flag nicht in den Rohdaten vorliegt
        if "embedding" in model_lower:
            ist_denkend = False
        elif "gemma" in model_lower:
            ist_denkend = True
        elif "gemini" in model_lower:
            ist_denkend = any(
                t in model_lower for t in ("2.5", "2.0-flash-thinking", "thinking")
            )
        else:
            ist_denkend = False

    # Denkstufen ermitteln
    raw_levels = (
        rohdaten.get("thinking_levels")
        or rohdaten.get("thinkingLevels")
        or rohdaten.get("efforts")
    )
    if isinstance(raw_levels, (list, tuple)) and raw_levels:
        stufen = tuple(str(s) for s in raw_levels if isinstance(s, (str, int)))
        standard_stufe = "medium" if "medium" in stufen else (stufen[0] if stufen else None)
    elif ist_denkend:
        stufen = ("low", "medium", "high")
        standard_stufe = "medium"
    else:
        stufen = ()
        standard_stufe = None

    # Einbettungsmodelle (text-embedding-004, gemini-embedding-001, etc.)
    if "embedding" in model_lower or ("embedContent" in supported_methods and "generateContent" not in supported_methods):
        return Modell(
            model_id=model_id,
            name=name,
            denkt=False,
            stufen=(),
            standard_stufe=None,
            zwingend=False,
            kontext_tokens=kontext or 2_048,
            max_ausgabe_tokens=max_ausgabe or 1,
            sieht=False,
        )

    # Gemma Open-Source-Modelle (Textmodelle, konfigurierbare Denkstufen)
    if "gemma" in model_lower:
        return Modell(
            model_id=model_id,
            name=name,
            denkt=ist_denkend,
            stufen=stufen,
            standard_stufe=standard_stufe,
            zwingend=False,
            kontext_tokens=kontext or 32_768,
            max_ausgabe_tokens=max_ausgabe or 8_192,
            sieht=False,
        )

    # Gemini-Modelle (Multimodal, bis 1M+ Kontext, dynamisches Thinking)
    if "gemini" in model_lower:
        return Modell(
            model_id=model_id,
            name=name,
            denkt=ist_denkend,
            stufen=stufen,
            standard_stufe=standard_stufe,
            zwingend=False,
            kontext_tokens=kontext or 1_048_576,
            max_ausgabe_tokens=max_ausgabe or 8_192,
            sieht=True,
        )

    # Generischer Rückfall für beliebige andere Google-Modelle (z.B. zukünftige Modelle)
    return Modell(
        model_id=model_id,
        name=name,
        denkt=ist_denkend,
        stufen=stufen,
        standard_stufe=standard_stufe,
        kontext_tokens=kontext or 32_768,
        max_ausgabe_tokens=max_ausgabe,
        sieht=None,
    )
