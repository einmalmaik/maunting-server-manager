"""Die Sprachwege: welche Echtzeit-Schnittstelle ein Sprachmodell spricht.

Ein Anbieter kann mehr als einen Weg tragen. OpenAI führt neben der
Realtime-API (``/v1/realtime/calls``) seit GPT-Live einen zweiten,
grundverschiedenen Endpunkt (``POST /v1/live/sessions``): anderes Protokoll,
andere Stimmen, eine Denkstufe, die nicht dem Sprachmodell gehört, sondern
seinem Backend, und eine Abrechnung nach Sekunden statt nach Token.

**Welcher Weg gilt, entscheidet das Modell, nicht der Anbieter.**
``gpt-realtime-2`` spricht Realtime, ``gpt-live-1`` spricht Live — beide stehen
am selben Zugang im selben Feld ``realtime_model``, so wie ein Chatzugang ein
Modell hat und nicht einen Schalter je Endpunkt. Laut OpenAIs Modellseiten
führt jedes der beiden genau seinen Endpunkt und keinen anderen
(``gpt-live-1``: nur ``v1/live/sessions``; ``gpt-live-transcribe``: dort
ausdrücklich *nicht*, sondern nur ``v1/realtime/transcription_sessions``).

Die Registry (`ai_provider_registry.basis.Anbieter.sprachwege`) nennt je
Anbieter nur die **Namen**. Was ein Weg verlangt, steht hier, an einer Stelle
für die Prüfung beim Speichern, die Wahl der Sitzung im Router und die
Oberfläche — die bekommt dieselben Felder über ``/settings/provider-kinds``
und urteilt mit derselben Regel (`Sprachweg.passt`), statt eine zweite Liste
zu führen, die beim nächsten Modell veraltet.

Das Modul hat keine Abhängigkeiten: die Registry, der Provider-Dienst und der
Router lesen es, und keiner von ihnen darf dabei einen Importzyklus bekommen.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Sprachweg:
    """Was ein Sprachweg verlangt und anbietet.

    ``merkmal`` und ``ausschluesse`` sind die **ganze** Erkennungsregel, und sie
    ist absichtlich schlicht: ein Teilwort in der Modellkennung. Sie wandert so,
    wie sie hier steht, an die Oberfläche — eine klügere Regel hier und eine
    einfachere dort wären zwei Antworten auf dieselbe Frage.
    """

    name: str
    #: Für Fehlermeldungen an den Betreiber — als Wortteil geschrieben, damit
    #: „Gemini-Live-Stimme“ und „OpenAI-Realtime-Modell“ daraus werden.
    label: str
    merkmal: str
    ausschluesse: tuple[str, ...] = ()
    #: In der Reihenfolge, in der die Oberfläche sie anbietet.
    stimmen: tuple[str, ...] = ()
    empfohlene_stimmen: tuple[str, ...] = ()
    empfohlene_modelle: tuple[str, ...] = ()
    #: Die Wörter, die dieser Endpunkt als Denkstufe annimmt. Leer: keine.
    denkstufen: tuple[str, ...] = ()
    #: Nur Modelle mit diesem Teilwort nehmen eine Denkstufe an (die
    #: Realtime-2-Reihe). ``None``: jedes Modell des Weges.
    denkstufen_merkmal: str | None = None
    #: Die Denkstufe gilt nicht dem Sprachmodell, sondern seinem Backend-Modell
    #: (GPT-Live: ``delegation.responses.reasoning.effort``). Welche Stufen
    #: davon greifen, sagt dann der Katalog **dieses** Modells.
    denkt_im_backend: bool = False
    #: Ob die Pausenerkennung (``semantic_vad``) einstellbar ist.
    vad: bool = False
    #: Ob der Verbrauch in Audio-Token abgerechnet wird.
    audiopreise: bool = False
    #: Ob die Sitzung nach Dauer abgerechnet wird.
    minutenpreis: bool = False
    #: Ob ein Backend-Modell die Arbeit macht (Werkzeuge, Nachdenken).
    backend_modell: bool = False

    def passt(self, modell: str | None) -> bool:
        kennung = (modell or "").strip().lower()
        return bool(kennung) and self.merkmal in kennung and not any(
            teil in kennung for teil in self.ausschluesse
        )

    def stimme(self, wert: str | None) -> str | None:
        """Die Stimme in ihrer kanonischen Schreibweise, oder ``None``.

        Nachsichtig gelesen, streng gespeichert: ``Marin`` wird ``marin`` und
        ``puck`` wird ``Puck`` — gespeichert wird immer die Schreibweise, die
        der Anbieter erwartet.
        """
        gesucht = (wert or "").strip().lower()
        for kandidat in self.stimmen:
            if kandidat.lower() == gesucht:
                return kandidat
        return None

    def nimmt_denkstufe(self, modell: str | None) -> bool:
        if not self.denkstufen:
            return False
        if self.denkstufen_merkmal is None:
            return True
        return self.denkstufen_merkmal in (modell or "").strip().lower()

    def als_dict(self) -> dict:
        """Die Felder, die die Oberfläche braucht — ohne ``label``."""
        return {
            "weg": self.name,
            "merkmal": self.merkmal,
            "ausschluesse": list(self.ausschluesse),
            "stimmen": list(self.stimmen),
            "empfohlene_stimmen": list(self.empfohlene_stimmen),
            "empfohlene_modelle": list(self.empfohlene_modelle),
            "denkstufen": list(self.denkstufen),
            "denkstufen_merkmal": self.denkstufen_merkmal,
            "denkt_im_backend": self.denkt_im_backend,
            "vad": self.vad,
            "audiopreise": self.audiopreise,
            "minutenpreis": self.minutenpreis,
            "backend_modell": self.backend_modell,
        }


OPENAI_REALTIME = Sprachweg(
    name="openai_realtime",
    label="OpenAI-Realtime",
    merkmal="realtime",
    stimmen=("marin", "cedar", "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse"),
    empfohlene_stimmen=("marin", "cedar"),
    empfohlene_modelle=("gpt-realtime-1.5", "gpt-realtime-2"),
    denkstufen=("low", "medium", "high"),
    denkstufen_merkmal="realtime-2",
    vad=True,
    audiopreise=True,
)

#: GPT-Live (`gpt-live-1`). Jede Angabe hier ist aus OpenAIs Referenz
#: ``/api/reference/resources/live/primary-websocket`` (Stand 22.09.2026):
#:
#: * ``stimmen`` — die 22 eingebauten Namen aus ``audio.output.voice``,
#:   ``marin`` ist dort die Vorgabe.
#: * ``denkstufen`` — ``delegation.responses.reasoning.effort``: ``none``,
#:   ``minimal``, ``low``, ``medium``, ``high``, ``xhigh``. Kein ``max``, auch
#:   wenn das Backend-Modell eines führt. „Supported values depend on the
#:   backend model" — deshalb ``denkt_im_backend``.
#: * ``ausschluesse`` — ``gpt-live-transcribe`` teilt den Namen, führt aber
#:   ``v1/live/sessions`` laut seiner Modellseite ausdrücklich nicht.
#:   ``translate`` aus demselben Grund vorsorglich: für keine Übersetzungs-
#:   variante ist dieser Endpunkt dokumentiert.
#: * keine Pausenerkennung — GPT-Live hört und spricht gleichzeitig und
#:   entscheidet selbst, wann es redet; eine ``turn_detection`` gibt es nicht.
#: * abgerechnet nach Sekunden (Modellseite: „billed per second"), das
#:   Backend-Modell getrennt nach seinen Token.
OPENAI_LIVE = Sprachweg(
    name="openai_live",
    label="GPT-Live",
    merkmal="gpt-live",
    ausschluesse=("transcribe", "translate"),
    stimmen=(
        "marin", "alloy", "ash", "ballad", "beacon", "bossa", "cedar", "cinder",
        "coral", "delta", "echo", "gleam", "meridian", "quartz", "ripple", "sage",
        "shimmer", "stone", "tempo", "verse", "vesper", "willow",
    ),
    empfohlene_stimmen=("marin",),
    empfohlene_modelle=("gpt-live-1",),
    denkstufen=("none", "minimal", "low", "medium", "high", "xhigh"),
    denkt_im_backend=True,
    minutenpreis=True,
    backend_modell=True,
)

GEMINI_LIVE = Sprachweg(
    name="gemini_live",
    label="Gemini-Live",
    merkmal="gemini",
    stimmen=("Puck", "Charon", "Kore", "Fenrir", "Aoede", "Zephyr", "Leda", "Orus"),
    empfohlene_stimmen=("Puck", "Charon"),
    empfohlene_modelle=(
        "gemini-2.0-flash",
        "gemini-2.0-flash-exp",
        "gemini-2.5-flash",
        "gemini-3.8-live",
        "gemini-3.8-live-extended-thinking",
    ),
    denkstufen=("low", "medium", "high"),
    audiopreise=True,
)

WEGE: dict[str, Sprachweg] = {
    weg.name: weg for weg in (OPENAI_REALTIME, OPENAI_LIVE, GEMINI_LIVE)
}

#: Die Antwortsprachen, die jeder Weg über seine Anweisungen umsetzt.
SPRACHEN = frozenset({"auto", "de", "en"})
#: Die Stufen der Pausenerkennung — nur, wo `Sprachweg.vad` gilt.
VAD_STUFEN = frozenset({"auto", "low", "medium", "high"})


def weg(name: str) -> Sprachweg:
    return WEGE[name]


def sprachweg_fuer(namen: tuple[str, ...], modell: str | None) -> Sprachweg | None:
    """Der erste Weg aus ``namen``, dessen Regel auf das Modell passt.

    ``namen`` ist `Anbieter.sprachwege` — ein Anbieter kann nur über die Wege
    sprechen, die er führt, auch wenn ein anderer Weg das Modell erkennen
    würde (``gpt-realtime`` an einem OpenRouter-Zugang ist kein Sprachweg).
    """
    for name in namen:
        kandidat = WEGE.get(name)
        if kandidat is not None and kandidat.passt(modell):
            return kandidat
    return None
