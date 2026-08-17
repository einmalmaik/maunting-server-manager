"""ElevenLabs — die Stimme. Sie antwortet nicht, sie liest vor.

Das ist der ganze Unterschied zum Realtime-Zugang, der bis zum 2026-08-16 an
dieser Stelle stand: der sprach selbst, mit eigenem Werkzeuglauf, eigener
Bestätigung und eigenem Gedächtnis neben dem des Chats. Zwei Modelle, die beide
dasselbe Panel bedienen durften, waren zweimal dieselbe Arbeit und zweimal
dieselbe Angriffsfläche. Hier schreibt das Chatmodell die Antwort, und diese
Datei sagt nur, wer sie ausspricht.

Sichtbar wird das am ``protokoll``: ``tts`` statt ``chat_completions``. Ein
solcher Zugang taucht in keiner Modellauswahl des Chats auf und kennt weder
Nachrichten noch Werkzeuge.

Der Betreiber wählt hier zweierlei: ``default_model`` ist das Sprachmodell,
``default_voice`` die Stimm-Kennung aus seinem Konto. Beides steht am Zugang und
nicht im Programm, weil beides eine Wahl ist und keine Tatsache — welche Stimme
zu einem Panel passt, weiß nur er.
"""

from __future__ import annotations

from services.ai_provider_registry.basis import Anbieter, Modell


ANBIETER = Anbieter(
    kind="elevenlabs",
    label="ElevenLabs (Stimme)",
    base_url="https://api.elevenlabs.io/v1",
    catalog_url="https://api.elevenlabs.io/v1/models",
    key_url="https://elevenlabs.io/app/settings/api-keys",
    # Ausdruecklich **keine** Praefixpruefung. Neue Schluessel beginnen mit
    # `sk_`, aeltere Konten tragen noch blanke Hex-Ketten ohne jedes Praefix.
    # Eine Pruefung wuerde hier also gueltige Schluessel abweisen — und der
    # Zweck des Feldes ist, dem Betreiber einen Umweg zu ersparen, nicht ihm
    # einen zu bauen. Der echte Testaufruf faengt den Rest.
    key_prefix=None,
    protokoll="tts",
    # ElevenLabs gibt seine Modellliste nur gegen einen Schluessel heraus …
    katalog_braucht_schluessel=True,
    # … und zwar in `xi-api-key`, nicht als Bearer-Token.
    schluessel_kopf="xi-api-key",
    schluessel_praefix="",
    # `GET /v1/models` antwortet mit einer nackten Liste. Kein `data`.
    katalog_liste_feld=None,
    # Am 2026-08-16 nachgesehen: rund 75 ms Rechenzeit, in Europa 100 bis 150 ms
    # bis zum ersten Ton — das einzige Modell, das sich wie ein Gespraech
    # anfuehlt. Die hoeherwertigen klingen besser und kosten genau das, was ein
    # Gespraech nicht hat. Es bleibt eine Empfehlung: fuehrt der Katalog die
    # Kennung nicht mehr, zeigt die Oberflaeche keine an, nie eine erfundene.
    empfehlung="eleven_flash_v2_5",
    # Kein Wortschatz fuer Chatanfragen — dieser Zugang bekommt nie eine.
)


def katalog_lesen(rohdaten: dict) -> Modell | None:
    """Liest ein Sprachmodell von ElevenLabs — und weiss dabei fast nichts.

    Das ist der Unterschied zu `openrouter.katalog_lesen` und keine
    Nachlässigkeit: ein Sprachmodell hat kein Kontextfenster, keine Denkstufen
    und keinen Tokenpreis. Es hat einen Namen, eine Kennung und eine Antwort auf
    die Frage, ob es überhaupt vorlesen kann. Alles Weitere, was `Modell` bieten
    würde, wäre hier eine erfundene Null.

    ``kontext_tokens=None`` heisst überall im Code „unbekannt" und nie „klein"
    (`ai_context_window.ermitteln`) — hier heisst es zusätzlich „gibt es nicht".
    Beides führt zum selben Verhalten, und das ist der Grund, warum kein drittes
    Feld nötig ist.

    ``can_do_text_to_speech`` ist die eigentliche Arbeit dieser Funktion. Der
    Katalog führt auch Modelle zur Stimmumwandlung; eines davon in der Auswahl
    für den Sprachmodus wäre ein Eintrag, der beim ersten Satz scheitert. Fehlt
    das Feld, wird der Eintrag **nicht** übernommen: ein unbekanntes Modell in
    einer Auswahl ist ein Versprechen, das MSM nicht halten kann.
    """
    model_id = rohdaten.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        return None
    if rohdaten.get("can_do_text_to_speech") is not True:
        return None
    name = rohdaten.get("name")
    return Modell(
        model_id=model_id,
        name=name if isinstance(name, str) and name else model_id,
        denkt=False,
    )
