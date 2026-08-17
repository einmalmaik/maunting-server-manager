"""Die Stimme, von aussen gesehen — wer vorliest, steht nicht hier.

Dieses Modul ist der **Controller** vor den Sprachdiensten, das Gegenstück zu
`ai_stt` auf der anderen Seite des Gesprächs. Es liest nichts vor, kennt kein
Protokoll und keine Bibliothek; es beantwortet zwei Fragen:

* *Kann dieser Zugang hier überhaupt sprechen?* — `unmoeglich()` / `moeglich()`
* *Wer spricht für ihn?* — `stimmweg()`

**Warum es das gibt.** Vorher nannten drei Module `ai_tts_elevenlabs` beim
Namen: beide Sprachrouter und `ai_voice_bridge`, zusammen an acht Stellen. Den
Sprachdienst zu wechseln hiess damit, acht fremde Stellen anzufassen — und drei
davon waren Typangaben, deren Übersehen niemandem auffällt, weil Python sie
nicht prüft. Jetzt kennt ihn nur noch `_stimmen()` unten. Ein Wechsel ist eine
neue Datei und eine geänderte Zeile, ein Ausbau eine gelöschte Datei und eine
gelöschte Zeile.

**Der Import steht absichtlich in der Funktion und nicht am Dateikopf.** Nicht
wegen eines Importzyklus, sondern damit das Löschen einer Stimme tatsächlich
beim Löschen der Datei endet: ein Import am Kopf würde beim Start der Anwendung
scheitern, ein fehlender Eintrag hier trifft nur den, der sprechen will — und
der bekommt „kein Sprachmodus" statt eines toten Panels. Dieselbe Überlegung
wie in `ai_stt._wege`, und aus demselben Betriebsvorfall geboren.

**Die beiden Protokolle zählen auf, was heute wirklich verlangt wird**, und
keinen Namen mehr. `MAX_ZEICHEN_JE_ANTWORT` etwa steht bewusst **nicht** darin:
der Deckel gilt innerhalb des Sprachdienstes, kein Aufrufer liest ihn. Wer
einen zweiten Anbieter einbaut, liest hier, was er zu schreiben hat, statt
dafür erst die Router zu lesen — und schreibt nichts, das niemand aufruft.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol

from services.openai_compatible_adapter import AiProviderRequestError


class Stimmsitzung(Protocol):
    """Eine offene Sitzung: Zeichen hinein, Tonstücke hinaus.

    Ein asynchroner Kontextmanager, weil an ihr eine Verbindung hängt, die auch
    dann zugehen muss, wenn der Lauf mitten im Satz abbricht.
    """

    async def __aenter__(self) -> "Stimmsitzung": ...

    async def __aexit__(self, *ausnahme: object) -> None: ...

    async def sagen(self, text: str) -> None:
        """Diesen Text vorlesen. Darf puffern, bis ein Satz voll ist."""
        ...

    async def ausklingen(self) -> None:
        """Den Rest vorlesen und auf das letzte Tonstück warten."""
        ...

    async def schliessen(self) -> None:
        """Sofort abbrechen — für das Dazwischenreden.

        Nicht dasselbe wie `ausklingen`: hier soll gerade **nicht** zu Ende
        gesprochen werden. Wer dem Panel ins Wort fällt, will nicht warten, bis
        der angefangene Satz fertig ist.
        """
        ...


class Stimmweg(Protocol):
    """Was ein Sprachdienst können muss, damit MSM ihn benutzen kann.

    Erfüllt wird das von einem **Modul**, nicht von einer Klasse — es gibt je
    Anbieter genau einen Sprachdienst, und eine Klasse mit einer einzigen
    Instanz wäre Zeremonie. Module sind gültige Protokoll-Erfüller.
    """

    #: Ob dieser Dienst in dieser Installation läuft. Er darf ``False`` sagen,
    #: ohne dass etwas kaputt ist: eine weich importierte Bibliothek kann fehlen
    #: (siehe `ai_tts_elevenlabs`), und für den Benutzer ist das dasselbe wie
    #: ein fehlender Zugang — die Funktion gibt es dann nicht.
    STIMME_MOEGLICH: bool

    #: Warum nicht. Gilt nur, wenn `STIMME_MOEGLICH` ``False`` ist, und geht
    #: dem **Betreiber** ins Gesicht, nicht ins Protokoll. Der Grund steht am
    #: Sprachdienst und nicht im Router, weil nur er ihn kennt.
    UNMOEGLICH_GRUND: str

    #: Die Sitzungsklasse. Aufgerufen mit ``adresse``, ``schluessel`` und
    #: ``senden`` — der Rückgabeweg für fertige Tonstücke.
    Stimme: Callable[..., Stimmsitzung]

    def verbindungsadresse(
        self, base_url: str, stimme: str, modell: str | None
    ) -> str:
        """Die vollständige Adresse für **diese** Stimme an diesem Zugang."""
        ...

    def pruefen(self, adresse: str, schluessel: str | None) -> Awaitable[None]:
        """Handschlag auf, Handschlag zu. Wirft, wenn etwas nicht stimmt."""
        ...

    def probe_fehlercode(self, fehler: BaseException) -> str:
        """Übersetzt einen Fehler der Probe in einen MSM-Fehlercode."""
        ...


def _stimmen() -> dict[str, Stimmweg]:
    """Je Anbieter sein Sprachdienst. Die eine Verkabelungsstelle.

    Spät importiert, siehe Modulkopf.
    """
    from services import ai_tts_elevenlabs

    return {"elevenlabs": ai_tts_elevenlabs}


def stimmweg(kind: str) -> Stimmweg:
    """Wer für diesen Anbieter vorliest.

    Wirft `AiProviderRequestError`, wenn es für ihn keinen Sprachdienst gibt —
    weil das im Betrieb nur zwei Ursachen hat, und beide sind Fehler: ein
    Datenstand, der einen Anbieter als sprechend führt, den diese Version nicht
    kennt, oder eine gelöschte Datei bei stehengebliebenem Eintrag. Genau der
    Fall, den der späte Import offenhalten soll: es trifft den, der sprechen
    will, und nicht den Start der Anwendung.

    Wer **fragen** will, statt zu sprechen, nimmt `moeglich`.
    """
    weg = _stimmen().get(kind)
    if weg is None:
        raise AiProviderRequestError(
            "AI_PROVIDER_TTS_UNSUPPORTED",
            f"Fuer {kind!r} ist kein Sprachdienst eingebaut",
        )
    return weg


def unmoeglich(kind: str) -> str | None:
    """``None`` heisst: dieser Anbieter kann hier sprechen. Sonst der Grund.

    Zwei Ursachen in einer Antwort — es gibt keinen Dienst für ihn, **oder** er
    läuft hier nicht. Die Router sollen sie nicht auseinanderhalten müssen: für
    den Betreiber ist das Ergebnis dasselbe, und was er stattdessen braucht, ist
    der Satz, der zurückkommt.
    """
    weg = _stimmen().get(kind)
    if weg is None:
        return "Für diesen Anbieter ist kein Sprachdienst eingebaut."
    if not weg.STIMME_MOEGLICH:
        return weg.UNMOEGLICH_GRUND
    return None


def moeglich(kind: str) -> bool:
    """Kann dieser Anbieter in dieser Installation sprechen?"""
    return unmoeglich(kind) is None
