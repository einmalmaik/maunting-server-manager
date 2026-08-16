"""Die Werkzeuge im Sprachmodus — dieselben, nur anders verpackt.

**Kein zweiter Werkzeugsatz.** Das war die Vorgabe, und sie ist auch technisch
die richtige: 52 Werkzeuge ein zweites Mal zu pflegen hiesse, sie ein zweites
Mal falsch zu pflegen. Was hier passiert, ist eine Umformung der *Hülle* — die
Schemata selbst wandern unverändert.

    Chat:     {"type": "function", "function": {"name", "description", "parameters"}}
    Realtime: {"type": "function",              "name", "description", "parameters"}

**Der eigentliche Unterschied liegt woanders, und er ist wichtiger als die
Hülle.** Im Chat führt `ai_stream_service.segment_ausfuehren` den Zug: es zählt
Runden, erkennt Schleifen, deckelt Ergebnisse und weiss, wann Schluss ist. In
einer Realtime-Sitzung führt das **Modell** den Zug. Alles, was im Chat an
dieser Schleife hängt, hängt hier an nichts — es sei denn, es steht hier.

Was von selbst mitkommt, weil das Panel in der Mitte sitzt und nicht der
Browser: die Rechteprüfung in den Handlern, `_resolve_server` und der
Schwärzungs-Choke-Point. Was **nicht** mitkommt und deshalb hier steht: die
Schleifenschranken. Bei 64 USD je Million Ausgabetokens ist ein festgefahrenes
Modell nicht nur laut.

Nicht mitgekommen und bewusst nicht ersetzt sind das Rundenbudget und der
Rückflussdeckel. Beide rechnen gegen einen Prompt, der in jeder Runde neu
gebaut wird; eine Sitzung baut ihn einmal.

**Drei Werkzeuge gibt es trotzdem nur hier**, und alle drei ersetzen etwas,
das im Sprachmodus fehlt und sonst da wäre: `bestaetige_vorschlag` den Klick
auf die Karte, `set_ai_autonomy` den Schalter in den Einstellungen,
`zeige_beleg` den Blick auf den Bildschirm. Sie stehen absichtlich nicht in
`ai_tool_registry` — der getippte Chat kann sie damit nicht sehen, und zwar
nicht, weil dort ein Filter greift, sondern weil es sie dort nicht gibt.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from models import User
from services import ai_tool_registry, permission_service
from services.ai_action_service import angebotene_werkzeuge, provider_tool_definitions

logger = logging.getLogger(__name__)


#: Wie oft derselbe Aufruf mit denselben Argumenten hintereinander laufen darf.
#:
#: Angelehnt an `ai_stream_service.MAX_GLEICHE_AUFRUFE` und bewusst nicht
#: importiert: dort ist es eine Grenze je Lauf, hier je Sitzung. Eine andere
#: Bezugsgrösse — ein Import täuschte eine Kopplung vor, die es nicht gibt.
#:
#: Dass die Zahl hier kleiner ist als dort, ist deshalb kein Nachziehbedarf,
#: sondern derselbe Gedanke unter anderen Bedingungen: im Chat darf ein Modell
#: einem Hochfahren zusehen und mehrfach nachfragen, hier hört jemand zu, und
#: dieselbe Auskunft ein viertes Mal vorgelesen zu bekommen ist keine
#: Gründlichkeit.
MAX_GLEICHE_AUFRUFE = 3

#: Wieviele Werkzeugaufrufe eine Sprachsitzung insgesamt tun darf.
#:
#: Im Chat begrenzt `ai_stream_service.MAX_TOOL_CALLS` **eine Runde**, und ein
#: Lauf darf viele Runden haben; eine Unterhaltung darf ausserdem beliebig viele
#: Züge haben, weil zwischen ihnen ein Mensch tippt. Hier gilt die Zahl für die
#: ganze Sitzung, und das ist deutlich strenger — mit Absicht. Zwischen zwei
#: Zügen liegt hier niemand, der etwas tippt, sondern eine Sprechpause. Wer nach
#: 32 Werkzeugaufrufen noch nicht fertig ist, ist in einer Schleife und nicht in
#: einem Gespräch.
MAX_AUFRUFE_JE_SITZUNG = 32

#: Wie lang ein Werkzeugergebnis höchstens sein darf, bevor es gekürzt wird.
#:
#: Deutlich kleiner als im Chat, und der Grund ist die Ausgabe: was hier
#: zurückkommt, wird **vorgelesen**. Ein Modell, das 16.000 Zeichen
#: Log-Ausschnitt bekommt, versucht sie zusammenzufassen — und je mehr es
#: zusammenfassen muss, desto mehr erfindet es dabei. Vier Kilozeichen sind
#: rund eine Minute gesprochener Text.
MAX_ERGEBNIS_ZEICHEN = 4_000

#: Wieviele Zeilen `zeige_beleg` auf einmal auf den Schirm bringen darf.
#:
#: Der Zweck des Werkzeugs ist die **erklärte Stelle** und nicht das Log. Wer
#: zwanzig Zeilen zeigt, zeigt schon mehr, als er in einem Atemzug erklären
#: kann; darüber wäre es ein Dump mit gesprochener Untermalung.
MAX_BELEG_ZEILEN = 20

#: Wie lang eine einzelne gezeigte Zeile sein darf.
MAX_BELEG_ZEILENLAENGE = 300

#: Wieviel Werkzeugergebnis die Echtheitsschranke mitführt.
#:
#: Der Puffer ist das Gedächtnis von `zeige_beleg`: gezeigt werden darf nur,
#: was in dieser Sitzung wirklich zurückkam. Er wächst nicht mit, sondern
#: rollt — die ältesten Ergebnisse fallen zuerst heraus. 64 KiB sind rund
#: sechzehn volle Werkzeugergebnisse und damit deutlich mehr, als ein Mensch
#: in einem Gespräch im Kopf behält.
MAX_BELEGPUFFER_ZEICHEN = 64 * 1024


def fuer_realtime(definition: dict) -> dict | None:
    """Eine Chat-Werkzeugdefinition in die Realtime-Hülle umformen.

    Gibt ``None`` zurück, wenn die Definition keine brauchbare ist. Ein
    einzelner kaputter Eintrag darf den ganzen Katalog nicht verwerfen — dieselbe
    Nachsicht wie beim Modellkatalog, und aus demselben Grund: ein leerer
    Katalog ist schlimmer als ein unvollständiger.
    """
    inneres = definition.get("function")
    if not isinstance(inneres, dict):
        return None
    name = inneres.get("name")
    if not isinstance(name, str) or not name:
        return None
    flach: dict[str, Any] = {"type": "function", "name": name}
    if isinstance(inneres.get("description"), str):
        flach["description"] = inneres["description"]
    if isinstance(inneres.get("parameters"), dict):
        flach["parameters"] = inneres["parameters"]
    return flach


def katalog(db: Session, user: User) -> list[dict]:
    """Der Werkzeugkatalog dieser Sprachsitzung.

    Zwei Schnitte, und die Reihenfolge ist egal, weil beide dasselbe tun —
    wegnehmen:

    * ``angebotene_werkzeuge`` nimmt weg, wofür diesem Benutzer das Recht fehlt.
      Das ist eine Korrektur und keine Schranke: seine KI könnte diese Werkzeuge
      ohnehin nicht ausführen, sie hätte es nur erst versucht.
    * ``SPRACHE_LESEN`` nimmt weg, was in einem Gespräch nicht funktioniert —
      `ask_user` etwa stellt eine Karte hin, die niemand hört.

    **Der Katalog ist keine Schranke**, hier so wenig wie im Chat. Ein Modell,
    das sich einen Namen ausdenkt, prallt weiterhin an `_resolve_server`, am
    Recht im Handler und an `_require_tool_permission` ab.
    """
    angeboten = angebotene_werkzeuge(db, user)
    # Ob dieser Benutzer ueberhaupt handeln darf, beantwortet die
    # Schnittmenge selbst: enthaelt sie kein einziges Schreibwerkzeug aus
    # `SPRACHE_HANDELN`, fehlt ihm das Recht dazu, und dann braucht er auch
    # das Bestaetigungswerkzeug nicht.
    darf_handeln = bool(angeboten & ai_tool_registry.SPRACHE_HANDELN)
    erlaubt = angeboten & ai_tool_registry.sprache_tools(darf_handeln=darf_handeln)

    werkzeuge = []
    for definition in provider_tool_definitions():
        flach = fuer_realtime(definition)
        if flach is not None and flach["name"] in erlaubt:
            werkzeuge.append(flach)
    if darf_handeln:
        # Das Bestaetigungswerkzeug kommt aus dieser Datei und nicht aus
        # `provider_tool_definitions`. Genau dadurch kann der getippte Chat es
        # nicht sehen — dort gibt es die Karte, und eine zweite, schwaechere
        # Art zu bestaetigen daneben waere ein Weg an ihr vorbei.
        werkzeuge.append(BESTAETIGEN_WERKZEUG)
        # Der Autonomieschalter haengt am Handeln und nicht am Lesen: wer keine
        # Schreibwerkzeuge angeboten bekommt, haette nichts, was ohne Rueckfrage
        # laufen koennte — der Schalter waere einer, der nirgends ankommt.
        # Deshalb steht die Rechtefrage in diesem Zweig und nicht davor; sie ist
        # eine Datenbankabfrage, und der reine Lesefall braucht sie nicht.
        if permission_service.has_global_permission(db, user, "ai.autonomous.use"):
            werkzeuge.append(AUTONOMIE_WERKZEUG)
    if angeboten & ai_tool_registry.SPRACHE_LESEN:
        # `zeige_beleg` kann nur zeigen, was ein Lesewerkzeug zurueckgebracht
        # hat — die Echtheitsschranke laesst nichts anderes durch. Ohne ein
        # einziges Lesewerkzeug waere es ein Knopf, der nie angeht, und ein
        # Modell, das ihn trotzdem versucht, haette eine Runde verbraucht.
        werkzeuge.append(BELEG_WERKZEUG)
    return werkzeuge


def signatur(name: str, argumente: dict) -> str:
    """Derselbe Aufruf mit denselben Argumenten — für die Schleifenerkennung."""
    return name + "|" + json.dumps(argumente, ensure_ascii=True, sort_keys=True)


# ── Die gesprochene Bestätigung ───────────────────────────────────────────

#: Das Werkzeug, mit dem ein Vorschlag per Stimme bestätigt wird.
#:
#: Es steht **nicht** in `ai_tool_registry` und nicht in
#: `provider_tool_definitions`, und das ist die eigentliche Zusage: der getippte
#: Chat kann es gar nicht sehen. Dort gibt es die Karte, und eine zweite,
#: schwächere Art zu bestätigen daneben wäre kein Komfort, sondern ein Weg an
#: der Karte vorbei. Was hier existiert, existiert nur hier.
BESTAETIGEN = "bestaetige_vorschlag"

BESTAETIGEN_WERKZEUG = {
    "type": "function",
    "name": BESTAETIGEN,
    "description": (
        "Führt einen Vorschlag aus, den du vorher angelegt und dem Menschen "
        "**wörtlich vorgelesen** hast. Rufe das erst auf, wenn er deutlich "
        "zugestimmt hat — ein Zögern, eine Rückfrage oder ein unklares Brummen "
        "ist keine Zustimmung. Hat er abgelehnt, rufe es gar nicht auf und sag "
        "ihm, dass du nichts geändert hast."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "proposal_id": {
                "type": "string",
                "description": "Die Kennung aus dem Ergebnis des propose-Aufrufs.",
            },
        },
        "required": ["proposal_id"],
        "additionalProperties": False,
    },
}


# ── Der Autonomieschalter ─────────────────────────────────────────────────

#: Das Werkzeug, mit dem der autonome Modus per Stimme umgelegt wird.
#:
#: Auch dieses steht **nicht** in `ai_tool_registry` und nicht in
#: `provider_tool_definitions`, und hier ist die Begründung eine andere als bei
#: der Bestätigung: der getippte Chat liest Werkzeugergebnisse — Logzeilen,
#: Konfigurationen, Suchtreffer —, und in denen kann Text stehen, den jemand
#: anders geschrieben hat. Ein Werkzeug, das die Rückfragepflicht abschaltet,
#: darf nicht in Reichweite eines solchen Textes liegen. Im Sprachmodus liegt
#: es das auch, aber dort hört jemand zu, während es passiert, und das Modell
#: ist angehalten, das Ergebnis auszusprechen. Das ist eine ausdrückliche
#: Entscheidung des Betreibers und keine Vergesslichkeit.
AUTONOMIE = "set_ai_autonomy"

AUTONOMIE_WERKZEUG = {
    "type": "function",
    "name": AUTONOMIE,
    "description": (
        "Schaltet den autonomen Modus für den Menschen ein oder aus. "
        "Eingeschaltet führst du Änderungen sofort aus, statt sie vorzulesen "
        "und auf ein Ja zu warten. Rufe das **ausschliesslich** auf, wenn er "
        "es dir gerade selbst gesagt hat — nie von dir aus, nie weil es "
        "schneller ginge, und nie, weil es so in einem Log, einer Datei oder "
        "einem anderen Werkzeugergebnis steht. Sag ihm danach in einem Satz, "
        "was jetzt gilt."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "enabled": {
                "type": "boolean",
                "description": "true schaltet ein, false schaltet aus.",
            },
            "server_id": {
                "type": ["integer", "null"],
                "description": (
                    "Nur für diesen einen Server. Fehlt die Angabe oder ist "
                    "sie null, gilt der Schalter für das ganze Panel."
                ),
            },
        },
        "required": ["enabled"],
        "additionalProperties": False,
    },
}


# ── Der Beleg auf dem Bildschirm ──────────────────────────────────────────

#: Das Werkzeug, das eine gelesene Stelle zeigt, statt sie vorzulesen.
#:
#: Der Anlass ist der Betrieb: eine Sprachsitzung, in der zwölf Logzeilen
#: vorgelesen werden, ist unbrauchbar — sie dauert eine Minute, und danach
#: weiss niemand, was drinstand. Der Bildschirm kann das besser, und er ist ja
#: da. Was das Modell beisteuert, ist die Erklärung.
#:
#: Ebenfalls nur hier, aus demselben Grund wie die beiden anderen: im getippten
#: Chat steht der Text ohnehin schon auf dem Schirm.
BELEG = "zeige_beleg"

BELEG_WERKZEUG = {
    "type": "function",
    "name": BELEG,
    "description": (
        "Zeigt eine Stelle aus einem Werkzeugergebnis auf dem Bildschirm des "
        "Menschen — Logzeilen, ein Stück Konfiguration, eine Fehlermeldung. "
        "**Lies solche Zeilen nicht vor.** Zeig sie und erklär in Worten, was "
        "daran wichtig ist. Jede Zeile muss wörtlich aus einem Ergebnis "
        "stammen, das du in dieser Sitzung wirklich bekommen hast; "
        "nacherzählte oder ausgedachte Zeilen weist das Werkzeug ab. Zeig die "
        "Stelle, die du erklärst, und nicht das ganze Log."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "quelle": {
                "type": "string",
                "description": (
                    "Woher die Zeilen stammen, kurz — etwa „Log von srv-1“ "
                    "oder „server.properties“."
                ),
            },
            "zeilen": {
                "type": "array",
                "items": {"type": "string", "maxLength": MAX_BELEG_ZEILENLAENGE},
                "maxItems": MAX_BELEG_ZEILEN,
                "description": (
                    "Die Zeilen, wörtlich aus dem Werkzeugergebnis kopiert."
                ),
            },
        },
        "required": ["quelle", "zeilen"],
        "additionalProperties": False,
    },
}


#: Wie ein Vorschlag klingt, wenn man ihn vorliest.
#:
#: **Der Satz kommt von MSM und nicht vom Modell.** Das ist der ganze Punkt der
#: Rücklesung: würde das Modell formulieren, was es gleich tut, wäre die
#: Bestätigung eine Zustimmung zu seiner Erzählung und nicht zu seiner Handlung.
#: Hier steht, was wirklich passiert.
#:
#: Sieben Einträge, einer je Werkzeug aus `SPRACHE_HANDELN`. Eine Tabelle und
#: kein Textgenerator: was ein Werkzeug tut, ist eine feste Aussage, und sie
#: gehört ausgeschrieben dorthin, wo man sie liest.
_VORLESEN = {
    "propose_server_lifecycle": "{server} {vorgang}",
    "propose_backup": "Ein Backup von {server} anlegen",
    "propose_config_update": "Die Datei {pfad} auf {server} vollständig ersetzen",
    "propose_config_patch": "{stellen} in der Datei {pfad} auf {server} ändern",
    "propose_mod_install": "Eine Mod auf {server} installieren",
    "propose_bind_ip_update": "Die Bind-IP von {server} auf {ip} ändern",
    "propose_server_repair": "Eine Reparatur an {server} ausführen",
}

_VORGANG = {
    "start": "starten",
    "stop": "stoppen",
    "restart": "neu starten",
}


def vorlesetext(tool_name: str, vorschau: dict, servername: str | None) -> str:
    """Der Satz, den das Modell wörtlich vorlesen soll.

    Fehlt ein Baustein, steht dort ein ehrliches Platzhalterwort statt einer
    Lücke. Ein Satz mit „diesem Server" ist unschön; ein Satz mit einem leeren
    Namen wäre irreführend.
    """
    server = servername or "diesem Server"
    vorlage = _VORLESEN.get(tool_name)
    if vorlage is None:
        # Ein Schreibwerkzeug ohne Zeile in `_VORLESEN` gibt es nicht — die
        # Tabelle deckt `SPRACHE_HANDELN` vollständig ab, und ein Test hält das
        # fest. Diese Zeile ist trotzdem da, weil der Rückfall auf „irgendetwas
        # ändern" besser ist als ein Absturz mitten im Gespräch.
        return f"Eine Änderung an {server} ausführen"

    anzahl = vorschau.get("edits")
    return vorlage.format(
        server=server,
        vorgang=_VORGANG.get(str(vorschau.get("operation") or ""), "ändern"),
        pfad=vorschau.get("path") or "einer Datei",
        ip=vorschau.get("bind_ip") or "einer anderen Adresse",
        stellen=(
            f"{anzahl} Stellen" if isinstance(anzahl, int) and anzahl != 1 else "Eine Stelle"
        ),
    )


def _gekuerzt(wert: object) -> object:
    """Ein Ergebnis auf Vorlesbarkeit kürzen.

    Gekürzt wird der **serialisierte** Text und nicht die Struktur: was das
    Modell sieht, ist ohnehin JSON, und eine Struktur zu beschneiden hiesse zu
    entscheiden, welches Feld unwichtig ist. Das weiss hier niemand.
    """
    roh = json.dumps(wert, ensure_ascii=False)
    if len(roh) <= MAX_ERGEBNIS_ZEICHEN:
        return wert
    return {
        "gekuerzt": True,
        "hinweis": (
            "Das Ergebnis war zu lang zum Vorlesen und wurde gekuerzt. Frag "
            "gezielter nach, wenn du mehr brauchst."
        ),
        "anfang": roh[:MAX_ERGEBNIS_ZEICHEN],
    }


@dataclass
class Bruecke:
    """Die Werkzeugschleife einer Sprachsitzung.

    Ein Objekt und keine losen Funktionen, weil es etwas zu zählen gibt: die
    Schranken gelten je Sitzung, und eine Sitzung ist genau die Lebensdauer
    dieses Objekts.
    """

    user_id: int
    #: Wie oft jede Aufrufsignatur schon lief.
    _gesehen: dict[str, int] = field(default_factory=dict)
    #: Wieviele Aufrufe insgesamt liefen.
    _gesamt: int = 0
    #: Der Vorschlag, der gerade auf ein gesprochenes Ja wartet — oder keiner.
    #:
    #: **Höchstens einer.** Das ist die wichtigste Schranke der gesprochenen
    #: Bestätigung, und sie kostet fast nichts: solange einer offen ist, wird
    #: kein zweiter angelegt. Damit gibt es die ganze Klasse „das Ja landete auf
    #: dem falschen Vorschlag" nicht — weder durch ein Missverständnis des
    #: Modells noch dadurch, dass jemand zwei Sätze hintereinander sagt.
    offener_vorschlag: str | None = None
    #: Die Stelle, die `zeige_beleg` auf den Schirm bringen will — oder keine.
    #:
    #: Ein Feld und kein Senden, weil `ausfuehren` in einem Thread läuft: von
    #: dort aus in die Ereignisschleife des Browsers zu schreiben, ginge eine
    #: Weile gut und wäre trotzdem falsch. `_aufruf_beantworten` leert das Feld
    #: und schickt es, und zwar von der Schleife aus, auf der es hingehört.
    offener_beleg: dict | None = None
    #: Was Werkzeuge in dieser Sitzung wirklich zurückgegeben haben.
    #:
    #: Der Kern der Echtheitsschranke von `zeige_beleg`. Gespeichert wird der
    #: **serialisierte** Text — also genau das, was das Modell gesehen hat, und
    #: nicht mehr: ein gekürztes Ergebnis ist auch hier gekürzt, sonst könnte
    #: das Modell einen Teil zeigen, den es selbst nie gelesen hat.
    _ergebnisse: list[str] = field(default_factory=list)
    #: Wie gross der Puffer gerade ist, damit er nicht gezählt werden muss.
    _ergebnisse_zeichen: int = 0
    #: Welches Sitzungselement zu welchem Werkzeugnamen gehört.
    #:
    #: Nötig, weil die Gegenstelle den Namen **einmal** nennt, wenn sie das
    #: Element anlegt (``response.output_item.added``), und danach nur noch die
    #: Argumente nachschiebt. Wer sich den Namen dort nicht merkt, bekommt am
    #: Ende Argumente ohne Werkzeug.
    _namen: dict[str, str] = field(default_factory=dict)
    #: Ob die Gegenstelle gerade eine Antwort erzeugt.
    #:
    #: Das ist die Buchführung hinter `_antwort_anfordern`, und sie behebt den
    #: auffälligsten Fehler des Sprachmodus: die KI sagte „schaue ich kurz
    #: nach" — und danach kam nichts mehr, bis der Mensch von sich aus
    #: nachfragte.
    #:
    #: Der Grund liegt in der Reihenfolge der Ereignisse.
    #: ``response.function_call_arguments.done`` kommt **während** die Antwort
    #: noch läuft, nicht danach: erst hinterher folgen
    #: ``response.output_item.done`` und ``response.done``. Wer dort sofort ein
    #: ``response.create`` schickt, schickt es in eine offene Antwort hinein,
    #: und die Gegenstelle weist es mit ``conversation_already_has_active_
    #: response`` ab. Danach bittet niemand mehr um eine Antwort — das Ergebnis
    #: liegt im Verlauf, und es ist still.
    #:
    #: Das Rennen entscheidet sich daran, ob das Werkzeug schneller fertig ist
    #: als der gesprochene Satz davor. Ein Werkzeug, das eine Sekunde braucht,
    #: gewinnt gegen „schaue ich kurz nach"; eines, das sofort einen Fehler
    #: zurückgibt, gewinnt fast immer.
    _antwort_laeuft: bool = False
    #: Ob ein Werkzeugergebnis darauf wartet, dass daraus geredet wird.
    _antwort_faellig: bool = False

    def darf(self, name: str, argumente: dict) -> str | None:
        """Darf dieser Aufruf laufen? ``None`` heisst ja, sonst der Grund.

        Der Grund geht als Werkzeugergebnis an das Modell zurück und nicht als
        Abbruch. Das ist die etablierte Form im Chat (`deferred` in
        `_tool_followup_messages`): „nicht ausgeführt, aber beantwortet". Ein
        Abbruch mitten im Gespräch wäre für den Sprechenden ein Aussetzer ohne
        Erklärung.
        """
        if self._gesamt >= MAX_AUFRUFE_JE_SITZUNG:
            return (
                "In dieser Sprachsitzung sind genug Werkzeugaufrufe gelaufen. "
                "Sag, was du herausgefunden hast, und frag nach, wie es "
                "weitergehen soll."
            )
        if self._gesehen.get(signatur(name, argumente), 0) >= MAX_GLEICHE_AUFRUFE:
            return (
                f"`{name}` lief mit genau diesen Argumenten schon "
                f"{MAX_GLEICHE_AUFRUFE} Mal. Das Ergebnis wird sich nicht "
                "aendern — nimm das vorige und mach weiter."
            )
        return None

    def vermerken(self, name: str, argumente: dict) -> None:
        self._gesamt += 1
        schluessel = signatur(name, argumente)
        self._gesehen[schluessel] = self._gesehen.get(schluessel, 0) + 1

    def ausfuehren(self, name: str, argumente: dict) -> object:
        """Ein Werkzeug ausführen — über denselben Weg wie der Chat.

        `_werkzeug_ausfuehren` ist mit Unterstrich benannt und wird hier
        trotzdem importiert. Das ist Absicht und keine Nachlässigkeit: es **ist**
        der Schwärzungs-Choke-Point, und sein eigener Kommentar sagt „hier — und
        nur hier". Eine zweite Ausführung daneben wäre die zweite Stelle, an der
        jemand das Schwärzen vergisst. Der Import steht deshalb hier unten und
        nicht oben: er ist eine Aussage über diese eine Zeile, keine über das
        Modul.
        """
        from services.ai_stream_service import _werkzeug_ausfuehren
        from services.openai_compatible_adapter import ProviderToolCall

        grund = self.darf(name, argumente)
        if grund is not None:
            return {"error": grund}

        self.vermerken(name, argumente)

        if name == BESTAETIGEN:
            return self._bestaetigen(str(argumente.get("proposal_id") or ""))
        if name == AUTONOMIE:
            return self._autonomie_setzen(argumente)
        if name == BELEG:
            return self._beleg_zeigen(argumente)
        if name in ai_tool_registry.SPRACHE_HANDELN:
            return self._vorschlagen(name, argumente)
        aufruf = ProviderToolCall(id="voice", name=name, arguments=argumente)
        try:
            wert, _fehler = _werkzeug_ausfuehren(self.user_id, aufruf)
        except Exception as exc:
            # Ein Werkzeug, das unerwartet stirbt, beendet nicht das Gespraech.
            # Der Wortlaut bleibt im Protokoll — er kann Pfade des Zielservers
            # enthalten, und die haben im Kontext des Modells nichts verloren.
            logger.warning(
                "Sprachwerkzeug %s gescheitert user=%s error=%s",
                name, self.user_id, type(exc).__name__,
            )
            return {"error": "Das Werkzeug ist fehlgeschlagen."}
        # Genau hier füllt sich der Puffer der Echtheitsschranke, und nur hier:
        # was über diesen Zweig kommt, hat ein Werkzeug aus der Registry
        # wirklich zurückgegeben. Die Ergebnisse der drei sprachlokalen
        # Werkzeuge bleiben absichtlich draussen — sie sind unsere eigenen
        # Sätze, und ihre Fehlermeldungen tragen mitunter Text, den das Modell
        # selbst hineingereicht hat. Der dürfte sonst als „Beleg" auf den Schirm.
        #
        # Gemerkt wird der **ungekürzte** Wert und nicht das Ergebnis von
        # `_gekuerzt`. Der Unterschied ist keine Feinheit, sondern war ein
        # Loch: bei einem langen Ergebnis legt `_gekuerzt` den serialisierten
        # Text als Zeichenkette in ein Feld `anfang`, und eine zweite
        # Serialisierung escapt darin jedes Anführungszeichen ein zweites Mal.
        # `_woertlich` sucht die einfach escapte Form und fand ausgerechnet in
        # langen Logs nichts mehr — also dort, wo gezeigt statt vorgelesen
        # werden soll.
        self._ergebnis_merken(wert)
        return _gekuerzt(wert)

    # ── Die Echtheitsschranke des Belegs ──────────────────────────────────

    def _ergebnis_merken(self, wert: object) -> None:
        """Ein Werkzeugergebnis in den rollenden Puffer legen.

        Erwartet den **ungekürzten** Wert und serialisiert ihn genau einmal.
        Abgeschnitten wird danach auf `MAX_ERGEBNIS_ZEICHEN` — dieselbe Grenze,
        an der `_gekuerzt` schneidet, und damit steht im Puffer buchstäblich
        das, was das Modell zu sehen bekam. Weiter hinten kann es nichts
        zitieren, weil es dort nichts gelesen hat.
        """
        try:
            roh = json.dumps(wert, ensure_ascii=False)[:MAX_ERGEBNIS_ZEICHEN]
        except (TypeError, ValueError):
            # Ein nicht serialisierbares Ergebnis gibt es nicht — es geht
            # gleich darauf über dieselbe Serialisierung an die Gegenstelle.
            # Fiele es doch an, wäre die richtige Folge „nicht belegbar" und
            # nicht „Sitzung zu Ende".
            return
        self._ergebnisse.append(roh)
        self._ergebnisse_zeichen += len(roh)
        while self._ergebnisse and self._ergebnisse_zeichen > MAX_BELEGPUFFER_ZEICHEN:
            self._ergebnisse_zeichen -= len(self._ergebnisse.pop(0))

    def _woertlich(self, zeile: str) -> bool:
        """Kam diese Zeile in dieser Sitzung wirklich aus einem Werkzeug?

        Gesucht wird die **escapte** Form der Zeile. Im Puffer stehen
        serialisierte Ergebnisse, und dort trägt eine Logzeile mit
        Anführungszeichen oder Rückstrich genau die Escapes, die `json.dumps`
        ihr gegeben hat. Ohne diesen Umweg fiele ausgerechnet die Sorte Zeile
        durch, die man am häufigsten zeigen will — Stacktraces und
        Konfigurationswerte stecken voller Anführungszeichen.
        """
        if not zeile:
            return False
        nadel = json.dumps(zeile, ensure_ascii=False)[1:-1]
        return any(nadel in stueck for stueck in self._ergebnisse)

    def _beleg_zeigen(self, argumente: dict) -> object:
        """Eine gelesene Stelle auf den Bildschirm legen, statt sie vorzulesen.

        Die Schranke ist der Sinn des Werkzeugs: gezeigt wird nur, was wörtlich
        in einem Werkzeugergebnis dieser Sitzung vorkam. Ein Modell, das eine
        Zeile nacherzählt oder sie sich zurechtlegt, damit sie seine These
        stützt, prallt hier ab — und bekommt gesagt, warum. Ohne diese Prüfung
        wäre der Bildschirm die glaubwürdigste Oberfläche für die unsicherste
        Aussage im ganzen Panel.

        Gesendet wird hier nichts. Diese Methode läuft in einem Thread; sie
        legt das Ergebnis in `offener_beleg` ab, und `_aufruf_beantworten`
        bringt es von der Ereignisschleife aus zum Browser.
        """
        quelle = str(argumente.get("quelle") or "").strip()
        if not quelle:
            return {"error": "Ohne `quelle` weiss niemand, woher die Stelle stammt."}
        roh = argumente.get("zeilen")
        if not isinstance(roh, list) or not roh:
            return {"error": "`zeilen` ist eine nichtleere Liste von Textzeilen."}
        if len(roh) > MAX_BELEG_ZEILEN:
            return {"error": (
                f"Hoechstens {MAX_BELEG_ZEILEN} Zeilen auf einmal. Zeig die "
                "Stelle, die du erklaerst, nicht das ganze Log."
            )}

        zeilen: list[str] = []
        for eintrag in roh:
            if not isinstance(eintrag, str):
                return {"error": "In `zeilen` stehen Textzeilen, nichts anderes."}
            if len(eintrag) > MAX_BELEG_ZEILENLAENGE:
                return {"error": (
                    f"Eine Zeile ist laenger als {MAX_BELEG_ZEILENLAENGE} "
                    "Zeichen. Zeig den Ausschnitt, der die Sache erklaert."
                )}
            if not self._woertlich(eintrag):
                return {"error": (
                    "Diese Zeile stand in keinem Werkzeugergebnis dieser "
                    "Sitzung. Zeige nur, was du wirklich gelesen hast — "
                    "kopiere sie woertlich aus dem Ergebnis, statt sie "
                    "nachzuerzaehlen."
                )}
            zeilen.append(eintrag)

        # Die Quelle ist eine Beschriftung und stammt vom Modell. Sie geht
        # gedeckelt auf den Schirm; die Oberflaeche sagt daneben, dass der Text
        # vom Server kommt und nicht von der KI.
        self.offener_beleg = {"quelle": quelle[:200], "zeilen": zeilen}
        return {
            "angezeigt": True,
            "zeilen": len(zeilen),
            "hinweis": (
                "Die Stelle steht jetzt auf seinem Bildschirm. Lies sie NICHT "
                "vor — erklaer in Worten, was daran wichtig ist und was daraus "
                "folgt."
            ),
        }

    # ── Der Autonomieschalter ─────────────────────────────────────────────

    def _autonomie_setzen(self, argumente: dict) -> object:
        """Den autonomen Modus umlegen — denselben, den das Panel schaltet.

        Kein zweiter Freigabeweg: es geht durch `ai_autonomy_service.set_grant`
        wie der Schalter in den Einstellungen, mit denselben Rechten davor. Zwei
        Dinge stehen hier trotzdem eigens, weil sie den gesprochenen Fall von
        einem Formular unterscheiden.

        **Das Stundenbudget wird nie erhöht.** Eine bestehende Freigabe behält
        ihre Zahl, eine neue übernimmt die, die für diesen Server bisher galt —
        und nur wenn es gar keine gab, den Spaltendefault. Das Budget ist die
        Schranke gegen ein Modell in der Schleife; sie per Zuruf zu weiten wäre
        genau die Handlung, gegen die sie steht. Wer mehr will, sagt es dem
        Panel.

        **Die Rechte werden erneut geprüft**, obwohl `katalog` das Werkzeug nur
        mit `ai.autonomous.use` anbietet. Zwischen dem Aufbau des Katalogs und
        diesem Aufruf liegen Minuten, in denen ein Admin eine Rolle ändern kann,
        und der Katalog ist ohnehin eine Bitte und keine Zusage.
        """
        from database import SessionLocal
        from models import User as UserModel
        from models.ai_autonomy_grant import DEFAULT_MAX_ACTIONS_PER_HOUR
        from services import ai_autonomy_service, audit_service

        an = _wahrheitswert(argumente.get("enabled"))
        if an is None:
            return {"error": "`enabled` muss `true` oder `false` sein."}
        server_id = _serverkennung(argumente.get("server_id"))
        if server_id is _UNLESBAR:
            return {"error": (
                "`server_id` ist die Nummer eines Servers — oder sie fehlt, "
                "dann gilt der Schalter panelweit."
            )}

        with SessionLocal() as db:
            user = db.get(UserModel, self.user_id)
            if user is None or not user.is_active:
                return {"error": "Der Zugriff wurde entzogen."}
            if not permission_service.has_global_permission(db, user, "ai.autonomous.use"):
                return {"error": (
                    "Dir fehlt das Recht, den autonomen Modus zu schalten. Sag "
                    "ihm, dass ein Administrator es freigeben muss."
                )}
            if server_id is not None and not permission_service.has_server_permission(
                db, user, server_id, "server.view"
            ):
                # Spiegelt `routers/ai_autonomy._require_server_access`, samt
                # dessen Wortwahl: eine Freigabe fuer einen Server, den man
                # nicht sehen darf, wuerde ohnehin nichts bewirken — sie wuerde
                # nur dessen Existenz verraten.
                return {"error": "Server nicht gefunden."}

            # Das Budget der neuen Zeile ist das, was für diesen Server
            # **bisher galt** — und genau dafür ist der Rückfall von
            # `resolve_grant` auf die panelweite Zeile richtig.
            #
            # Hier stand einmal das Gegenteil: eine neue Serverzeile bekam den
            # Spaltendefault, mit der Begründung, das Budget des Nachbarn sei
            # nicht unseres. Das klingt sauber und war eine stille Erhöhung.
            # Weil `resolve_grant` die spezifischere Zeile vorzieht, hob ein
            # „schalt Autonomie für Server 7 ein" die wirksame Grenze dort von
            # einem knapp gesetzten panelweiten Wert auf den Default — eine Zahl,
            # die niemand genannt hatte, ausgelöst durch einen Satz über den
            # Geltungsbereich. Ein Zuruf verschiebt den Bereich, nicht das Tempo.
            vorhanden = ai_autonomy_service.resolve_grant(
                db, user_id=user.id, server_id=server_id
            )
            budget = (
                vorhanden.max_actions_per_hour if vorhanden is not None
                else DEFAULT_MAX_ACTIONS_PER_HOUR
            )

            try:
                ai_autonomy_service.set_grant(
                    db,
                    user=user,
                    server_id=server_id,
                    enabled=an,
                    max_actions_per_hour=budget,
                    granted_by=user.id,
                )
                audit_service.record_privileged_action(
                    db,
                    user_id=user.id,
                    action="ai.autonomy.updated",
                    target_type="server" if server_id is not None else "ai_autonomy",
                    target_id=server_id,
                    details={
                        "enabled": an,
                        "max_actions_per_hour": budget,
                        # Der Unterschied, den ein Betreiber spaeter sucht: der
                        # Schalter im Panel ist eine Betaetigung, dieser hier
                        # ein gesprochener Satz.
                        "channel": "voice",
                    },
                    # "ai" heisst hier wie ueberall: ein Mensch hat die KI darum
                    # gebeten. Ein Schalter, der sich selbst umlegt, waere
                    # "system" — und den gibt es nicht.
                    origin="ai",
                )
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.warning(
                    "Autonomieschalter per Stimme gescheitert user=%s error=%s",
                    self.user_id, type(exc).__name__,
                )
                return {"error": "Der Schalter liess sich nicht setzen."}

        if an and budget <= 0:
            # Die Freigabe steht, wirken kann sie nicht: `autonomy_allows` weist
            # ein Budget von null ab. Das hier gesagt zu bekommen ist besser als
            # ein „ist eingeschaltet", nach dem trotzdem jede Aenderung nachfragt
            # — und besser als eine Zahl, die wir ungefragt anheben.
            return {
                "autonom": True,
                "bereich": "panelweit" if server_id is None else f"Server {server_id}",
                "aktionen_pro_stunde": 0,
                "hinweis": (
                    "Der Schalter steht auf ein, aber das Stundenbudget ist "
                    "null — es wird trotzdem nichts ohne Rueckfrage laufen. Sag "
                    "ihm, dass er die Zahl im Panel anheben muss; per Sprache "
                    "aenderst du sie nicht."
                ),
            }

        return {
            "autonom": an,
            "bereich": "panelweit" if server_id is None else f"Server {server_id}",
            "aktionen_pro_stunde": budget,
            "hinweis": (
                "Sag ihm in einem Satz, was jetzt gilt — und bei "
                "eingeschaltetem Modus dazu, dass du Aenderungen ab sofort "
                "ohne Rueckfrage ausfuehrst."
            ),
        }

    # ── Vorschlagen und bestätigen ────────────────────────────────────────

    def _vorschlagen(self, name: str, argumente: dict) -> object:
        """Ein Schreibwerkzeug wird zum Vorschlag — wie im Chat auch.

        Derselbe `ai_proposal_service`, dieselbe Rechteprüfung, dieselbe Karte
        im Panel. Der einzige Unterschied ist, was zurückkommt: statt „liegt
        bereit" bekommt das Modell den Satz, den es vorlesen soll.

        **Ausser bei Autonomie** — dann gibt es nichts vorzulesen. Wer den
        autonomen Modus eingeschaltet hat, hat genau den Schritt abbestellt, an
        dem ein Mensch zustimmt; ihn hier durch eine Rücklesung wieder
        einzuführen, hiesse den Schalter im Sprachmodus zu ignorieren. Die
        Aktion läuft sofort, und das Modell erzählt danach, was passiert ist.
        """
        import uuid

        from database import SessionLocal
        from models import Server, User as UserModel
        from services import ai_chat_service, ai_proposal_service
        from services.ai_action_errors import AiActionValidationError

        with SessionLocal() as db:
            user = db.get(UserModel, self.user_id)
            if user is None or not user.is_active:
                return {"error": "Der Zugriff wurde entzogen."}
            gespraech = ai_chat_service.get_or_create_primary_conversation(db, user)
            try:
                vorschlag = ai_proposal_service.create_proposal(
                    db,
                    user=user,
                    conversation=gespraech,
                    tool_name=name,
                    arguments=argumente,
                    correlation_id=str(uuid.uuid4()),
                    sprache=True,
                )
                # Ob dieser Vorschlag autonom laufen darf, hat `create_proposal`
                # bereits entschieden — genauer `autonomy_allows` darin. Die
                # Frage vorab selbst zu beantworten hiesse, `_resolve_server`
                # nachzubauen: die Freigabe kann an einem Server hängen, den
                # erst der Payloadbau aus den Argumenten auflöst, und eine
                # zweite Antwort darauf wäre eine, die irgendwann abweicht.
                #
                # `ALWAYS_CONFIRM_TOOLS` kommt hier nie an, und das ist schon
                # zweifach gesichert: in `SPRACHE_HANDELN` steht keines davon
                # (ein Test in `test_ai_voice_tools` hält das fest, und
                # `create_proposal(sprache=True)` weist den Rest ab), und
                # `autonomy_allows` gibt für sie ohnehin `False` zurück. Eine
                # dritte Prüfung an dieser Stelle wäre keine zusätzliche
                # Sicherheit, sondern eine dritte Stelle zum Pflegen — dieser
                # Absatz steht hier, damit der nächste Leser nicht danach sucht.
                autonom = bool(vorschlag.autonomous)
                # Die Schranke „höchstens ein offener Vorschlag" greift erst
                # jetzt, und nur für den Fall, den sie meint: sie schützt das
                # gesprochene Ja davor, auf dem falschen Vorschlag zu landen.
                # Wo kein Ja gebraucht wird, gibt es nichts zu verwechseln.
                #
                # Dass die Zeile dafür erst entsteht und dann zurückgerollt
                # wird, ist der Preis: `create_proposal` flusht nur, der Commit
                # steht hier. Nach dem Rollback ist weder der Vorschlag noch
                # sein Auditeintrag übrig.
                if not autonom and self.offener_vorschlag is not None:
                    db.rollback()
                    return {"error": (
                        "Es liegt noch ein Vorschlag zur Bestätigung. Lies ihn "
                        "vor und warte auf ein Ja oder Nein, bevor du etwas "
                        "Neues vorschlägst."
                    )}
                # Feste Kopien vor dem Commit: danach ist das ORM-Objekt
                # abgelaufen, und `execute_autonomously` committet gleich noch
                # einmal und rollt bei einem Fehler zurück.
                kennung = vorschlag.id
                vorschau = json.loads(vorschlag.preview_json or "{}")
                server_id = vorschlag.server_id
                db.commit()
            except AiActionValidationError as exc:
                db.rollback()
                # Fehlendes Recht, fremder Server, unzulässiges Werkzeug. Das
                # Modell soll es erfahren und weiterreden können.
                return {"error": str(exc)}

            if autonom:
                return self._autonom_ausfuehren(db, kennung, user)

            servername = None
            if server_id is not None:
                server = db.get(Server, server_id)
                servername = server.name if server is not None else None
            satz = vorlesetext(name, vorschau, servername)
            self.offener_vorschlag = kennung

        return {
            "proposal_id": kennung,
            "vorlesen": satz,
            "hinweis": (
                "Lies `vorlesen` WÖRTLICH vor und frag, ob du es tun sollst. "
                "Formuliere nichts um und lass nichts weg — der Mensch stimmt "
                "dem zu, was du sagst. Bei einem klaren Ja rufe "
                f"`{BESTAETIGEN}` mit dieser `proposal_id` auf."
            ),
        }

    def _autonom_ausfuehren(self, db: Session, proposal_id: str, user: User) -> object:
        """Ein autonom freigegebener Vorschlag läuft sofort.

        Reihenfolge und Fehlerbehandlung sind die von `ai_stream_service` um
        Zeile 1258, und zwar absichtlich Zeile für Zeile: derselbe
        `execute_autonomously`, dieselbe Trennung zwischen einem
        Zustandsfehler, der eine Auskunft ist, und einem unerwarteten, der
        keine sein darf. Zwei Wege in dieselbe Ausführung mit zwei
        Fehlerbildern wären ein Betriebsrätsel — der Benutzer hörte im
        Sprachmodus etwas anderes als das, was im Chat auf der Karte steht.

        Ein Fehlschlag beendet das Gespräch nicht. Das Modell bekommt ihn als
        Ergebnis und kann ihn aussprechen; ein Abbruch wäre für den
        Sprechenden ein Aussetzer ohne Erklärung.
        """
        from services import ai_proposal_service
        from services.ai_action_errors import AiActionStateError

        try:
            _zeile, ergebnis = ai_proposal_service.execute_autonomously(
                db, proposal_id=proposal_id, user=user
            )
        except AiActionStateError as exc:
            db.rollback()
            logger.warning(
                "Autonome Sprachaktion abgewiesen user=%s grund=%s",
                self.user_id, str(exc)[:64],
            )
            return {"error": f"Das ging nicht: {exc}", "autonom": True}
        except Exception as exc:
            db.rollback()
            logger.warning(
                "Autonome Sprachaktion gescheitert user=%s error=%s",
                self.user_id, type(exc).__name__,
            )
            return {"error": "Die Ausführung ist fehlgeschlagen.", "autonom": True}

        return _gekuerzt({"ausgefuehrt": True, "autonom": True, "ergebnis": ergebnis})

    def _bestaetigen(self, proposal_id: str) -> object:
        """Das gesprochene Ja einlösen.

        Die Prüfungen hier ersetzen **nicht** die Rechteprüfung — die läuft
        unverändert zweimal in `confirm_proposal` und `execute_proposal`, und
        ein zwischenzeitlich entzogenes Recht schlägt dort weiterhin mit
        `AI_ACTION_ACCESS_REVOKED` durch. Was hier steht, ist die Frage, ob
        dieses Ja überhaupt zu diesem Vorschlag gehört.
        """
        from database import SessionLocal
        from models import User as UserModel
        from services import ai_proposal_service
        from services.ai_action_errors import AiActionStateError

        if not proposal_id:
            return {"error": "Ohne `proposal_id` gibt es nichts zu bestätigen."}
        if proposal_id != self.offener_vorschlag:
            # Ein Ja gilt genau dem Vorschlag, der gerade vorgelesen wurde.
            # Nicht dem von vorhin, und keinem, dessen Kennung das Modell aus
            # dem Verlauf abgeschrieben hat.
            return {"error": (
                "Zu dieser Kennung wartet nichts auf eine Bestätigung. Leg den "
                "Vorschlag neu an, wenn er noch gewollt ist."
            )}

        with SessionLocal() as db:
            user = db.get(UserModel, self.user_id)
            if user is None or not user.is_active:
                return {"error": "Der Zugriff wurde entzogen."}
            try:
                _vorschlag, token = ai_proposal_service.confirm_proposal(
                    db, proposal_id=proposal_id, user=user
                )
                _ausgefuehrt, ergebnis = ai_proposal_service.execute_proposal(
                    db, proposal_id=proposal_id, user=user, confirmation_token=token
                )
            except AiActionStateError as exc:
                db.rollback()
                self.offener_vorschlag = None
                logger.warning(
                    "Gesprochene Bestaetigung abgewiesen user=%s grund=%s",
                    self.user_id, str(exc)[:64],
                )
                return {"error": f"Das ging nicht: {exc}"}
            except Exception as exc:
                db.rollback()
                self.offener_vorschlag = None
                logger.warning(
                    "Gesprochene Bestaetigung gescheitert user=%s error=%s",
                    self.user_id, type(exc).__name__,
                )
                return {"error": "Die Ausführung ist fehlgeschlagen."}

        self.offener_vorschlag = None
        return _gekuerzt({"ausgefuehrt": True, "ergebnis": ergebnis})

    # ── Der Weg über die Leitung ──────────────────────────────────────────

    async def ereignis(self, ereignis: dict, oben: Any, browser: Any) -> None:
        """Werkzeugereignisse der Gegenstelle bearbeiten.

        Zwei davon zählen, und sie kommen in dieser Reihenfolge:

        1. ``response.output_item.added`` legt ein ``function_call``-Element an
           und nennt dabei **den Namen**. Er wird hier gemerkt, sonst ist er weg.
        2. ``response.function_call_arguments.done`` bringt die fertigen
           Argumente. Erst hier wird ausgeführt.

        Alles andere geht durch. Ein Anbieter, der ein Ereignis hinzufügt, darf
        keine Sitzung abreissen.
        """
        art = str(ereignis.get("type") or "")

        if art == "response.output_item.added":
            element = ereignis.get("item")
            if isinstance(element, dict) and element.get("type") == "function_call":
                kennung = element.get("id") or element.get("call_id")
                name = element.get("name")
                if isinstance(kennung, str) and isinstance(name, str):
                    self._namen[kennung] = name
            return

        if art != "response.function_call_arguments.done":
            return

        await self._aufruf_beantworten(ereignis, oben, browser)

    async def _aufruf_beantworten(self, ereignis: dict, oben: Any, browser: Any) -> None:
        import asyncio
        import contextlib

        call_id = ereignis.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            return
        name = (
            ereignis.get("name")
            or self._namen.get(str(ereignis.get("item_id") or ""))
            or self._namen.get(call_id)
        )
        if not isinstance(name, str) or not name:
            logger.warning("Sprachwerkzeug ohne Namen call_id=%s", call_id[:32])
            return

        argumente = _argumente_lesen(ereignis.get("arguments"))

        # Der Sprechende soll sehen, woran gearbeitet wird — der **Name** und
        # sonst nichts. Argumente tragen Serverkennungen und Pfade; sie gehören
        # nicht in eine Anzeige, die nebenbei mitläuft.
        with contextlib.suppress(Exception):
            await browser.send_text(
                json.dumps({"art": "werkzeug", "name": name}, ensure_ascii=False)
            )

        # Die Ausführung ist Datenbankarbeit und blockiert. Auf der
        # Ereignisschleife stünde währenddessen der Tonfluss beider Richtungen.
        ergebnis = await asyncio.to_thread(self.ausfuehren, name, argumente)

        # `zeige_beleg` lief eben im Thread und durfte von dort nichts senden.
        # Was es zeigen will, liegt im Feld und geht hier raus — vor dem
        # Ergebnis nach oben, damit die Stelle schon auf dem Schirm steht, wenn
        # das Modell anfängt, sie zu erklären.
        beleg = self.offener_beleg
        self.offener_beleg = None
        if beleg is not None:
            with contextlib.suppress(Exception):
                await browser.send_text(
                    json.dumps({"art": "beleg", **beleg}, ensure_ascii=False)
                )

        await oben.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(ergebnis, ensure_ascii=False),
            },
        }, ensure_ascii=False))
        # Ohne dieses zweite `response.create` bleibt das Ergebnis liegen und
        # das Modell schweigt — es hat geantwortet, aber niemand hat es
        # gebeten, weiterzureden. Wann es hinausgeht, entscheidet
        # `_antwort_anfordern`: sofort, oder beim Ende der laufenden Antwort.
        await self._antwort_anfordern(oben)

    # ── Wann um eine Antwort gebeten wird ─────────────────────────────────

    def antwort_begonnen(self) -> None:
        """``response.created`` — ab jetzt läuft eine Antwort."""
        self._antwort_laeuft = True

    async def antwort_beendet(self, oben: Any, *, abgebrochen: bool = False) -> None:
        """``response.done`` — und falls ein Ergebnis wartete, jetzt reden.

        Bei einem **Abbruch** wird die Bitte verworfen statt nachgeholt. Ein
        Abbruch heisst hier fast immer: der Mensch redet dazwischen. Ihm dann
        die Antwort auf seine vorige Frage entgegenzusprechen wäre genau das,
        wogegen das Unterbrechen gebaut ist. Das Ergebnis geht nicht verloren —
        es steht im Verlauf und liegt der nächsten Antwort ohnehin vor.
        """
        self._antwort_laeuft = False
        faellig, self._antwort_faellig = self._antwort_faellig, False
        if faellig and not abgebrochen:
            await oben.send(json.dumps({"type": "response.create"}))

    async def _antwort_anfordern(self, oben: Any) -> None:
        """Um eine Antwort bitten — aber nie in eine laufende hinein.

        Mehrere Werkzeuge einer Runde setzen dieselbe Marke; daraus wird **eine**
        Bitte und nicht drei. Das ist auch fachlich richtig: die Ergebnisse
        gehören in eine Antwort, nicht in drei angefangene.
        """
        if self._antwort_laeuft:
            self._antwort_faellig = True
            return
        await oben.send(json.dumps({"type": "response.create"}))


def _argumente_lesen(roh: object) -> dict:
    """Die Argumente eines Aufrufs — nachsichtig gelesen, streng verwendet.

    Die Gegenstelle schickt sie als JSON-Zeichenkette. Ist sie kaputt, wird
    daraus ein leeres Wörterbuch und **kein** Abbruch: das Werkzeug weist die
    fehlenden Pflichtfelder dann selbst ab, mit einer Meldung, die das Modell
    versteht und beantworten kann. Ein Abbruch kostete das Gespräch, ein
    Formfehler kostet eine Runde.
    """
    if isinstance(roh, dict):
        return roh
    if not isinstance(roh, str) or not roh.strip():
        return {}
    try:
        gelesen = json.loads(roh)
    except ValueError:
        return {}
    return gelesen if isinstance(gelesen, dict) else {}


def _wahrheitswert(roh: object) -> bool | None:
    """Ein Ja/Nein aus dem Argument lesen — ``None`` heisst „unlesbar".

    Nachsichtig lesen, streng verwenden, und hier ist die Strenge wichtiger als
    sonst: `bool("false")` ist wahr. Ein Modell, das den Wert als Zeichenkette
    schickt — und Realtime-Modelle tun das gelegentlich —, hätte damit die
    Rückfragepflicht ausgeschaltet, während der Mensch gerade gesagt hat, dass
    er sie will. Was hier nicht eindeutig ist, wird deshalb nicht geraten,
    sondern zurückgefragt.
    """
    if isinstance(roh, bool):
        return roh
    if isinstance(roh, str):
        text = roh.strip().lower()
        if text in ("true", "ja", "an", "ein", "1"):
            return True
        if text in ("false", "nein", "aus", "0"):
            return False
    return None


#: „Da stand etwas, aber keine Serverkennung." Ein eigener Wert, weil ``None``
#: an dieser Stelle schon vergeben ist — es heisst „panelweit".
_UNLESBAR = object()


def _serverkennung(roh: object) -> int | None | object:
    """Die Serverkennung eines Arguments — oder ``_UNLESBAR``.

    ``True`` ist in Python eine Eins, und ein Modell, das ``server_id: true``
    schickt, meinte nie Server 1. Deshalb steht die Bool-Prüfung vor der
    Zahlenprüfung und nicht danach.
    """
    if roh is None or roh == "":
        return None
    if isinstance(roh, bool):
        return _UNLESBAR
    if isinstance(roh, int):
        return roh
    if isinstance(roh, str):
        try:
            return int(roh.strip())
        except ValueError:
            return _UNLESBAR
    return _UNLESBAR
