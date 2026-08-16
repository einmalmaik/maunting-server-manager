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
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from models import User
from services import ai_tool_registry
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
    "propose_guardian_tuning": "Die Guardian-Einstellungen von {server} ändern",
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
    #: Welches Sitzungselement zu welchem Werkzeugnamen gehört.
    #:
    #: Nötig, weil die Gegenstelle den Namen **einmal** nennt, wenn sie das
    #: Element anlegt (``response.output_item.added``), und danach nur noch die
    #: Argumente nachschiebt. Wer sich den Namen dort nicht merkt, bekommt am
    #: Ende Argumente ohne Werkzeug.
    _namen: dict[str, str] = field(default_factory=dict)

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
        return _gekuerzt(wert)

    # ── Vorschlagen und bestätigen ────────────────────────────────────────

    def _vorschlagen(self, name: str, argumente: dict) -> object:
        """Ein Schreibwerkzeug wird zum Vorschlag — wie im Chat auch.

        Derselbe `ai_proposal_service`, dieselbe Rechteprüfung, dieselbe Karte
        im Panel. Der einzige Unterschied ist, was zurückkommt: statt „liegt
        bereit" bekommt das Modell den Satz, den es vorlesen soll.
        """
        import uuid

        from database import SessionLocal
        from models import Server, User as UserModel
        from services import ai_chat_service, ai_proposal_service
        from services.ai_action_errors import AiActionValidationError

        if self.offener_vorschlag is not None:
            return {"error": (
                "Es liegt noch ein Vorschlag zur Bestätigung. Lies ihn vor und "
                "warte auf ein Ja oder Nein, bevor du etwas Neues vorschlägst."
            )}

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
                db.commit()
            except AiActionValidationError as exc:
                db.rollback()
                # Fehlendes Recht, fremder Server, unzulässiges Werkzeug. Das
                # Modell soll es erfahren und weiterreden können.
                return {"error": str(exc)}

            vorschau = json.loads(vorschlag.preview_json or "{}")
            servername = None
            if vorschlag.server_id is not None:
                server = db.get(Server, vorschlag.server_id)
                servername = server.name if server is not None else None
            satz = vorlesetext(name, vorschau, servername)
            self.offener_vorschlag = vorschlag.id
            kennung = vorschlag.id

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
        # gebeten, weiterzureden.
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
