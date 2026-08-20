"""Ownership, DIS-Schutz, Secret-Abweisung und Abruf fuer AI-Memory."""

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import re
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query, Session

from models import AiMemoryEntry, AiMemoryPreference, Server, Team, User
from services import (
    ai_embedding_service,
    ai_limit_service,
    audit_service,
    permission_service,
)
from services.ai_redaction import enthaelt_zugangsdaten
from services.ai_embedding_service import EMBEDDING_DIMENSIONS
from services.ai_embedding_service import MODEL_TAG as _EMBEDDING_MODEL_TAG
from services.dis_client import DisClient, DisDecryptionError, DisSidecarError


logger = logging.getLogger(__name__)

#: Was diesem Benutzer gehoert und deshalb an seiner Einwilligung haengt.
#: `team` und `panel` gehoeren dem Team bzw. dem Betreiber.
PERSOENLICHE_SCOPES = ("user", "server")
#: Wieviele Einträge eine Seite trägt — in **jeder** blätternden Ansicht.
#:
#: Der Name stammt von der Profilansicht, die als erste blättern musste; seit
#: die Bereichsansicht (`scope_entries`) dasselbe tut, gilt die Zahl für beide.
#: Zwei Seitengrößen nebeneinander wären zwei Zahlen mit derselben Begründung,
#: und eine davon liefe der anderen irgendwann davon.
#:
#: Die Zahl steht hier und nicht in der Anfrage. Jede Zeile dieser Seite kostet
#: einen eigenen Roundtrip zum DIS-Sidecar; wer die Grenze selbst setzen dürfte,
#: könnte sich 5.000 davon auf einmal bestellen — gemessen am 19.08.2026 sind
#: das 10,3 s bei 2 ms je Roundtrip, genug für ein Gateway-Timeout. Der Aufrufer
#: sagt also nur, *wo* er weiterlesen will, nie *wieviel*.
#:
#: 200 bleibt bei 0,1 bis 0,4 s und ist immer noch eine Seite, die ein Mensch
#: überfliegen kann. Der Deckel je Bereich liegt bei 5.000 — das sind 25 Seiten
#: und damit eine Zahl, die man auch durchblättert.
PERSONAL_PAGE_SIZE = 200
# Der **Sockel** des Blocks: soviel Platz bekommt das Gedächtnis, wenn der
# Aufrufer kein Kontextfenster nennt. Wer eines kennt, reicht es als `budget`
# an `provider_memory_context` durch — die Zahl kommt dann aus
# `ai_context_service.teilbudgets(...).gedaechtnis_zeichen` und wächst mit dem
# Fenster des Modells. Vorher war diese Konstante die einzige Wahrheit, und das
# Gedächtnis blieb als einziger Kontextblock bei 6.000 Zeichen stehen, auch wo
# 200.000 zur Verfügung standen.
MAX_CONTEXT_CHARS = 6_000
# Wieviele Einträge eine *einzelne Anfrage* höchstens entschlüsselt — beim
# Sockelbudget. Wächst das Budget, wächst der Deckel im selben Verhältnis mit
# (`provider_memory_context`), denn er ist gegen genau dieses Budget gerechnet.
#
# Das ist eine andere Zusage als `max_memory_entries`, und die eine ersetzt die
# andere nicht: jenes Rollenlimit deckelt einen **Bereich**, dieser Wert deckelt
# eine **Anfrage**. Der Unterschied ist der Multiplikator dazwischen, und der
# gehoert nicht dem Betreiber, sondern dem Benutzer: mit jedem Server, den er
# sehen darf, und jedem Team, das er gruendet, kommt ein weiterer Bereich hinzu.
# `provider_memory_context` filtert nur `server_shared` auf den einen aktuellen
# Server — die persoenlichen Servernotizen kommen fuer *alle* sichtbaren Server
# mit. Bei einem Rollenlimit an der Obergrenze
# (`ai_limit_service.MAX_MEMORY_ENTRIES_MAX`, heute 5.000) und zwanzig Anlagen
# sind das über 105.000 Zeilen, und jede kostet in `_entschluesseln` einen eigenen
# HTTP-Roundtrip zum DIS-Sidecar — vor dem Schnitt auf `MAX_CONTEXT_CHARS`,
# weil sich erst am Klartext messen laesst, was ins Budget passt. Die
# Roundtrips laufen inzwischen zu mehreren gleichzeitig
# (`_ENTSCHLUESSELN_GLEICHZEITIG`); das teilt den Aufwand, es begrenzt ihn
# nicht — dafuer ist dieser Deckel da.
#
# 300 ist gegen genau dieses Budget gewaehlt: bei kurzen Eintraegen passen
# hoechstens rund 150 Zeilen in 6.000 Zeichen, der Deckel liegt also beim
# Doppelten dessen, was ueberhaupt je gezeigt werden koennte. Genau deshalb
# darf er nicht stehenbleiben, wenn das Budget wächst — sonst wäre die
# Begründung dieser Zahl bei einem großen Fenster schlicht falsch. Unterhalb
# davon aendert sich **nichts** — `_vorauswahl` reicht die Zeilen dann
# unveraendert durch. Er greift nur dort, wo das Budget ohnehin das meiste
# weggeworfen haette.
MAX_CONTEXT_ROWS = 300
# Nach so vielen Tagen ohne Nutzung haelbiert sich der Aktualitaetsbonus. Grob
# an "eine Arbeitswoche" angelehnt; der Wert entscheidet nur bei Platzmangel.
RECENCY_HALFLIFE_DAYS = 7.0

#: Ab welcher Abrufstaerke ein Eintrag **verkuerzt** in den Block geht.
#:
#: **Das Gedaechtnis vergisst nicht, es verblasst.** Bis hierher galt: passt
#: alles ins Budget, kommt alles gleich stark mit — und erst wenn es nicht
#: passt, wird ausgewaehlt. Gemessen am 19.08.2026 lag die Auslastung bei
#: 14,6 % (874 von 6.000 Zeichen); es haette rund 41 Eintraege gebraucht,
#: bevor ueberhaupt irgendetwas bewertet worden waere. Bis dahin steht der
#: Eintrag von gestern gleichberechtigt neben dem von vor drei Monaten.
#:
#: Der Betreiber hat das Ziel so beschrieben: aeltere Eintraege sollen "in den
#: Hintergrund" treten und "verwaschen nach der Zeit, aber sie verschwinden
#: nicht komplett" — und wenn ein Reiz kommt, sind sie wieder da. Ausdruecklich
#: nicht nur nach Alter: auch ein junger Eintrag, der nie gebraucht wurde,
#: gehoert nach hinten.
#:
#: Das ist der Unterschied zwischen **Speicherstaerke** und **Abrufstaerke**.
#: Gespeichert bleibt alles, unveraendert und vollstaendig; was sich aendert,
#: ist die Praesenz im Kontext. Ein verblasster Eintrag steht weiter da, nur
#: kuerzer — und sobald die Frage ihn trifft (ueber Bedeutung oder Wortbezug),
#: ist er sofort wieder vollstaendig.
VERBLASSEN_AB = 0.35

#: Wieviele Zeichen ein verblasster Eintrag noch bekommt.
#:
#: Nicht null: er soll auffindbar bleiben. Das Modell sieht Schluessel und
#: Anfang und kann bei Bedarf mit `search_memory` nachfassen — genau der Weg,
#: den ein Mensch nimmt, wenn ihm etwas "auf der Zunge liegt".
VERBLASST_ZEICHEN = 60

#: Wieviel Aehnlichkeit einen bestehenden Eintrag zum Duplikat macht.
#:
#: Konflikte loest das Gedaechtnis ueber den Schluessel: derselbe Key wird
#: ueberschrieben. Das ist sauber, solange die KI den vorhandenen Schluessel
#: wiederfindet — und genau das ist die Schwachstelle. `ram.vorgabe`,
#: `standard_ram`, `speicher.default` sind drei Namen fuer denselben Fakt, und
#: keiner faellt jemandem auf, bis der Bereich voll ist und sich drei Antworten
#: widersprechen.
#:
#: 0,70 ist bewusst hoch. Das Modell trennt Unverwandtes sicher (`Zeitzone` zu
#: `Pizza` 0,04), aber Verwandtes traegt es nicht immer weit genug (`Sicherung`
#: zu `backup` nur 0,27). Eine niedrige Schwelle wuerde deshalb echte,
#: verschiedene Eintraege zusammenwerfen — und ein faelschlich
#: zusammengelegter Fakt ist teurer als ein doppelter.
DUPLIKAT_AB = 0.70

#: Wieviel Abrufstaerke ein frisch gemerkter Eintrag mitbringt.
#:
#: Ohne diesen Startwert waere jeder neue Eintrag sofort blass: er hat noch
#: keine Nutzung, und `use_count` ist der staerkste Anteil der Formel. Genau
#: das war beim Vorgaenger der Fehler, gegen den `recency` eingebaut wurde.
NEUHEITSSCHUTZ_TAGE = 3.0
_WORD_RE = re.compile(r"[\w]+", re.UNICODE)

#: Wieviele Zeilen gleichzeitig beim DIS-Sidecar liegen duerfen.
#:
#: An einer Entschluesselung ist fast nichts Rechnung: der Sidecar oeffnet ein
#: paar hundert Byte AES-GCM, alles andere ist der Weg hin und zurueck. Gehen
#: die Zeilen nacheinander, addiert sich genau diese Wartezeit — gemessen am
#: 19.08.2026 bei 300 Zeilen 150 ms (0,5 ms je Roundtrip) bis 600 ms (2 ms),
#: die der Benutzer vor dem ersten Byte der Antwort absitzt. `personal_entries`
#: traegt denselben Aufschlag je Eintrag und war deshalb der teurere Weg: ohne
#: Grenze waren es dort bei 5.000 Zeilen 10,3 s. Seit die Profilansicht
#: seitenweise laedt (`PERSONAL_PAGE_SIZE`), sind es 200 Zeilen je Klick.
#: Nebenlaeufig faellt davon der Bruchteil an.
#:
#: Acht ist bewusst eine kleine feste Zahl und keine Einstellung. Der Sidecar
#: ist ein einzelner Node-Prozess auf demselben Rechner; mehr gleichzeitige
#: Verbindungen kaufen dort nichts, weil die Zeit im Roundtrip liegt und nicht
#: in seiner Rechenzeit. Wirklich billiger waere ein Sammelendpunkt — den gibt
#: es heute nicht: `dis-sidecar/server.mjs` kennt unter `/decrypt` genau einen
#: Ciphertext je Anfrage.
_ENTSCHLUESSELN_GLEICHZEITIG = 8


class MemoryScopeVoll(HTTPException):
    """409 mit dem Zählstand daneben, weil ein Text allein zu wenig trägt.

    Bleibt bewusst eine `HTTPException`: der Router in `routers/ai_memory.py`
    und jeder andere Aufrufer merken von dieser Klasse nichts, bekommen
    denselben 409 und dasselbe `detail` wie vorher — und dieses `detail` ist
    ein Satz für einen **Menschen**, denn über den Router legt der Benutzer
    selbst einen Eintrag an und liest die Meldung als Toast.

    Die drei Zahlen daneben sind für die eine Stelle da, die aus derselben
    Tatsache eine Anweisung an das *Modell* macht (`_execute_remember`). Ohne
    sie müsste die auf den Meldungstext horchen, um „gesperrt“ von „genau
    voll“ von „nachträglich gesenkt“ zu unterscheiden — und jede Umformulierung
    hier änderte drüben stillschweigend das Verhalten, ohne dass ein Test es
    merkt.
    """

    def __init__(self, *, bereich: str, bestand: int, grenze: int, detail: str) -> None:
        super().__init__(status_code=409, detail=detail)
        self.bereich = bereich
        self.bestand = bestand
        self.grenze = grenze


def _aad(row: AiMemoryEntry) -> str:
    """Die Zusatzdaten, an die der Ciphertext gebunden ist.

    Version 2 nimmt den Scope mit auf und bindet den Eintrag damit
    kryptografisch an seinen Besitzer. Wer in der Datenbank `owner_user_id`
    oder `scope_identity` umschreibt, um an fremde Notizen zu kommen, macht sie
    damit **unlesbar**, statt sie zu uebernehmen — die Entschluesselung
    scheitert an der nicht mehr passenden AAD.

    Version 1 ist der Bestand aus Phase C, gebunden nur an die Eintrags-ID. Er
    bleibt lesbar, bis der Eintrag das naechste Mal geschrieben wird. Eine
    Neuverschluesselung waehrend der Migration schied aus: der DIS-Sidecar
    laeuft zu diesem Zeitpunkt nicht garantiert, und eine Migration, die an
    einem HTTP-Aufruf scheitern kann, ist keine.
    """
    if int(row.aad_version or 1) >= 2:
        return f"msm:ai:memory:{row.scope_identity}:{row.id}"
    return f"msm:ai:memory:{row.id}"


def scope_identity(
    db: Session, user: User, scope: str, server_id: int | None,
    team_id: int | None = None,
) -> tuple[str, int | None, int | None, int | None]:
    """Loest einen Scope in seine Kennung und die zugehoerigen Fremdschluessel auf.

    Die Kennung ist der Primaerfilter jeder Leseabfrage und damit die
    eigentliche Trennlinie zwischen den Benutzern. Sie wird ausschliesslich
    hier gebildet — jede Stelle, die selbst eine Zeichenkette zusammensetzt,
    waere eine Stelle, an der die Trennung falsch sein kann.
    """
    if scope == "user":
        if server_id is not None or team_id is not None:
            raise HTTPException(status_code=422, detail="User-Memory akzeptiert keinen Bezug")
        return f"user:{user.id}", user.id, None, None
    if scope in ("server", "server_shared"):
        if team_id is not None:
            raise HTTPException(status_code=422, detail="Server-Memory akzeptiert kein Team")
        # Existenz **und** Recht — vorher stand hier nur das Recht.
        # `has_server_permission` laedt den Server nie: fuer einen Owner oder
        # eine Rolle mit pauschalem `server.view` ist damit jede beliebige
        # Nummer erlaubt. Eine erfundene ID kam so bis zum `db.commit()` durch
        # und scheiterte erst am Fremdschluessel auf `servers.id`; der
        # IntegrityError-Handler weiter unten deutet das als Schreibkonflikt und
        # antwortet "Bitte erneut versuchen". Das Modell befolgt diese
        # Aufforderung und wiederholt denselben aussichtslosen Aufruf, statt mit
        # `list_my_servers` nach der richtigen Nummer zu suchen. Die Diagnose
        # muss stimmen, sonst fuehrt sie das Modell in die Irre.
        #
        # Ein nicht existierender und ein nicht sichtbarer Server bleiben
        # ununterscheidbar — dieselbe Zusage wie in `_resolve_server`: sonst
        # waere die Fehlermeldung ein Existenzorakel ueber fremde Server.
        if (
            server_id is None
            or db.get(Server, server_id) is None
            or not permission_service.has_server_permission(
                db=db, user=user, server_id=server_id, key="server.view"
            )
        ):
            raise HTTPException(status_code=404, detail="Server nicht gefunden")
        if scope == "server":
            return f"server:{server_id}:user:{user.id}", user.id, server_id, None
        # Das Wissen der Anlage. Bewusst **ohne** Besitzer, genau wie bei `team`
        # weiter unten: es soll stehenbleiben, wenn der Kollege geht, der es
        # aufgeschrieben hat — und `owner_user_id` traegt ein CASCADE auf
        # `users.id`, das es mitnehmen wuerde.
        #
        # Die Kennung endet auf `:shared` und nicht auf der blossen Servernummer.
        # `server:62` waere ein **Praefix** von `server:62:user:7`; jede
        # Vergleichslogik, die je mit `startswith` arbeitet, saehe die
        # persoenliche Notiz eines Kollegen dann als Anlagenwissen an.
        return f"server:{server_id}:shared", None, server_id, None
    if scope == "team":
        from services import team_service

        if server_id is not None:
            raise HTTPException(status_code=422, detail="Team-Memory akzeptiert keinen Server")
        if team_id is None or team_service.membership(db, team_id, user.id) is None:
            # 404 statt 403: ob es ein Team mit dieser Nummer gibt, geht einen
            # Aussenstehenden nichts an.
            raise HTTPException(status_code=404, detail="Team nicht gefunden")
        # Bewusst **ohne** Besitzer: Teamwissen gehoert dem Team. Es soll
        # bestehen bleiben, wenn der Kollege geht, der es aufgeschrieben hat —
        # und ein `ondelete="CASCADE"` auf den Benutzer wuerde es mitnehmen.
        return f"team:{team_id}", None, None, team_id
    if scope == "panel":
        if server_id is not None or team_id is not None:
            raise HTTPException(status_code=422, detail="Panel-Memory akzeptiert keinen Bezug")
        return "panel", None, None, None
    raise HTTPException(status_code=422, detail="Unbekannter Memory-Scope")


# So lange nach einem "Nein" Ruhe ist, bevor erneut gefragt wird.
NOTICE_REPEAT_HOURS = 24


def preference(db: Session, user_id: int) -> bool:
    """Darf sich die KI fuer diesen Benutzer etwas merken?

    Ohne Zeile: **nein**. Frueher stand hier `True` — das Gedaechtnis war also
    fuer jeden neuen Benutzer stillschweigend eingeschaltet. Das ist bei einer
    Funktion, deren Inhalt an einen externen Anbieter geht, die falsche
    Voreinstellung, unabhaengig davon, wie nuetzlich sie ist.
    """
    row = db.get(AiMemoryPreference, user_id)
    return False if row is None else row.enabled


def _preference_row(db: Session, user_id: int) -> AiMemoryPreference:
    row = db.get(AiMemoryPreference, user_id)
    if row is None:
        row = AiMemoryPreference(user_id=user_id, enabled=False)
        db.add(row)
        db.flush()
    return row


def set_preference(db: Session, user: User, enabled: bool) -> AiMemoryPreference:
    row = _preference_row(db, user.id)
    row.enabled = enabled
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row


def notice_due(db: Session, user_id: int) -> bool:
    """Soll dem Benutzer der Hinweis vor der naechsten Nachricht gezeigt werden?

    Drei Bedingungen, alle noetig: das Gedaechtnis ist aus, der Benutzer hat
    den Hinweis nicht dauerhaft abbestellt, und seit dem letzten Mal ist genug
    Zeit vergangen. Ist das Gedaechtnis an, gibt es nichts zu fragen.
    """
    row = db.get(AiMemoryPreference, user_id)
    if row is None:
        return True
    if row.enabled or row.notice_hidden:
        return False
    if row.notice_last_shown_at is None:
        return True
    shown = _utc(row.notice_last_shown_at)
    return (datetime.now(timezone.utc) - shown).total_seconds() >= NOTICE_REPEAT_HOURS * 3600


def record_notice_answer(
    db: Session, user: User, *, enable: bool, hide_future: bool
) -> AiMemoryPreference:
    """Verarbeitet die Antwort auf den Hinweis.

    "Ja" schaltet ein. "Nein" laesst es aus und merkt sich den Zeitpunkt, damit
    in 24 Stunden erneut gefragt wird. "Nicht mehr anzeigen" beendet das
    Fragen — aber nicht die Moeglichkeit: unter Profil > Memory bleibt der
    Schalter erreichbar.
    """
    row = _preference_row(db, user.id)
    if enable:
        row.enabled = True
    if hide_future:
        row.notice_hidden = True
    row.notice_last_shown_at = datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return row


def _safe_value(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 2_000:
        raise HTTPException(status_code=422, detail="Memory-Inhalt ist leer oder zu gross")
    # `enthaelt_zugangsdaten`, nicht "Schwaerzung veraendert etwas": die
    # Schwaerzung ersetzt auch E-Mail-Adressen und Zahlenkontingente
    # ("Serverwechsel-Token: 2"), und beides sind keine Zugangsdaten. Wer
    # "Rechnungen gehen an billing@firma.de" merken will, legt die Adresse
    # absichtlich in seinen eigenen Vorrat — die Abweisung log ihn an
    # ("darf keine Zugangsdaten enthalten") und die KI erklaerte ihm dann,
    # er habe ein Passwort geschickt.
    if enthaelt_zugangsdaten(normalized):
        raise HTTPException(status_code=422, detail="Memory darf keine Zugangsdaten enthalten")
    return normalized


def list_entries(
    db: Session, user: User, scope: str, server_id: int | None,
    team_id: int | None = None,
) -> list[tuple[AiMemoryEntry, str]]:
    """Ein ganzer Bereich auf einmal — eine Entschlüsselung je Zeile.

    Der ungeblätterte Leseweg, und damit der teuerste. Die Oberfläche nimmt ihn
    nicht mehr: sie liest Seiten (`scope_entries`), seit ein Teambereich 5.000
    Einträge fassen darf. Wer ihn ruft, sollte den Bereich kennen, den er
    aufmacht.
    """
    identity, _, _, _ = scope_identity(db, user, scope, server_id, team_id)
    rows = db.query(AiMemoryEntry).filter(AiMemoryEntry.scope_identity == identity).order_by(AiMemoryEntry.key).all()
    return _entschluesseln_lesbare(rows)


@dataclass(frozen=True)
class Gedaechtnisseite:
    """Ein Ausschnitt einer Gedächtnisansicht und die zwei Zahlen daneben.

    Beide Zahlen zählen etwas anderes, und beide werden gebraucht:

    * ``gesamt`` trägt die Ansage über der Liste ("5.000 Einträge, Seite 1 von
      25"). Ohne sie wäre die Seitenweise genau der stille Deckel, den sie
      ersetzen soll — der Benutzer sähe 200 Zeilen und nichts, was ihm sagt,
      dass 4.800 dahinterliegen.
    * ``loeschbar`` ist davon das, was "Alle löschen" wirklich trifft. In der
      Profilansicht sind das nur die allgemeinen Einträge (``scope='user'``):
      gelöscht wird über die Kennung ``user:{id}``, die Servernotizen liegen
      unter ``server:{sid}:user:{uid}`` und bleiben stehen. In der Ansicht eines
      einzelnen Bereichs sind es alle, denn dort *ist* die Kennung die Ansicht.
      Die Bestätigungsfrage muss die Zahl nennen, die sie danach wirklich
      trifft — sonst fragt sie nach 200 und meldet 4.800.
    """

    eintraege: list[tuple[AiMemoryEntry, str]]
    gesamt: int
    loeschbar: int


#: Die Reihenfolge, in der eine Seite geschnitten wird: zuletzt genutzt zuerst,
#: nie Genutztes hinten, dann nach Schlüssel.
#:
#: Dieselbe Reihenfolge, die die Oberfläche schon immer herstellte — nur ist sie
#: seit der Seitenweise keine Geschmacksfrage mehr, sondern die Schnittkante:
#: welche Zeile auf Seite 3 landet, entscheidet allein sie. Wer sie hier ändert,
#: ändert die Seiteneinteilung, und wer in der Oberfläche anders sortiert,
#: bekommt eine Seite in einer anderen Reihenfolge angezeigt, als sie
#: geschnitten wurde.
#:
#: ``last_used_at IS NULL`` als erstes Kriterium statt ``NULLS LAST``: das ist
#: auf SQLite wie auf PostgreSQL dasselbe Ergebnis, während die beiden ohne
#: Angabe entgegengesetzt sortieren (PostgreSQL stellt NULL bei DESC nach vorn,
#: SQLite nach hinten). Bei einer Seiteneinteilung wäre das nicht Kosmetik,
#: sondern eine andere Seite je Datenbank.
_SEITENORDNUNG = (
    AiMemoryEntry.last_used_at.is_(None),
    AiMemoryEntry.last_used_at.desc(),
    AiMemoryEntry.key,
)


def _seite(
    basis: Query, offset: int, *, loeschbar: int | None = None
) -> Gedaechtnisseite:
    """Eine Seite aus einer Bestandsabfrage — der gemeinsame Rumpf aller Ansichten.

    ``basis`` ist die ungeschnittene Abfrage und wird zweimal gebraucht: einmal
    für die Zeilen dieser Seite und einmal für die Gesamtzahl daneben. Nur die
    Zeilen der Seite gehen durch den Sidecar; die Zahl kommt aus der Datenbank
    und kostet nichts.

    ``loeschbar`` bestimmt der Aufrufer, denn nur er weiß, was sein "Alle
    löschen" trifft (siehe `Gedaechtnisseite`). ``None`` heißt "alles, was diese
    Abfrage findet" — dann wird nicht zweimal dasselbe gezählt.

    Die Seitengröße steht bewusst **nicht** in der Signatur: sie wird in
    Sidecar-Roundtrips bezahlt, und eine Größe, die der Aufrufer wählen darf,
    ist keine Grenze.
    """
    rows = (
        basis.order_by(*_SEITENORDNUNG)
        .offset(max(0, offset))
        .limit(PERSONAL_PAGE_SIZE)
        .all()
    )
    gesamt = basis.count()
    return Gedaechtnisseite(
        eintraege=_entschluesseln_lesbare(rows),
        gesamt=gesamt,
        loeschbar=gesamt if loeschbar is None else loeschbar,
    )


def scope_entries(
    db: Session, user: User, scope: str, server_id: int | None = None,
    team_id: int | None = None, *, offset: int = 0,
) -> Gedaechtnisseite:
    """Eine Seite **eines** Bereichs — Team, Panel oder das Wissen einer Anlage.

    Dieselbe Auswahl wie `list_entries`, nur in Stücken. Von den drei Bereichen
    braucht das heute genau einer: `panel` und `server_shared` hängen an der
    festen `ai_limit_service.MAX_SYSTEM_SCOPE_ENTRIES` und passen damit immer
    auf eine Seite, `team` hängt am Rollenlimit seines Gründers und darf seit
    dem 19.08.2026 bis zu 5.000 Einträge fassen. Jeder davon kostet beim Öffnen
    einen eigenen Roundtrip zum DIS-Sidecar — gemessen 10,3 s bei 5.000 Zeilen,
    also dieselbe Wartezeit, gegen die die Profilansicht längst geschützt ist.

    Ein stiller Deckel wäre hier so falsch wie dort: wer diese Liste aufruft,
    will aufräumen, und eine Ansicht, die 300 von 5.000 zeigt, versteckt genau
    die Zeilen, wegen denen er gekommen ist. `gesamt` steht deshalb daneben, und
    die nächste Seite ist einen Klick entfernt.

    ``loeschbar`` bleibt hier ``None`` und ist damit gleich ``gesamt``:
    `delete_all_entries` räumt genau diese eine Kennung ab. In der Profilansicht
    ist das anders — dort liegen zwei Bereiche in einer Liste.
    """
    identity, _, _, _ = scope_identity(db, user, scope, server_id, team_id)
    return _seite(
        db.query(AiMemoryEntry).filter(AiMemoryEntry.scope_identity == identity),
        offset,
    )


def personal_entries(
    db: Session, user: User, *, offset: int = 0
) -> Gedaechtnisseite:
    """Eine Seite von allem, was diesem Benutzer selbst gehoert.

    `list_entries` fragt genau eine Scope-Kennung ab und braucht dafuer bei
    serverbezogenen Notizen eine konkrete `server_id`. Damit war der Bereich
    ueber die Oberflaeche nicht erreichbar: die KI schreibt solche Notizen
    (`remember` mit scope='server'), sie fliessen in jeden Chat und zaehlen
    gegen die Bereichsgrenze — sehen oder loeschen konnte man sie nicht.

    Serverbezogene Eintraege bleiben persoenlich (`owner_user_id` ist gesetzt,
    die Kennung lautet `server:{sid}:user:{uid}`), gehoeren also ins Profil und
    nicht zum Server. Anders als beim Kontextaufbau wird hier **nicht** auf
    `server.view` geprueft: es ist der eigene Eintrag, und wer den Zugriff auf
    einen Server verliert, soll seine Notiz dazu weiterhin loeschen koennen.

    **Seitenweise statt gedeckelt.** Bis der Bereich 1.000 Eintraege fasste,
    ging hier alles auf einmal durch den Sidecar; bei 5.000 sind das gemessen
    10,3 s (2 ms je Roundtrip) und der Multiplikator je sichtbarem Server kommt
    noch obendrauf. Ein `MAX_CONTEXT_ROWS` wie im Chatweg waere hier trotzdem
    die falsche Antwort und bleibt es: dort schuetzt der Deckel *jede*
    Nachricht und schneidet an einer Rangfolge, die zur Frage passt — hier hat
    der Benutzer genau diese Liste angefordert, um sie aufzuraeumen, und eine
    Ansicht, die stillschweigend 300 von 5.000 zeigt, versteckte genau die
    Zeilen, wegen denen er gekommen ist.

    Der Unterschied ist, dass eine Seite nichts verschweigt: `gesamt` steht
    daneben, und die naechste Seite ist einen Klick entfernt. `offset` ist das
    einzige, was der Aufrufer bestimmt — die Groesse gehoert dem Dienst
    (`PERSONAL_PAGE_SIZE`), weil sie in Sidecar-Roundtrips bezahlt wird.

    Geschnitten wird wie in jeder anderen Seitenansicht (`_SEITENORDNUNG`); den
    Unterschied macht allein die Abfrage darunter — sie geht über den
    **Besitzer** und nicht über eine Bereichskennung, denn genau diesen Bereich
    gibt es als Kennung nicht.
    """
    basis = db.query(AiMemoryEntry).filter(
        AiMemoryEntry.owner_user_id == user.id,
        AiMemoryEntry.scope.in_(PERSOENLICHE_SCOPES),
    )
    return _seite(
        basis,
        offset,
        # Die einzige Ansicht, in der "Alle löschen" weniger trifft, als die
        # Liste zeigt: die Servernotizen stehen mit drin und bleiben stehen.
        loeschbar=basis.filter(AiMemoryEntry.scope == "user").count(),
    )


def _assert_may_write(
    db: Session, user: User, scope: str, team_id: int | None,
    server_id: int | None = None,
) -> None:
    """Wer einen geteilten Bereich veraendern darf.

    Persoenliche Eintraege brauchen keine Pruefung — sie verlassen den Benutzer
    nie. Alles Geteilte verlangt ein Recht, und zwar *dasselbe*, das ein Mensch
    fuer denselben Schritt braeuchte. Genau darin liegt die Zusicherung, die
    ueber jedem KI-Schreibvorgang steht: **die KI kann nie mehr teilen, als der
    Benutzer selbst teilen duerfte.**

    ``server_id`` ist bereits durch `scope_identity` gegangen und damit gegen
    `server.view` geprueft. Hier steht die zweite, engere Frage: Lesen reicht,
    um das Wissen einer Anlage zu **sehen**; um es zu **aendern**, muss man an
    der Anlage auch etwas aendern duerfen. Sonst schriebe ein Gast eine Ansage
    in die Betriebsanleitung, nach der sich alle anderen richten.
    """
    if scope == "server_shared":
        if server_id is None or not permission_service.has_server_permission(
            db=db, user=user, server_id=server_id, key="server.config.write"
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Du darfst das Wissen dieses Servers nicht veraendern. Fuer "
                    "eine eigene Notiz dazu nimm scope='server'."
                ),
            )
        return
    if scope == "panel":
        if not permission_service.has_global_permission(db, user, "panel.settings.write"):
            raise HTTPException(status_code=403, detail="Keine Berechtigung")
        return
    if scope == "team":
        from services import team_service

        if team_id is None or not team_service.can_manage_team_memory(db, user, team_id):
            raise HTTPException(
                status_code=403,
                detail="Du darfst das Wissen dieses Teams nicht veraendern",
            )
        # Ein persoenliches Team ist kein Ablageort fuer Teamwissen. Der Eintrag
        # laege unter `team:{persoenlich}` und waere danach **nirgends**
        # sichtbar: die persoenliche Ansicht zeigt `scope='user'`, eine
        # Teamansicht gibt es fuer das Ein-Mann-Team nicht. Der KI-Weg stuft
        # deshalb auf `scope='user'` herunter — genau deswegen gehoert die Regel
        # hierher und nicht in die Aufrufer.
        ziel = db.get(Team, team_id)
        if ziel is not None and ziel.is_personal:
            raise HTTPException(
                status_code=422,
                detail="Persoenliches Wissen gehoert nicht in ein Team",
            )


def _bereichsname(
    db: Session, scope: str, server_id: int | None, team_id: int | None
) -> str:
    """Benennt einen Bereich so, dass der Satz darum vor einem Menschen besteht.

    Der zweite Benenner dieses Moduls neben dem in `_memory_line`, und das mit
    Absicht. Das dortige Etikett (`server:62:anlage`) ist eine Marke für die
    Maschine: es steht vor **jeder** Kontextzeile, wird also je Zeile bezahlt,
    und das Modell muss es wieder in `scope` und `server_id` zerlegen können,
    um denselben Bereich noch einmal anzusprechen. Hier ist das Gegenteil
    gefragt — ein Satzteil, den ein Mensch versteht, gleich ob er ihn selbst
    als Fehlermeldung liest oder das Modell ihn ihm vorliest. Die
    beiden aus einer Funktion zu bedienen hiesse, an jeder Aufrufstelle einen
    Schalter mitzuführen, und sie ändern sich aus verschiedenen Gründen: das
    Etikett, wenn ein Bereich dazukommt, dieser Name, wenn der Ton der
    Meldungen nicht mehr stimmt. Auch die Form passt nicht zusammen: dort liegt
    eine fertige Zeile vor, hier gibt es noch keine — geschrieben wird ja
    gerade erst.

    Die Sitzung steht in der Signatur wegen des Teams: dessen Name ist das
    einzige Stück dieses Satzes, das nicht schon in den Argumenten liegt, und
    ohne ihn wäre der Satz für ein Team wertlos (siehe unten).
    """
    if scope == "user":
        return "dein persönliches Gedächtnis"
    if scope == "server":
        return f"deine Notizen zu Server {server_id}"
    if scope == "server_shared":
        return f"das Wissen von Server {server_id}"
    if scope == "team":
        # Der Name, nicht die Nummer — und das ist keine Kosmetik. Ein Team
        # sprechen `remember` und `forget_memory` ausschliesslich über
        # `team="<Name>"` an, aufgelöst über Namensgleichheit in
        # `team_service.learning_team`; ein Werkzeug, das eine Nummer in einen
        # Namen übersetzt, gibt es nicht. „Team 42“ benennt für das Modell also
        # nichts, was es ansprechen könnte: die Auflage „nur Einträge aus genau
        # diesem Bereich“ wäre damit nicht befolgbar, und dem Benutzer könnte es
        # den vollen Bereich nur als Nummer nennen.
        #
        # Der Schaden bleibt nicht beim Nichtstun. Schlüssel sind bewusst stabil
        # und wiederholen sich über Teams hinweg — wer den Bereich nicht treffen
        # kann, greift den gleichnamigen Treffer des falschen Teams und löscht
        # dort.
        ziel = db.get(Team, team_id) if team_id is not None else None
        if ziel is not None and ziel.name:
            return f"das Wissen von Team „{ziel.name}“"
        # Kein stiller Notausgang: `scope_identity` hat die Mitgliedschaft in
        # genau diesem Team schon geprüft, die Zeile existiert also. Bliebe sie
        # wider Erwarten aus, ist die Nummer immer noch besser als ein Satz ohne
        # Bereich — dass es um das Team geht und nicht um den eigenen Vorrat,
        # ist die Hälfte der Meldung, und die darf nie fehlen.
        return f"das Wissen von Team {team_id}"
    if scope == "panel":
        return "das panelweite Gedächtnis"
    # Unerreichbar: `scope_identity` hat jeden unbekannten Bereich längst mit
    # 422 abgewiesen. Trotzdem kein `else`-Zweig auf "panel" — ein neuer
    # Bereich, der hier durchrutscht, würde dem Benutzer sonst als das
    # panelweite Gedächtnis angesagt und damit als Sache des Betreibers.
    return "dieser Bereich"


def _sperrzeile(db: Session, identity: str) -> Query:
    """Die eine Zeile, auf der zwei gleichzeitige Schreiber aufeinandertreffen.

    Zwischen der Zählung in `upsert_entry` und dem `db.add()` danach lag einmal
    keine Sperre. Zwei gleichzeitige Läufe mit verschiedenen Schlüsseln — Chat
    und Sprachsitzung sind ausdrücklich möglich — sahen beide denselben Bestand
    und legten beide an; die einzige Datenbankzusage ist der UNIQUE auf
    (scope_identity, key) und greift bei verschiedenen Schlüsseln gar nicht.
    Die Bereichsgrenze war damit eine Bitte, keine Grenze.

    Gesperrt wird dafür **eine** Zeile und nicht der ganze Bereich. Bis
    `MAX_MEMORY_ENTRIES_MAX` auf 5.000 stieg, war das dasselbe in teuer: beide
    Schreiber lesen in derselben Reihenfolge, sie treffen sich also ohnehin an
    der ersten Zeile — wer danach noch 4.999 weitere sperrt, kauft damit keine
    zusätzliche Ausschließlichkeit, nur Arbeit. Gemessen gegen PostgreSQL 17
    (local-plans/mess-bestandssperre.py): alle 5.000 Zeilen sperren kostet
    10–18 ms und 281 KB WAL **je gemerktem Satz**, diese eine Zeile 0,5 ms und
    56 Bytes. Der Rest des Bereichs bleibt dabei schreibbar — der Abrufweg
    zählt Nutzung hoch und schreibt Vektoren nach, und das lief bisher gegen
    die Sperre des Schreibenden.

    Dass der Treffpunkt hält, hängt am `LIMIT` **über** der Sperre: geliefert
    wird die erste Zeile, die sich auch wirklich sperren lässt. Wurde die
    bisher erste gerade gelöscht, rücken alle Wartenden gemeinsam auf die
    nächste. Und eine Zeile, die sich davor einsortiert, kann nur anlegen, wer
    selbst durch diese Sperre gegangen ist — die Reihenfolge ist deshalb kein
    Schmuck, sondern die Zusage.

    Zwei Grenzen, unverändert zu vorher: ein **leerer** Bereich hat keine Zeile
    zum Sperren, dort können zwei erste Einträge nebeneinander entstehen. Und
    auf SQLite (Testsuite) ist `FOR UPDATE` ein No-Op.
    """
    return (
        db.query(AiMemoryEntry.id)
        .filter(AiMemoryEntry.scope_identity == identity)
        .order_by(AiMemoryEntry.id)
        .limit(1)
        .with_for_update()
    )


def _bestand_unter_sperre(db: Session, identity: str) -> int:
    """Wieviele Einträge der Bereich führt — gezählt hinter der Sperre.

    Erst sperren, dann zählen, und diese Reihenfolge ist der eigentliche Punkt.
    Vorher taten beides eine einzige Abfrage, weil `count()` kein `FOR UPDATE`
    verträgt und deshalb die Liste der IDs herhalten musste. Das kostete nicht
    nur — es zählte auch falsch: der wartende Schreiber bekam die Zeilen aus
    dem Schnappschuss von **vor** dem Warten. Gemessen (siehe `_sperrzeile`)
    zählte er 5.001, während in der Datenbank bereits 5.002 standen, und legte
    darauf den 5.003. an. Als eigene Anweisung **nach** der Sperre sieht die
    Zählung den fremden Eintrag; dieselbe Messung ergibt dann 5.001 von 5.001.

    Der Bereich ist bis zum Commit gesperrt, die Zahl gilt also bis dahin.
    """
    _sperrzeile(db, identity).first()
    return int(
        db.query(func.count(AiMemoryEntry.id))
        .filter(AiMemoryEntry.scope_identity == identity)
        .scalar()
        or 0
    )


def upsert_entry(
    db: Session, *, user: User, scope: str, server_id: int | None, key: str, value: str,
    origin: str = "user", team_id: int | None = None, replace_user_entry: bool = False,
) -> tuple[AiMemoryEntry, str]:
    """Legt einen Eintrag an oder ueberschreibt ihn unter demselben Schluessel.

    Das Ueberschreiben ist die Konfliktaufloesung des Gedaechtnisses: "ich will
    jetzt 16 GB" ersetzt "8 GB", statt beides nebeneinander stehen zu lassen.
    Deshalb ist der Schluessel die Identitaet eines Fakts — und deshalb bekommt
    die KI im Werkzeugtext die ausdrueckliche Anweisung, einen vorhandenen
    Schluessel wiederzuverwenden, statt einen fuenften aehnlichen anzulegen.

    ``origin`` unterscheidet eine Ansage des Benutzers von einer Ableitung der
    KI. Eine Ableitung ueberschreibt bewusst **keine** ausdrueckliche Ansage:
    was der Benutzer selbst gesagt hat, darf die KI nicht stillschweigend
    korrigieren.
    """
    if origin not in {"user", "ai"}:
        raise HTTPException(status_code=422, detail="Unbekannte Memory-Herkunft")
    identity, owner_id, normalized_server_id, normalized_team_id = scope_identity(
        db, user, scope, server_id, team_id
    )
    _assert_may_write(db, user, scope, normalized_team_id, normalized_server_id)
    safe_value = _safe_value(value)
    row = db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == identity, AiMemoryEntry.key == key
    ).first()
    action = "ai.memory.updated"
    if row is None:
        # Wieviel hier hineinpasst, entscheidet nicht mehr eine Konstante dieses
        # Moduls, sondern der Betreiber über das Rollenlimit — je nach Bereich
        # das des Schreibenden, das des Teamgründers oder die feste
        # Systemgrenze. Eine zweite Zahl hier daneben wäre eine zweite Wahrheit,
        # die mit der ersten auseinanderläuft.
        #
        # Die Auflösung gibt immer eine Zahl zurück, nie „unbegrenzt“: hat der
        # Betreiber nichts hinterlegt, gilt dort weiterhin die alte 100. Die
        # Zählung läuft deshalb ausnahmslos — und ihr Ergebnis steht in einer
        # Variablen, weil die Meldung es gleich noch braucht.
        grenze = ai_limit_service.resolve_scope_memory_limit(
            db, scope, user, team_id=normalized_team_id, server_id=normalized_server_id,
        )
        bestand = _bestand_unter_sperre(db, identity)
        if bestand >= grenze:
            # Hier steht die **Tatsache**, in Sätzen, die ein Mensch versteht —
            # und nichts sonst. Vorher stand hier eine Regieanweisung an das
            # Modell („Sag dem Benutzer …“, „Suche mit search_memory …“). Diese
            # Funktion hat aber zwei Adressaten: über `routers/ai_memory.py`
            # legt der Benutzer selbst einen Eintrag an, und `detail` wird ihm
            # als Toast vorgesetzt. Er las dort eine Anweisung an eine dritte
            # Instanz, über ihn selbst, mit Werkzeugnamen, die er nicht hat. Ein
            # Text, der beiden dienen soll, dient keinem — was das Modell tun
            # oder lassen soll, steht deshalb in `_execute_remember`, an der
            # Naht zum Modell.
            #
            # Der Bereich steht in jedem der drei Sätze, und das ist keine
            # Höflichkeit: nur er sagt, **wo** es klemmt. „Voll“ ohne Bereich
            # liest sich wie „das Gedächtnis ist voll“ und stimmt dann für jeden
            # anderen Vorrat des Benutzers nicht.
            #
            # Drei Fälle, weil die Auskunft in dreien verschieden ist:
            #
            # 0 — hier passt nichts hinein, und daran ändert kein Aufräumen
            #   etwas. Vom Tarif des Benutzers spricht die Absage bewusst nicht:
            #   bei `scope='team'` kommt die 0 vom Gründer, nicht vom
            #   Schreibenden. Ein Mitglied mit grosszügigem eigenem Limit hörte
            #   sonst, sein Tarif sei schuld — und könnte das durch keinen
            #   Tarifwechsel beheben.
            # genau voll — der Normalfall. Einer geht, einer kommt.
            # zu voll — der Betreiber hat nachträglich gesenkt. Nur dieser Fall
            #   nennt eine Menge, und er nennt sie als Auskunft, nicht als
            #   Auftrag: „einer muss weichen“ wäre hier eine Anleitung zu so
            #   vielen Fehlschlägen, wie der Bereich zu viel hat. Wer entscheidet,
            #   welche gehen, steht bewusst **nicht** mehr dabei. „Welche das
            #   sind, entscheidet der Benutzer“ stand hier bis zuletzt — ein Satz,
            #   der über seinen eigenen Leser hinwegredet, denn dieses `detail`
            #   liest der Benutzer selbst als Toast, und die zwei Sätze davor
            #   duzen ihn. Derselbe Fehler wie die frühere Regieanweisung im
            #   0-Fall, nur eine Stufe leiser. Als Anweisung gebraucht wird der
            #   Gedanke ohnehin nur vom Modell, und dort steht er schon: „Nenne
            #   dem Benutzer den Stand und frag, was weg soll“ in
            #   `_execute_remember`.
            #
            # Verdrängt wird bewusst nichts von selbst: was ein Mensch gesagt
            # hat, wirft das Panel nicht ungefragt weg.
            bereich = _bereichsname(db, scope, normalized_server_id, normalized_team_id)
            if grenze == 0:
                meldung = (
                    f"Für {bereich} ist kein Gedächtnis freigegeben (0 Einträge erlaubt). "
                    "Das entscheidet die Rolle und nicht der Inhalt — Löschen schafft hier "
                    "keinen Platz."
                )
            elif bestand == grenze:
                meldung = (
                    f"Voll — {bereich} führt {bestand} von {grenze} erlaubten Einträgen. "
                    "Einer muss weichen, bevor ein neuer passt."
                )
            else:
                # Nicht `bestand - grenze`: der neue Eintrag will ja auch noch
                # hinein. Bei 21 von 20 wären das sonst „1 muss weichen“ und
                # danach immer noch kein Platz.
                zuviel = bestand - grenze + 1
                meldung = (
                    f"Zu voll — {bereich} führt {bestand} Einträge, erlaubt sind {grenze}. "
                    f"Die Grenze wurde nachträglich gesenkt; {zuviel} müssen weichen."
                )
            raise MemoryScopeVoll(
                bereich=bereich, bestand=bestand, grenze=grenze, detail=meldung
            )
        row = AiMemoryEntry(
            id=str(uuid4()), owner_user_id=owner_id, server_id=normalized_server_id,
            team_id=normalized_team_id,
            scope=scope, scope_identity=identity, key=key, value_encrypted="",
            origin=origin, aad_version=2,
        )
        db.add(row)
        action = "ai.memory.created"
    elif origin == "ai" and row.origin == "user" and not replace_user_entry:
        # Der Schutz gilt gegen die *stillschweigende* Korrektur: die KI leitet
        # nebenbei etwas ab und ueberschreibt damit, was der Benutzer selbst
        # gesagt hat. Verlangt er die Korrektur ausdruecklich, ist genau das
        # erwuenscht — dafuer gibt es `replace_user_entry`.
        raise HTTPException(
            status_code=409,
            detail=(
                "Dieser Eintrag stammt vom Benutzer. Ueberschreibe ihn nur, wenn "
                "der Benutzer die Korrektur ausdruecklich verlangt hat — dann mit "
                "replace_user_entry=true. Lege keinen zweiten aehnlichen Schluessel an."
            ),
        )
    else:
        # Eine ausdrueckliche Ansage bleibt eine ausdrueckliche Ansage.
        # Vorher stand hier bedingungslos `row.origin = origin`: eine einmal vom
        # Benutzer verlangte Korrektur (`replace_user_entry`) stufte den Eintrag
        # dauerhaft auf "ai" herunter — und der Schutz im Zweig darueber haengt
        # genau an diesem Feld. Er galt danach fuer immer nicht mehr, die
        # naechste beilaeufige Ableitung der KI durfte den Wert wieder
        # stillschweigend ersetzen. Das ist das Gegenteil dessen, was der
        # Docstring zusichert.
        #
        # Nur diese eine Richtung ist gesperrt. Die Hochstufung "ai" -> "user"
        # bleibt erlaubt: wer eine Ableitung selbst bestaetigt, macht sie damit
        # zu seiner eigenen Ansage und sie verdient den Schutz.
        if not (origin == "ai" and row.origin == "user"):
            row.origin = origin
    # Jeder Schreibvorgang hebt den Eintrag auf die gebundene AAD. Bestandsdaten
    # aus Phase C wandern damit von selbst mit, sobald sie angefasst werden —
    # ohne Migrationsschritt, der den DIS-Sidecar voraussetzt.
    row.aad_version = 2
    row.value_encrypted = DisClient.encrypt(safe_value, aad=_aad(row))
    # Der Vektor entsteht aus dem Klartext, bevor er verschluesselt wird —
    # danach waere er nicht mehr zu haben, ohne erneut zu entschluesseln.
    refresh_embedding(row, safe_value)
    row.updated_at = datetime.now(timezone.utc)
    audit_service.record_privileged_action(
        db, user_id=user.id, action=action, target_type="ai_memory", target_id=row.id,
        # Die Servernummer gehoert ins Protokoll, sobald der Bereich sie hat.
        # Ohne sie stuende dort "jemand hat Anlagenwissen geaendert" und nicht
        # bei welcher Anlage — und genau das ist die Frage, die ein Betreiber
        # spaeter stellt.
        details={
            "scope": scope, "origin": origin,
            **({"server_id": normalized_server_id} if normalized_server_id else {}),
        },
        origin="ai" if origin == "ai" else "direct",
    )
    try:
        db.commit()
    except IntegrityError as exc:
        # Zwei parallele Schreibvorgaenge auf denselben (scope, key). Die
        # UNIQUE-Bedingung hat den Verlierer abgewiesen. Das ist ein
        # verstaendlicher Konflikt und kein Serverfehler: der naechste Versuch
        # findet die Zeile vor und nimmt den Update-Zweig.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Memory-Eintrag wurde parallel geaendert. Bitte erneut versuchen.",
        ) from exc
    db.refresh(row)
    return row, safe_value


def aehnlicher_eintrag(
    db: Session,
    *,
    scope_kennung: str,
    key: str,
    value: str,
    schwelle: float = DUPLIKAT_AB,
) -> tuple[AiMemoryEntry, float] | None:
    """Der naechstliegende Bestandseintrag im selben Bereich — oder ``None``.

    **Wozu:** Das Ueberschreiben ueber den Schluessel loest Konflikte nur,
    wenn derselbe Schluessel wiedergefunden wird. Legt die KI stattdessen
    einen aehnlichen neuen an, stehen zwei Antworten auf dieselbe Frage
    nebeneinander — und beim naechsten Abruf gewinnt der Zufall.

    Verglichen wird **Schluessel und Wert** zusammen, wie beim Einbetten
    (`_embedding_source`). Der Schluessel allein truege zu wenig ("ram" gegen
    "speicher"), der Wert allein zu viel Rauschen.

    Ohne Modell gibt die Funktion ``None`` zurueck statt zu raten. Ein
    Wortabgleich als Ersatz waere hier gefaehrlich: zwei Eintraege ueber
    denselben Server teilen fast alle Woerter, ohne dasselbe zu sagen.

    Sucht **nur im eigenen Bereich** (`scope_kennung`). Ein persoenlicher
    Eintrag darf nie gegen einen fremden oder geteilten geprueft werden — das
    waere ein Leseweg ueber die Bereichsgrenze hinweg, und sei es nur ueber
    ein Aehnlichkeitsmass.
    """
    vektoren = ai_embedding_service.encode([_embedding_source(key, value)])
    if not vektoren:
        return None

    # Vektor und Zeile zusammen halten: die Filterung oben hat `None`
    # ausgeschlossen, aber der Typpruefer sieht das nicht — und ein zweites
    # `_stored_vector` je Zeile wäre ein zweites Lesen der Vektorspalte.
    paare: list[tuple[AiMemoryEntry, Sequence[float]]] = []
    for row in db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == scope_kennung,
        AiMemoryEntry.key != key,
    ).all():
        vektor = _stored_vector(row)
        if vektor is not None:
            paare.append((row, vektor))
    if not paare:
        return None

    werte = ai_embedding_service.similarity(
        vektoren[0], [vektor for _row, vektor in paare]
    )
    bester: tuple[AiMemoryEntry, float] | None = None
    for (row, _vektor), wert in zip(paare, werte):
        if wert >= schwelle and (bester is None or wert > bester[1]):
            bester = (row, float(wert))
    return bester


def delete_entry(db: Session, user: User, entry_id: str) -> None:
    try:
        canonical = str(UUID(entry_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=404, detail="Memory-Eintrag nicht gefunden") from exc
    row = db.get(AiMemoryEntry, canonical)
    if row is None:
        raise HTTPException(status_code=404, detail="Memory-Eintrag nicht gefunden")
    if row.scope == "panel":
        allowed = permission_service.has_global_permission(db, user, "panel.settings.write")
    elif row.scope == "team":
        from services import team_service

        allowed = row.team_id is not None and team_service.can_manage_team_memory(
            db, user, row.team_id
        )
    elif row.scope == "server_shared":
        # Ein eigener Zweig und nicht der `else` darunter. Dort steht
        # `row.owner_user_id == user.id`, und Anlagenwissen hat bewusst keinen
        # Besitzer: der Vergleich waere gegen NULL immer False, der Eintrag
        # damit fuer **niemanden** loeschbar — auch nicht fuer den Betreiber.
        #
        # Und auch die Begruendung des `else` traegt hier nicht: sie gilt dem
        # eigenen Eintrag, den man behalten koennen soll, wenn der Serverzugang
        # wegfaellt. Fremdes Wissen zu loeschen, das man nicht mehr sehen darf,
        # ist das Gegenteil davon.
        allowed = row.server_id is not None and permission_service.has_server_permission(
            db=db, user=user, server_id=row.server_id, key="server.config.write"
        )
    else:
        # Der eigene Eintrag, ohne zusaetzliche Serverbedingung. Vorher verlangte
        # eine serverbezogene Notiz weiterhin `server.view` — wer den Zugriff auf
        # einen Server verlor, konnte seine eigene Notiz dazu nicht mehr
        # loeschen. Sie blieb in der Datenbank, zaehlte gegen sein Kontingent
        # und war fuer ihn unerreichbar. Was gelesen wird, entscheidet weiterhin
        # `_visible_scope_rows` mit `server.view`; das ist eine andere Frage.
        allowed = row.owner_user_id == user.id
    if not allowed:
        raise HTTPException(status_code=404, detail="Memory-Eintrag nicht gefunden")
    audit_service.record_privileged_action(
        db, user_id=user.id, action="ai.memory.deleted", target_type="ai_memory",
        target_id=row.id,
        details={
            "scope": row.scope,
            **({"server_id": row.server_id} if row.server_id else {}),
        },
        origin="direct",
    )
    db.delete(row)
    db.commit()


def delete_all_entries(
    db: Session, user: User, scope: str, server_id: int | None = None,
    team_id: int | None = None,
) -> int:
    """Leert einen ganzen Bereich. Gibt die Zahl der geloeschten Eintraege zurueck.

    Ohne das musste man ein gewachsenes Gedaechtnis Zeile fuer Zeile abraeumen —
    bei dreissig abgeleiteten Eintraegen dreissig Bestaetigungen. Wer sein
    Gedaechtnis loeschen will, will es ganz loeschen.

    Die Berechtigung entsteht **nicht** neu, sondern aus denselben zwei Quellen
    wie beim einzelnen Loeschen: `scope_identity` entscheidet, welche Zeilen
    ueberhaupt sichtbar sind (bei `user` nur die eigenen, bei `team` nur die des
    Teams, in dem man Mitglied ist), und `_assert_may_write` entscheidet, ob man
    einen geteilten Bereich veraendern darf. Persoenliche Eintraege verlassen
    den Benutzer nie und brauchen keine zweite Pruefung.

    Ein Audit-Eintrag mit der Anzahl statt einem je Zeile: dreissig gleichartige
    Zeilen im Protokoll verdecken die Handlung, statt sie zu belegen.
    """
    # Die **normalisierten** Werte weitergeben und nicht die rohen. Vorher stand
    # hier `team_id` direkt aus der Anfrage; bei einem Bereich, dessen Kennung
    # aus etwas anderem entsteht, laeuft die Rechtepruefung damit gegen einen
    # anderen Gegenstand als das Loeschen darunter.
    identity, _, normalized_server_id, normalized_team_id = scope_identity(
        db, user, scope, server_id, team_id
    )
    _assert_may_write(db, user, scope, normalized_team_id, normalized_server_id)
    rows = db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == identity
    ).all()
    if not rows:
        return 0
    for row in rows:
        db.delete(row)
    audit_service.record_privileged_action(
        db, user_id=user.id, action="ai.memory.cleared", target_type="ai_memory",
        target_id=None,
        details={
            "scope": scope, "count": len(rows),
            **({"server_id": normalized_server_id} if normalized_server_id else {}),
        },
        origin="direct",
    )
    db.commit()
    return len(rows)


def _tokens(text: str) -> set[str]:
    """Zerlegt Text in vergleichbare Wortstaemme.

    Bewusst simpel: Kleinschreibung, alles Nicht-Alphanumerische trennt, kurze
    Fuellwoerter fliegen raus. Das ist **keine** semantische Aehnlichkeit — es
    ist ein Wortabgleich und funktioniert nur innerhalb derselben Sprache.
    Genau deshalb ist er unten nur ein Kriterium von dreien und entscheidet nie
    allein.
    """
    return {word for word in _WORD_RE.findall(text.lower()) if len(word) > 2}


def _reiz(similarity: float | None, overlap: int) -> float:
    """Wie stark die aktuelle Frage einen Eintrag trifft — zwischen 0.0 und 1.0.

    Steht als eigene Funktion da, weil derselbe Wert an zwei Stellen gebraucht
    wird und dieselbe Zahl sein muss: `abrufstaerke` benutzt ihn als Untergrenze
    für die Darstellung, und `provider_memory_context` entscheidet an ihm, ob
    ein Eintrag als **gebraucht** vermerkt wird. Zwei getrennt gepflegte
    Fassungen hießen, dass ein Eintrag voll gezeigt wird, ohne als benutzt zu
    gelten — oder umgekehrt.
    """
    # `similarity` liegt in [-1, 1]; negativ heißt "hat nichts miteinander zu
    # tun" und darf nicht als Beitrag zählen.
    reiz = max(0.0, similarity) if similarity is not None else 0.0
    if overlap:
        # Wortüberlappung ist ein gröberes, aber sehr sicheres Signal — im
        # Gameserver-Umfeld stehen Lehnwörter (Backup, RAM, Ports) wörtlich
        # in deutschen Einträgen. Ein Treffer hebt auf mindestens die Hälfte,
        # zwei auf volle Präsenz.
        reiz = max(reiz, min(1.0, 0.5 + 0.25 * overlap))
    return reiz


def abrufstaerke(
    row: AiMemoryEntry,
    now: datetime,
    similarity: float | None = None,
    overlap: int = 0,
) -> float:
    """Wie praesent ein Eintrag gerade ist — zwischen 0.0 und 1.0.

    **Der Unterschied zu `_bewertung`:** jene ordnet Eintraege
    *untereinander*, wenn zu wenig Platz ist. Diese sagt fuer einen einzelnen
    Eintrag, wie stark er *an sich* gerade abrufbar ist — unabhaengig davon,
    wie viele andere es gibt. Deshalb ist sie normiert und nicht offen nach
    oben; nur so laesst sich eine feste Schwelle (`VERBLASSEN_AB`) daran
    haengen.

    Drei Beitraege, und die Reihenfolge ist Absicht:

    * **Der Reiz** (`similarity`, `overlap`). Er sticht alles. Trifft die
      aktuelle Frage einen Eintrag, ist er sofort voll da — egal wie lange er
      geschlafen hat. Das ist die Haelfte, die aus "vergessen" ein
      "verblasst" macht: nichts ist weg, es liegt nur weiter hinten, bis es
      gebraucht wird.
    * **Vertrautheit** (`use_count`). Was oft gebraucht wurde, bleibt praesent,
      auch ohne Reiz. Beim Menschen dasselbe: die eigene Telefonnummer faellt
      einem ein, ohne dass jemand danach fragt.
    * **Frische** (Zeit seit dem letzten Gebrauch). Der schwaechste Anteil,
      und bewusst so: **Alter allein soll nicht verblassen lassen.** Ein
      Eintrag von vor einem Jahr, der jede Woche gebraucht wird, ist praesent;
      ein Eintrag von gestern, den nie jemand abgerufen hat, ist es nicht. Der
      Betreiber hat genau darauf bestanden ("nicht nur fuer aeltere Eintraege,
      sondern auch Eintraege, die nicht so oft genutzt werden").

    Gemessen wird ab dem **letzten Gebrauch**, nicht ab dem Anlegen: ein
    Eintrag, der regelmaessig zum Zug kommt, altert gar nicht.
    """
    reiz = _reiz(similarity, overlap)

    vertrautheit = min(row.use_count or 0, 20) / 20.0

    referenz = row.last_used_at or row.updated_at or row.created_at
    alter_tage = max(0.0, (now - _utc(referenz)).total_seconds() / 86_400)
    # Der Neuheitsschutz verschiebt die Kurve, statt sie zu skalieren: die
    # ersten Tage kosten gar nichts, danach faellt es wie gehabt.
    wirksames_alter = max(0.0, alter_tage - NEUHEITSSCHUTZ_TAGE)
    frische = 1.0 / (1.0 + wirksames_alter / RECENCY_HALFLIFE_DAYS)

    # Der Reiz steht **nicht** in der Summe, sondern als Untergrenze daneben.
    # In einer Summe koennte ein blasser Eintrag trotz perfektem Treffer unter
    # der Schwelle bleiben, weil ihm Nutzung und Frische fehlen — und genau
    # dieser Fall ist der, um den es geht.
    ruhewert = vertrautheit * 0.55 + frische * 0.45
    return max(reiz, ruhewert)


def _relevance(
    row: AiMemoryEntry,
    value: str,
    query_tokens: set[str],
    now: datetime,
    similarity: float | None = None,
) -> float:
    """Bewertet einen Eintrag fuer die aktuelle Frage.

    Vier Anteile, die absichtlich verschiedene Dinge messen:

    - **Bedeutung** (Vektoraehnlichkeit). Das einzige Kriterium, das ueber
      Sprachgrenzen traegt: "quel jeu je prefere" findet "lieblingsspiel", wo
      Wortabgleich null liefert.
    - **Bezug zur Frage** (Wortueberlappung). Bleibt trotzdem drin, weil im
      Gameserver-Umfeld die halbe Fachsprache aus Lehnwoertern besteht: Backup,
      RAM, Mods, Ports stehen woertlich in deutschen Eintraegen. Gemessen
      erkennt der Wortabgleich diese Faelle sicherer als das statische
      Embedding — die beiden Signale ergaenzen sich.
    - **Nutzung.** Was oft abgerufen wurde, ist erfahrungsgemaess wichtig —
      aber nur als Ausschlag bei Gleichstand. Sie wiegt höchstens ihr Gewicht,
      genau wie die anderen Anteile; warum das ausdrücklich dasteht, erklärt
      `_bewertung`.
    - **Aktualitaet.** Frisch Gemerktes gewinnt gegen Altes, das nie gebraucht
      wurde — sonst kaeme ein neuer Eintrag nie zum Zug, weil ihm die
      Nutzungshistorie fehlt.

    ``similarity`` ist ``None``, wenn kein Modell geladen ist oder der Eintrag
    noch keinen Vektor hat. Dann entscheiden die drei uebrigen Kriterien; der
    Eintrag faellt nicht heraus.
    """
    return _bewertung(row, f"{row.key} {value}", query_tokens, now, similarity)


#: Die vier Gewichte von `_bewertung`: Bedeutung, Bezug zur Frage, Nutzung,
#: Aktualitaet — einmal fuer die Auswahl **mit** Klartext und einmal fuer die
#: Vorauswahl davor.
#:
#: **Es bleibt eine Formel.** Was wechselt, sind die Gewichte, nicht die
#: Rechnung; der Docstring von `_bewertung` erklaert, warum zwei getrennt
#: gepflegte Kopien hier besonders tueckisch waeren.
#:
#: **Warum die Vorauswahl die Nutzung nicht mitzaehlt.** Gemessen am 19.08.2026
#: an 5.000 Eintraegen und zehn Fragen mit bekannter Antwort: mit den Gewichten
#: der Auswahl ueberlebten 4 von 10 gesuchten Eintraegen den Schnitt auf
#: `MAX_CONTEXT_ROWS` — ohne den Nutzungsterm 6, ohne Nutzung und mit doppelter
#: Bedeutung 7. Der Grund steht in den Groessenordnungen: bei 5.000 Zeilen liegt
#: die Punktschwelle des 300. Platzes bei 10,31 bis 11,18, und Nutzung (bis
#: 10,0) und Aktualitaet (bis 2,0) bilden sie allein. Die Bedeutung bringt
#: gegen diese Schwelle hoechstens 6,0 und real 1,03 bis 3,91 — ein perfekter
#: Bedeutungstreffer reichte also nicht gegen eine oft gebrauchte Zeile, die mit
#: der Frage nichts zu tun hat. Bei hundert Eintraegen fiel das nicht auf, weil
#: da noch alles mitging; die Formel skaliert nicht mit der Menge, sie kippt.
#:
#: **Warum die Aktualitaet trotzdem stehenbleibt.** Ohne den Nutzungsterm kann
#: sie die Bedeutung nicht mehr ueberstimmen (2,0 gegen 12,0). Dafuer ist sie
#: der einzige Anteil, der Zeilen **ohne** Vektor noch sinnvoll ordnet — ohne
#: geladenes Modell ist die Bedeutung fuer jede Zeile 0,0 und der Wortbezug
#: sieht nur den Schluessel. Genau das sichert `_vorauswahl` zu: wer noch nie
#: eingebettet wurde, soll nicht schon deshalb herausfallen. Die reine
#: Bedeutung haette 8 von 10 gerettet und diese Zusage aufgegeben.
#:
#: **Und warum die Nutzung nach dem Entschluesseln bleibt.** Sie ist dort
#: richtig: das Feld ist klein, der Klartext liegt vor, und sie ist der einzige
#: sprachunabhaengige Anteil, wenn jemand auf Englisch nach deutschen Notizen
#: fragt. Falsch war allein, sie darueber entscheiden zu lassen, was die KI
#: ueberhaupt zu sehen bekommt.
#:
#: **Nachgemessen am 19.08.2026 — sie entschied es trotzdem.** Die Vorauswahl
#: lieferte 7 von 10 gesuchten Einträgen ab, im fertigen Block standen aber nur
#: 5: zwei Ziele, die die Vorauswahl auf Platz 1 gesetzt hatte, fielen hier auf
#: Rang 74 von 300 und damit aus dem Budget. Der Grund lag nicht im Gewicht,
#: sondern im Maßstab — die Nutzung ging als rohe Zahl 0 bis 20 in die Summe,
#: während Bedeutung und Aktualität zwischen 0 und 1 liegen. Seit sie wie in
#: `abrufstaerke` normiert wird (Begründung in `_bewertung`), stehen 7 von 10
#: im Block, und die geretteten Ziele stehen dort auf Rang 1 statt 74. Die vier
#: Zahlen unten sind deshalb unverändert: gemessen wurde ein Rechenfehler, keine
#: Geschmacksfrage. Wer stattdessen an ihnen dreht, dreht an einer Zahl, die
#: zwischen 0,0 und 0,3 dasselbe Ergebnis liefert — auch das gemessen.
GEWICHTE_AUSWAHL = (6.0, 3.0, 0.5, 2.0)
GEWICHTE_VORAUSWAHL = (12.0, 3.0, 0.0, 2.0)


def _bewertung(
    row: AiMemoryEntry,
    text: str,
    query_tokens: set[str],
    now: datetime,
    similarity: float | None = None,
    gewichte: tuple[float, float, float, float] = GEWICHTE_AUSWAHL,
) -> float:
    """Die Formel hinter `_relevance` — mit dem Vergleichstext als Parameter.

    Sie steht getrennt, weil es zwei Zeitpunkte gibt, an denen bewertet wird,
    und nur einer davon den Klartext hat: `_vorauswahl` laeuft **vor** der
    Entschluesselung und kann nur `row.key` beisteuern, `_relevance` laeuft
    danach und hat Schluessel *und* Wert. Zwei getrennt gepflegte Formeln waeren
    hier besonders tueckisch — die Vorauswahl entschiede dann nach anderen
    Massstaeben, als die Auswahl unmittelbar danach anlegt, und die Zeile fiele
    in der ersten Runde heraus, die in der zweiten gewonnen haette.

    Genau deshalb sind die Gewichte ein Parameter und keine zweite Kopie: die
    beiden Zeitpunkte wiegen verschieden schwer (`GEWICHTE_VORAUSWAHL` gegen
    `GEWICHTE_AUSWAHL`, Begruendung dort), aber sie rechnen dasselbe.
    """
    w_bedeutung, w_bezug, w_nutzung, w_aktualitaet = gewichte
    overlap = len(query_tokens & _tokens(text))
    reference = row.last_used_at or row.updated_at or row.created_at
    age_days = max(0.0, (now - _utc(reference)).total_seconds() / 86_400)
    recency = 1.0 / (1.0 + age_days / RECENCY_HALFLIFE_DAYS)
    # Negative Aehnlichkeit heisst "hat nichts miteinander zu tun" und darf
    # einen Eintrag nicht unter einen ohne Vektor druecken.
    meaning = max(0.0, similarity) if similarity is not None else 0.0
    # **Die Nutzung wird normiert wie in `abrufstaerke`, und das ist der Punkt.**
    # Bedeutung und Aktualität liegen zwischen 0 und 1; das Gewicht daneben sagt
    # also, wieviel dieser Anteil höchstens wiegen darf. Die Nutzung stand hier
    # als rohe Zahl von 0 bis 20 — mit demselben Gewicht 0,5 waren das bis zu
    # 10,0 Punkte gegen höchstens 6,0 aus der Bedeutung, und die Zahl hatte mit
    # der Frage nichts zu tun. Gemessen am 19.08.2026 an 5.000 Einträgen: von
    # den 65 Zeilen, die ins Budget passten, standen 65 überwiegend wegen ihrer
    # Nutzung dort, und zwei der zehn gesuchten Antworten fielen deshalb heraus,
    # obwohl die Vorauswahl sie auf Platz 1 gesetzt hatte.
    #
    # Nicht das Gewicht ist gefallen, sondern der Maßstab: durch 20 geteilt
    # wiegt die Nutzung wie jeder andere Anteil höchstens ihr Gewicht. Sie
    # bleibt damit, was sie sein sollte — ein Ausschlag bei Gleichstand, der
    # auch dann noch trägt, wenn jemand auf Englisch nach deutschen Notizen
    # fragt und weder Wortabgleich noch Vektor etwas hergeben.
    familiarity = min(row.use_count or 0, 20) / 20.0
    return (
        meaning * w_bedeutung
        + overlap * w_bezug
        + familiarity * w_nutzung
        + recency * w_aktualitaet
    )


def _vorauswahl(
    rows: list[AiMemoryEntry],
    query: str,
    now: datetime,
    limit: int,
) -> tuple[list[AiMemoryEntry], bool]:
    """Kuerzt die Zeilenmenge, **bevor** sie entschluesselt wird.

    Der Trick liegt darin, dass die Rangfolge den Klartext fast nicht braucht:
    `_similarities` liest den gespeicherten Vektor von der Zeile, die
    Aktualitaet steht als Spalte daneben, und `row.key` ist ohnehin Klartext —
    verschluesselt ist allein der Wert. Nur die Wortueberlappung sieht hier den
    Schluessel statt Schluessel und Wert.

    Das ist der Preis, und er ist der richtige: die Alternative waere, alles zu
    entschluesseln, um es bewerten zu koennen — also genau der Aufwand, gegen
    den dieser Deckel steht. Und er faellt nur an, wo er kaum wiegt: unterhalb
    von ``limit`` gibt die Funktion die Liste **unveraendert** zurueck, nicht
    einmal neu sortiert.

    **Was hier bewusst nicht mitzaehlt, ist die Nutzung.** Sie stuende als
    Spalte bereit, aber diese Stufe entscheidet, was die KI ueberhaupt zu sehen
    bekommt, und dafuer taugt nur, was mit der *Frage* zu tun hat. Gemessen
    ueberlebten mit ihr 4 von 10 gesuchten Eintraegen den Schnitt, ohne sie 7 —
    die Begruendung mit allen Zahlen steht an `GEWICHTE_VORAUSWAHL`.

    Hat ein Eintrag keinen Vektor, liefert `_similarities` fuer ihn ``None``.
    Das heisst dort ausdruecklich "kein Vergleich moeglich" und nicht
    "unaehnlich" — er wird nach den uebrigen Kriterien bewertet und faellt
    nicht schon deshalb heraus, weil er noch nie eingebettet wurde.

    Der zweite Rueckgabewert sagt, ob gekuerzt wurde. Er gehoert in dasselbe
    `truncated`-Kennzeichen wie der Budgetschnitt: dass das Modell nicht alles
    sieht, muss im Block stehen, egal an welcher der beiden Engstellen es
    weggefallen ist.
    """
    if len(rows) <= limit:
        return rows, False
    query_tokens = _tokens(query)
    scores = _similarities(query, rows)
    ranked = sorted(
        zip(rows, scores),
        key=lambda paar: _bewertung(
            paar[0], paar[0].key, query_tokens, now, paar[1], GEWICHTE_VORAUSWAHL
        ),
        reverse=True,
    )
    return [row for row, _score in ranked[:limit]], True


def _stored_vector(row: AiMemoryEntry) -> Sequence[float] | None:
    """Liest den gespeicherten Vektor, wenn er zum aktuellen Modell passt.

    Zwei Spalten, eine Wahrheit. Geschrieben wird seit dem 19.08.2026 als
    float32-Bytes, und von dort wird zuerst gelesen; ``embedding_json`` ist der
    Rückfall für Bestandszeilen. Beide Formen tragen dieselben Zahlen —
    unterschiedlich ist nur, was das Lesen kostet: 4 ms gegen 381 ms bei 5.000
    Einträgen.

    Der Rückfall bleibt, bis die Migration `20260819_01` überall gelaufen
    ist. Zwischen dem Einspielen des Codes und diesem Lauf liegen bei jedem
    Betreiber ein paar Sekunden, und in denen fände ein Gedächtnis ohne
    Rückfall zu keiner Frage mehr etwas.

    Dass eine beschädigte Byteszeile ebenfalls auf JSON zurückfällt, ist
    Absicht und keine Nachlässigkeit: beide Spalten beschreiben denselben
    Text, also ist die unversehrte von beiden die richtige Antwort.
    """
    if row.embedding_model != _EMBEDDING_MODEL_TAG:
        return None
    vektor = ai_embedding_service.bytes_zu_vektor(row.embedding_bytes)
    if vektor is not None:
        return vektor
    if not row.embedding_json:
        return None
    try:
        alt = json.loads(row.embedding_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(alt, list) or len(alt) != EMBEDDING_DIMENSIONS:
        return None
    return alt


def _vektor_setzen(row: AiMemoryEntry, vektor: Sequence[float] | None) -> None:
    """Schreibt den Vektor einer Zeile — in genau einer Form.

    ``embedding_json`` wird dabei immer geleert, auch wenn gar nichts
    geschrieben wird. Das ist dieselbe Regel, die `refresh_embedding` schon
    für das Verwerfen begründet: eine Zeile trägt einen Vektor, und zwar den
    zu ihrem aktuellen Text. Bliebe der alte JSON-Stand daneben stehen,
    beschriebe er nach einer Berichtigung den *alten* Text — und der Rückfall
    in `_stored_vector` griffe genau dann darauf zurück, wenn die Bytes einmal
    fehlen.
    """
    row.embedding_bytes = (
        None if vektor is None else ai_embedding_service.vektor_zu_bytes(vektor)
    )
    row.embedding_json = None
    row.embedding_model = None if vektor is None else _EMBEDDING_MODEL_TAG


def _embedding_source(key: str, value: str) -> str:
    """Der Text, aus dem der Vektor entsteht.

    Schluessel und Wert zusammen: der Schluessel traegt oft das Stichwort
    ("zeitzone"), der Wert den Inhalt. Punkte und Unterstriche werden zu
    Leerzeichen, damit `backup.zeitpunkt` als zwei Woerter gelesen wird.
    """
    readable_key = key.replace(".", " ").replace("_", " ").replace("-", " ")
    return f"{readable_key}: {value}"


def refresh_embedding(row: AiMemoryEntry, value: str) -> None:
    """Berechnet den Vektor eines Eintrags neu, falls ein Modell da ist.

    Schlägt es fehl, wird ein alter Vektor **verworfen** und der Eintrag eben
    ohne Bedeutungsanteil bewertet. Ein Gedächtniseintrag darf nicht daran
    scheitern, dass ein Modell fehlt.

    Das Verwerfen ist der Punkt: bliebe der alte Vektor stehen, beschriebe er
    dauerhaft den *alten* Text. Ein von "Minecraft" auf "Factorio" berichtigter
    Eintrag würde bei knappem Platz weiterhin für Minecraft-Fragen hochgezogen.
    ``None`` heißt laut Modell "noch nicht berechnet"; `_stored_vector` kommt
    damit zurecht — und `_vektoren_nachziehen` holt es beim nächsten Abruf in
    den Kontext nach, sobald wieder ein Modell da ist.
    """
    vectors = ai_embedding_service.encode([_embedding_source(row.key, value)])
    _vektor_setzen(row, vectors[0] if vectors else None)


def _vektoren_nachziehen(decoded: list[tuple[AiMemoryEntry, str]]) -> None:
    """Berechnet fehlende Vektoren nach, solange der Klartext ohnehin vorliegt.

    `refresh_embedding` verwirft den Vektor, wenn `encode` beim Schreiben nichts
    liefert — richtig, denn ein stehengebliebener Vektor beschriebe danach den
    *alten* Text. Falsch war allein, dass es nie jemand nachholte: eine
    Ausfallphase des Modells (abgebrochener Download, zu wenig Speicher, ein
    einzelner Stolperer) machte jeden in dieser Zeit geschriebenen Eintrag
    **dauerhaft** blind für Bedeutungsrang und Verblassen-Reiz, denn
    `upsert_entry` ist der einzige Aufrufer und niemand fasst die Zeile je
    wieder an.

    Kein Hintergrundlauf, kein Skript, kein Kommando: nachgezogen wird genau
    dort, wo der entschlüsselte Wert schon in der Hand liegt — beim Abruf in
    den Kontext. Damit holt die erste Anfrage nach einem Neustart mit
    funktionierendem Modell alles auf, und zwar höchstens einmal je Zeile.
    Ein Aufruf für alle offenen Zeilen, wie `aehnlicher_eintrag` es auch tut.

    Bewusst nur an dieser einen Stelle: `list_entries`, `scope_entries` und
    `personal_entries` sind Verwaltungsansichten, dort gehört kein Rechenschritt
    hin — auch nicht, seit die beiden letzten seitenweise laden und damit eine
    überschaubare Menge vor sich haben. Fehlt das Modell weiterhin, passiert
    schlicht nichts.
    """
    offen = [(row, value) for row, value in decoded if _stored_vector(row) is None]
    if not offen:
        return
    vektoren = ai_embedding_service.encode(
        [_embedding_source(row.key, value) for row, value in offen]
    )
    # Die Längenprüfung ist keine Formsache: käme weniger zurück als
    # hineingegeben, schriebe das `zip` den Vektor der einen Zeile an die
    # andere — eine falsche Bedeutung unter dem richtigen Schlüssel.
    if not vektoren or len(vektoren) != len(offen):
        return
    for (row, _value), vektor in zip(offen, vektoren):
        _vektor_setzen(row, vektor)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _visible_scope_rows(
    db: Session, user: User, *, persoenlich: bool = True
) -> list[AiMemoryEntry]:
    """Alle Eintraege, die dieser Benutzer gerade sehen darf.

    Vier Bereiche, drei Sichtbarkeitsregeln:

    - **panelweit** und **eigene** immer.
    - **serverbezogen** nur fuer Server, die der Benutzer *jetzt* sehen darf —
      verliert er den Zugriff, verschwindet auch seine Notiz dazu aus dem
      Kontext. Sie kommen bewusst alle mit, nicht die eines bestimmten Servers:
      der Assistent hat seit dem Einzelchat keinen festen Serverbezug mehr.
    - **teambezogen** fuer die Teams, in denen der Benutzer *jetzt* Mitglied
      ist. Der Austritt wirkt damit sofort, ohne dass jemand Eintraege
      nachpflegen muss.

    ``persoenlich=False`` laesst `user` und `server` weg. Beides gehoert dem
    Benutzer und haengt an seiner Einwilligung; `team` und `panel` gehoeren dem
    Team beziehungsweise dem Betreiber und haengen an Mitgliedschaft und
    Betreiberentscheidung. Vorher war das ein Schalter fuer alles: wer sein
    eigenes Gedaechtnis abschaltete, nahm dem Assistenten unbemerkt auch das
    Wissen seiner Teams.

    Die Abfrage filtert ueber `scope_identity` beziehungsweise `team_id` — nie
    ueber ein Kennzeichen im Text. Das ist die Stelle, an der die Trennung
    zwischen zwei Benutzern tatsaechlich stattfindet.

    Serverbezogene Zeilen werden zusaetzlich schon in der Abfrage auf die
    sichtbaren Server begrenzt. Das ist eine Mengenbegrenzung, keine zweite
    Rechtepruefung — die Autoritaet bleibt die Schleife unten.
    """
    from services import team_service

    team_ids = team_service.user_team_ids(db, user)
    # Welche Server dieser Benutzer gerade sehen darf. Dieselbe Menge, die
    # `list_my_servers` zeigt — die Funktion ist die vorhandene Antwort auf
    # genau diese Frage und liegt bereits `list_visible_servers` zugrunde.
    #
    # Drei Rueckgabefaelle, und die Unterscheidung ist der ganze Punkt:
    # `None` heisst **alle** (Owner oder eine Rolle mit pauschalem
    # `server.view`), `[]` heisst **keinen**, eine Liste heisst genau diese.
    # Ein `if sichtbare:` statt der Fallunterscheidung machte aus "sieht
    # nichts" ein "sieht alles".
    #
    # Der Vorfilter ist eine Mengenbegrenzung, keine Rechtepruefung: die
    # zeilenweise Nachpruefung unten bleibt die Autoritaet. Vorher stand hier
    # gar keine Begrenzung, und die Schleife fragte fuer *jede* serverbezogene
    # Zeile einzeln nach — bei einem Betreiber mit vielen Servern eine Abfrage
    # je Zeile und Chatnachricht.
    sichtbare = permission_service.list_visible_server_ids(db, user)

    def _serverbezogen(*bedingungen):
        """Dieselbe Mengenbegrenzung fuer jeden serverbezogenen Bereich."""
        if sichtbare is None:
            return and_(*bedingungen)
        if not sichtbare:
            return None
        return and_(*bedingungen, AiMemoryEntry.server_id.in_(sichtbare))

    conditions = [AiMemoryEntry.scope_identity == "panel"]
    # Anlagenwissen haengt **nicht** am persoenlichen Einwilligungsschalter.
    # Derselbe Gedanke wie bei `team` und `panel`: der Schalter ist eine
    # Entscheidung ueber das eigene Gedaechtnis, nicht ueber das der Kollegen.
    # Wer ihn ausschaltet, soll nicht nebenbei die Betriebsanleitung seines
    # Servers verlieren — die hat er nicht angelegt und kann sie nicht ersetzen.
    anlagenwissen = _serverbezogen(AiMemoryEntry.scope == "server_shared")
    if anlagenwissen is not None:
        conditions.append(anlagenwissen)
    if persoenlich:
        conditions.append(AiMemoryEntry.scope_identity == f"user:{user.id}")
        eigene_notiz = _serverbezogen(
            AiMemoryEntry.scope == "server",
            AiMemoryEntry.owner_user_id == user.id,
        )
        if eigene_notiz is not None:
            conditions.append(eigene_notiz)
    if team_ids:
        conditions.append(
            and_(AiMemoryEntry.scope == "team", AiMemoryEntry.team_id.in_(team_ids))
        )
    rows = db.query(AiMemoryEntry).filter(
        or_(*conditions)
    ).order_by(AiMemoryEntry.scope, AiMemoryEntry.key).all()

    # Je Server einmal fragen, nicht je Zeile. Zehn Notizen zu demselben Server
    # stellten bisher zehnmal dieselbe Frage, und die ist nicht billig:
    # `has_server_permission` lädt Rollen, Rollenrechte und Serverrechte und
    # fällt bei einem Teammitglied zusätzlich in einen Dreifach-Join. Die
    # Antwort kann sich innerhalb dieses Aufrufs nicht ändern — `db`, `user`
    # und `key` sind konstant.
    #
    # **Die Lebensdauer ist die Bedingung.** Dieses Wörterbuch lebt genau so
    # lange wie der Funktionsrumpf. Eine Rechteantwort, die eine Anfrage
    # überlebt, wäre kein schnellerer Aufruf mehr, sondern ein entzogenes
    # Recht, das noch eine Weile weiterwirkt.
    geprueft: dict[int, bool] = {}
    visible: list[AiMemoryEntry] = []
    for row in rows:
        if row.scope in ("server", "server_shared"):
            if row.server_id is None:
                continue
            erlaubt = geprueft.get(row.server_id)
            if erlaubt is None:
                erlaubt = permission_service.has_server_permission(
                    db=db, user=user, server_id=row.server_id, key="server.view"
                )
                geprueft[row.server_id] = erlaubt
            if not erlaubt:
                continue
        visible.append(row)
    return visible


def _entschluesseln(rows: list[AiMemoryEntry]) -> list[tuple[AiMemoryEntry, str]]:
    """Entschluesselt, was sich entschluesseln laesst, und ueberspringt den Rest.

    Vorher stand hier eine Listenauswertung ohne `try`. Ein einziger Eintrag,
    dessen Text sich nicht mehr oeffnen laesst — verdrehte AAD, gewechselter
    Schluessel, halb geschriebene Zeile —, warf `DisDecryptionError` bis in
    `build_provider_messages`. Der Aufrufer in `ai_stream_service` faengt dort
    `DisSidecarError` und uebersetzt ihn zu `AI_CREDENTIAL_UNAVAILABLE`: der
    Lauf begann gar nicht erst. Eine kaputte Notiz nahm damit den ganzen Chat
    mit, und zwar jedes Mal wieder, bis jemand die Zeile in der Datenbank fand.

    Ein Gedaechtnis ist eine Beigabe. Es darf fehlen; es darf nicht im Weg
    stehen. Dieselbe Haltung wie bei `refresh_embedding` weiter oben.

    Bewusst `DisSidecarError` und nicht nur `DisDecryptionError`: ist der
    Sidecar nicht erreichbar, scheitert jede Zeile, und der Benutzer bekommt
    einen Assistenten ohne Gedaechtnis statt gar keinen. Sichtbar bleibt es
    ueber das Protokoll — je Zeile eine Warnung.
    """
    return _entschluesseln_nebenlaeufig(rows, uebergehen=DisSidecarError)


def _entschluesseln_lesbare(rows: list[AiMemoryEntry]) -> list[tuple[AiMemoryEntry, str]]:
    """Wie `_entschluesseln`, aber für die Verwaltungsansicht.

    Der Unterschied zum Helfer darüber ist **genau eine Ausnahmeklasse**, und er
    ist Absicht:

    - `DisDecryptionError` — diese eine Zeile lässt sich nicht mehr öffnen
      (verdrehte AAD, gewechselter Schlüssel). Sie fällt still heraus, die
      übrigen bleiben sichtbar. Vorher nahm ein einziger solcher Eintrag die
      ganze Seite mit: der Router übersetzt ihn zu 503, und der Benutzer sah
      unter Profil > Memory dauerhaft "Memory ist nicht verfügbar" — auch für
      die vierzig intakten Einträge daneben, während der Chat sie unauffällig
      weiterbenutzte.
    - `DisSidecarError` — der Sidecar antwortet gar nicht. Der läuft bewusst
      **weiter** bis zum Router und wird dort zu einem ehrlichen 503. Finge man
      ihn hier mit, zeigte die Verwaltungsansicht eine leere Liste: das
      Gedächtnis wäre angeblich leer, während jedes Schreiben scheitert.

    Der Chatweg (`_entschluesseln`) wählt genau andersherum, und aus demselben
    Grund: dort ist ein Assistent ohne Gedächtnis besser als gar keiner.
    """
    return _entschluesseln_nebenlaeufig(rows, uebergehen=DisDecryptionError)


def _entschluesseln_nebenlaeufig(
    rows: list[AiMemoryEntry], *, uebergehen: type[DisSidecarError]
) -> list[tuple[AiMemoryEntry, str]]:
    """Der gemeinsame Rumpf der beiden Helfer darueber — mehrere Zeilen zugleich.

    Die beiden unterscheiden sich in genau einer Ausnahmeklasse, und diese
    Funktion nimmt sie als ``uebergehen`` entgegen: was darunter faellt, wird
    still uebersprungen, alles andere fliegt weiter zum Aufrufer. Zwei getrennt
    gepflegte Schleifen waeren hier dieselbe Falle wie zwei getrennte
    Bewertungsformeln — eine davon bekaeme eine Verbesserung, die andere nicht.

    **Warum ueberhaupt nebenlaeufig.** Jede Zeile ist ein eigener HTTP-POST an
    den DIS-Sidecar; sequenziell schlaegt deren Zahl eins zu eins in die
    Wartezeit des Benutzers durch (Begruendung und Zahlen an
    `_ENTSCHLUESSELN_GLEICHZEITIG`). `httpx.Client` ist threadsicher und wird
    in `dis_client` ohnehin als einer gehalten — mehr als ein paar Threads
    braucht es dafuer nicht.

    **Was nicht in die Threads darf: die Sitzung.** `_aad` liest
    `aad_version`, `scope_identity` und `id` von einem SQLAlchemy-Objekt, und
    eine abgelaufene Zeile laedt dabei nach. Ein solcher Zugriff aus einem
    Thread waere ein zweiter Benutzer derselben Session, und die ist nicht
    threadsicher. Deshalb werden Ciphertext und AAD **hier** fertiggestellt;
    was hinuebergeht, sind reine Zeichenketten.

    Der Fehler kommt aus dem Thread als Rueckgabewert zurueck und nicht als
    Ausnahme: ob er uebergangen oder weitergereicht wird, entscheidet der
    Aufrufer, und diese Entscheidung gehoert hierher.
    """
    if not rows:
        return []
    auftraege = [(row.value_encrypted, _aad(row)) for row in rows]

    def oeffnen(ciphertext: str, aad: str) -> str | DisSidecarError:
        try:
            return DisClient.decrypt(ciphertext, aad=aad)
        except DisSidecarError as exc:
            return exc

    entschluesselt: list[tuple[AiMemoryEntry, str]] = []
    with ThreadPoolExecutor(
        max_workers=min(_ENTSCHLUESSELN_GLEICHZEITIG, len(auftraege)),
        thread_name_prefix="dis-decrypt",
    ) as pool:
        offen = [pool.submit(oeffnen, ciphertext, aad) for ciphertext, aad in auftraege]
        for row, auftrag in zip(rows, offen):
            ergebnis = auftrag.result()
            if isinstance(ergebnis, DisSidecarError):
                if not isinstance(ergebnis, uebergehen):
                    # Der tote Sidecar in der Verwaltungsansicht: dann scheitert
                    # ohnehin jede Zeile. Sequenziell hoerte die Schleife bei
                    # der ersten auf; ohne das Abraeumen liefe jede uebrige noch
                    # in ihren eigenen 15-Sekunden-Zeitablauf, und aus einem
                    # ehrlichen 503 wuerde eine Seite, die minutenlang haengt.
                    pool.shutdown(cancel_futures=True)
                    raise ergebnis
                logger.warning(
                    "Gedächtniseintrag %s (%s) nicht lesbar, wird übersprungen: %s",
                    row.id, row.scope, type(ergebnis).__name__,
                )
                continue
            entschluesselt.append((row, ergebnis))
    return entschluesselt


def _memory_line(
    row: AiMemoryEntry, value: str, staerke: float | None = None
) -> str:
    # Der Block ist zeilenbasiert und jede Zeile traegt ihren Scope. Ein Wert
    # mit Zeilenumbruch koennte deshalb beliebig viele gefaelschte
    # "[panel] ..."-Zeilen vortaeuschen — ein Benutzer wuerde sich damit im
    # eigenen Kontext panelweite Vorgaben andichten. Der Schluessel ist
    # bereits auf [A-Za-z0-9_.-] begrenzt (schemas/ai_memory.py), der Wert
    # ist es bewusst nicht: er soll frei formulierbar bleiben.
    flattened = " ".join(str(value).splitlines())
    origin = "gesagt" if row.origin == "user" else "gemerkt"
    # Bei serverbezogenen Eintraegen muss die ID mit dran: sonst weiss das
    # Modell nicht, auf welchen der Server sich die Notiz bezieht, und wendet
    # eine Eigenheit von Server 62 versehentlich auf Server 84 an. Bei Teams
    # gilt dasselbe — wer in zwei Teams ist, hat womoeglich zwei verschiedene
    # Antworten auf dieselbe Frage.
    if row.scope == "server":
        scope = f"server:{row.server_id}"
    elif row.scope == "server_shared":
        # Nummer **und** Unterscheidung zur eigenen Notiz. Der `else`-Zweig
        # unten schriebe bloss "server_shared" ohne Nummer — also genau die
        # Verwechslung, die der Kommentar darueber verhindern soll. Das Wort
        # "anlage" statt "shared", weil das Etikett das Modell erreicht und der
        # Rest der Zeile ebenfalls deutsch ist.
        scope = f"server:{row.server_id}:anlage"
        # **Hier zaehlt die Herkunft anders als ueberall sonst.** Bei den
        # persoenlichen Bereichen heisst "gemerkt", dass die KI es sich im
        # Gespraech mit *diesem* Benutzer notiert hat — er war dabei. Wissen
        # der Anlage liest dagegen jeder Kollege mit `server.view`, und keiner
        # von ihnen war dabei: die Zeile kann aus einem fremden Lauf stammen,
        # der eine Logzeile oder eine Konfigdatei gelesen hat. Bestaetigt hat
        # sie dort niemand.
        #
        # Deshalb sagt die Marke an dieser Stelle nicht, *wie* der Eintrag
        # entstanden ist, sondern *worauf er sich stuetzt*: "eingetragen" hat
        # ihn ein Mensch ueber die Oberflaeche, "unbestätigt" ist er, solange
        # nur eine KI ihn notiert hat. Das nimmt der KI nichts weg — sie darf
        # weiter selbst schreiben —, aber der naechste Lauf weiss, wie fest
        # der Boden ist, auf dem er steht.
        origin = "eingetragen" if row.origin == "user" else "unbestätigt"
    elif row.scope == "team":
        # Die Nummer, nicht der Name — anders als in der Absage
        # (`_bereichsname`) und im Suchergebnis (`_execute_search_memory`), und
        # das ist kein Versehen. Dort geht es ums **Ansprechen**: das Team ist
        # das Ziel eines naechsten Aufrufs, und dafuer taugt nur der Name, den
        # `remember` und `forget_memory` als `team="<Name>"` entgegennehmen.
        # Hier geht es ums **Unterscheiden**: die Zeile soll sagen, dass zwei
        # widersprechende Antworten aus zwei verschiedenen Teams stammen, und
        # dafuer reicht eine beliebige stabile Marke.
        #
        # Der Name kostete hier, was er dort nicht kostet. `_memory_line` hat
        # keine Sitzung und ist eine reine Formatierung; ihn zu holen hiesse,
        # die Signatur und beide Kontextaufbauten zu aendern und im
        # schlechtesten Fall je Zeile ein `db.get(Team, …)` zu bezahlen — bei
        # jeder Nachricht neu, waehrend die Suche einmal je Loeschabsicht
        # laeuft. Dazu geht jede Zeile gegen dieselben 6.000 Zeichen; ein Name
        # je Zeile verdraengt Eintraege, statt welche zu erklaeren.
        scope = f"team:{row.team_id}"
    else:
        scope = row.scope
    # **Verblasst statt weg.** Liegt die Abrufstaerke unter der Schwelle, geht
    # nur der Anfang mit — der Schluessel bleibt immer vollstaendig, damit das
    # Modell weiss, *dass* es die Notiz gibt, und mit `search_memory`
    # nachfassen kann. Genau der Weg, den ein Mensch nimmt, wenn ihm etwas
    # "auf der Zunge liegt".
    if staerke is not None and staerke < VERBLASSEN_AB and len(flattened) > VERBLASST_ZEICHEN:
        flattened = flattened[:VERBLASST_ZEICHEN].rstrip() + " …"
        return f"[{scope}/{origin}/blass] {row.key}: {flattened}"
    return f"[{scope}/{origin}] {row.key}: {flattened}"


def _similarities(query: str, rows: list[AiMemoryEntry]) -> list[float | None]:
    """Bedeutungsaehnlichkeit der Eintraege zur Frage, oder lauter ``None``.

    ``None`` steht fuer "kein Vergleich moeglich" und nicht fuer "unaehnlich":
    ohne Modell, ohne Frage oder ohne gespeicherten Vektor soll ein Eintrag
    nach den uebrigen Kriterien bewertet werden, statt hinten anzustehen.
    """
    if not query.strip():
        return [None] * len(rows)
    query_vectors = ai_embedding_service.encode([query])
    if not query_vectors:
        return [None] * len(rows)

    stored = [_stored_vector(row) for row in rows]
    known = [vector for vector in stored if vector is not None]
    if not known:
        return [None] * len(rows)

    scores = ai_embedding_service.similarity(query_vectors[0], known)
    if len(scores) != len(known):
        return [None] * len(rows)
    result: list[float | None] = []
    iterator = iter(scores)
    for vector in stored:
        result.append(next(iterator) if vector is not None else None)
    return result


def server_shared_context(
    db: Session, user: User, server_id: int, query: str = ""
) -> str | None:
    """Nur das Wissen **einer** Anlage, als fertiger Block.

    Gebraucht wird das mitten im Lauf: der Kontext entsteht einmal, beim
    Anlegen, und da weiss noch niemand, um welchen Server es geht. Der Benutzer
    schreibt "warum kommt keiner rein?", und erst das erste Werkzeug klaert die
    Nummer. Ohne Nachreichen kaeme die Betriebsanleitung genau eine Nachricht zu
    spaet — also gerade nicht bei der Frage, fuer die sie gedacht ist.

    Ueber denselben Leseweg wie der ganze Kontext, nur auf einen Bereich und
    einen Server eingeschraenkt. Die Sichtbarkeitspruefung steckt in
    `_visible_scope_rows`; hier steht keine zweite Kopie davon, und deshalb
    kann sie hier auch nicht abweichen.

    Der Nutzungszähler läuft nach derselben Regel mit wie im Gesamtkontext:
    vermerkt wird, wen die Frage getroffen hat, nicht wer mitgegangen ist.
    Ohne diese Einschränkung zählte Anlagenwissen bei **jedem** Nachtrag hoch
    und gewänne im Engpass gegen persönliche Vorlieben — nicht weil es
    gebraucht wurde, sondern weil es häufiger nachgereicht wird.
    """
    rows = [
        row
        for row in _visible_scope_rows(db, user, persoenlich=preference(db, user.id))
        if row.scope == "server_shared" and row.server_id == server_id
    ]
    if not rows:
        return None
    # Hier braucht es kein `_vorauswahl`: der Filter laesst genau *einen*
    # Bereich uebrig, und `server_shared` haengt an der festen
    # `MAX_SYSTEM_SCOPE_ENTRIES` statt am Rollenlimit. Mehr als hundert Zeilen
    # koennen es also gar nicht sein — der Multiplikator, gegen den
    # `MAX_CONTEXT_ROWS` steht, entsteht erst durch *mehrere* Bereiche.
    decoded = _entschluesseln(rows)
    if not decoded:
        return None
    jetzt = datetime.now(timezone.utc)
    query_tokens = _tokens(query)
    aehnlichkeiten = _similarities(query, [row for row, _ in decoded])
    for (row, wert), aehnlichkeit in zip(decoded, aehnlichkeiten):
        treffer = _reiz(aehnlichkeit, len(query_tokens & _tokens(f"{row.key} {wert}")))
        if treffer < VERBLASSEN_AB:
            continue
        row.use_count = int(row.use_count or 0) + 1
        row.last_used_at = jetzt
    db.flush()
    # Kein Budgetschnitt und keine Rangfolge: eine Anlage hat hoechstens
    # `MAX_SYSTEM_SCOPE_ENTRIES` Zeilen — dieser Bereich haengt an keiner
    # Benutzerrolle und bleibt deshalb bei der festen Systemgrenze, auch
    # nachdem die uebrigen Bereiche konfigurierbar geworden sind. Anders als
    # beim Gesamtkontext steht hier ausserdem nichts daneben, mit
    # dem sie um Platz konkurrieren muessten. `query` bleibt trotzdem in der
    # Signatur — wird die Grenze eines Tages doch erreicht, ist die Auswahl an
    # dieser Stelle zu treffen und nicht beim Aufrufer.
    return "\n".join(_memory_line(row, wert) for row, wert in decoded)


def provider_memory_context(
    db: Session,
    user: User,
    query: str = "",
    server_id: int | None = None,
    budget: int | None = None,
) -> str | None:
    """Baut den Memory-Block fuer eine konkrete Anfrage.

    Passt alles ins Budget, kommt alles mit — der Normalfall, solange der
    Betreiber die Bereichsgrenze nicht hochgesetzt hat, und zugleich der
    sprachunabhaengigste Fall: das Sprachmodell sieht jeden Eintrag und stellt
    den Bezug selbst her, egal in welcher Sprache er formuliert ist.

    **Mitkommen heisst aber nicht gleich stark.** Jede Zeile bekommt ihre
    Abrufstaerke (`abrufstaerke`); was darunter liegt, geht verkuerzt mit —
    Schluessel und Anfang statt des ganzen Werts. Der Eintrag ist damit nicht
    weg, sondern blass: das Modell sieht, *dass* es ihn gibt, und kann mit
    `search_memory` nachfassen. Trifft die Frage ihn, ist er in derselben
    Runde wieder vollstaendig da.

    Ohne diesen Schritt war das System binaer: bis zur Budgetgrenze alles
    gleich stark, danach Auswahl. Gemessen am 19.08.2026 lag die Auslastung
    bei 14,6 % — es haette rund 41 Eintraege gebraucht, bevor ueberhaupt etwas
    bewertet worden waere, und bis dahin stand der Eintrag von gestern
    gleichberechtigt neben dem von vor drei Monaten.

    Erst wenn es *nicht* passt, wird zusaetzlich ausgewaehlt — nach Bedeutung,
    Bezug zur Frage, Nutzung und Aktualitaet. Vorher wurde an dieser Stelle
    alphabetisch nach Schluessel sortiert und bei 6.000 Zeichen abgeschnitten:
    ein Eintrag "zeitzone" fiel damit systematisch raus, "backup" blieb immer
    drin.

    Als benutzt vermerkt werden dabei nur die Einträge, die die Frage
    tatsächlich **getroffen** hat — nicht alles, was mitgegangen ist. Dieses
    Zählwerk ist das Gedächtnis des Gedächtnisses: es entscheidet beim nächsten
    Engpass mit, was bleibt, und es ist der Weg zurück aus dem Verblassen.
    Genau deshalb darf es nicht am bloßen Danebenliegen hängen.

    ``budget`` ist der Platz in Zeichen, den der Block bekommen darf. Er kommt
    aus derselben Rechnung wie der aller anderen Kontextblöcke
    (``ai_context_service.teilbudgets(...).gedaechtnis_zeichen``) und wächst
    damit mit dem Fenster des Modells. ``None`` heißt "der Aufrufer kennt kein
    Fenster" und führt auf ``MAX_CONTEXT_CHARS`` — dieselben 6.000 Zeichen wie
    vor der Fensterberechnung. Der Vorgabewert steht bewusst als ``None`` in der
    Signatur und nicht als Konstante: nur so wirkt ein Test, der
    ``MAX_CONTEXT_CHARS`` heruntersetzt, weiterhin auch hier.
    """
    zeichen = budget if budget is not None else MAX_CONTEXT_CHARS
    # Die Einwilligung gilt dem **eigenen** Gedaechtnis. Teamwissen gehoert dem
    # Team und panelweites dem Betreiber; wer diesen Schalter umlegt, trifft
    # eine Entscheidung ueber sich, nicht ueber seine Kollegen. Vorher endete
    # die Funktion hier komplett — ein Mitglied ohne Einwilligung arbeitete
    # unbemerkt ohne das Wissen seiner Teams.
    rows = _visible_scope_rows(db, user, persoenlich=preference(db, user.id))
    # Anlagenwissen kommt **nur** fuer den Server mit, um den es gerade geht.
    #
    # Sichtbar sind einem Betreiber leicht zwanzig Server. Faende alles
    # Anlagenwissen dieser zwanzig gleichzeitig in den Kontext, waere das Budget
    # des Blocks von Betriebsanleitungen aufgebraucht, die mit der Frage
    # nichts zu tun haben — und die persoenlichen Vorlieben des Benutzers fielen
    # als Erstes heraus. Schlimmer noch: das Modell saehe zwanzig Anleitungen
    # nebeneinander und wendete die Eigenheit des einen auf den anderen an.
    #
    # Ohne Serverbezug kommt bewusst gar keines mit statt alles. `ai_runs.
    # last_server_id` liefert den Bezug, sobald ein Werkzeug einen Server
    # angefasst hat; vorher gibt es schlicht kein Thema, auf das man einen
    # Ausschnitt beziehen koennte.
    rows = [
        row for row in rows
        if row.scope != "server_shared" or row.server_id == server_id
    ]
    if not rows:
        return None

    now = datetime.now(timezone.utc)
    # Die zweite Engstelle, und die einzige, die vor der Entschluesselung
    # greifen kann. Der Budgetschnitt weiter unten braucht den Klartext, um zu
    # messen — er kommt also zwangslaeufig zu spaet, um Roundtrips zu sparen.
    #
    # Der Zeilendeckel wandert im selben Verhältnis mit wie das Budget, denn er
    # steht nicht für sich: 300 ist gegen genau 6.000 Zeichen gewählt
    # (Begründung an `MAX_CONTEXT_ROWS`), nämlich als das Doppelte dessen, was
    # bei kurzen Einträgen überhaupt hineinpasst. Bliebe er fest, während das
    # Budget mit dem Fenster wächst, meldete der Block bei vielen kurzen
    # Einträgen "ausgelassen", obwohl daneben noch Platz frei ist. Der Preis
    # wandert mit: beim Deckel von 24.000 Zeichen sind es bis zu 1.200
    # Sidecar-Roundtrips statt 300 — proportional zu dem Fenster, das der
    # Betreiber sich ausgesucht hat.
    zeilen = max(1, MAX_CONTEXT_ROWS * zeichen // MAX_CONTEXT_CHARS)
    rows, vorgekuerzt = _vorauswahl(rows, query, now, zeilen)
    decoded = _entschluesseln(rows)
    # Zeilen aus einer Ausfallphase des Modells tragen keinen Vektor. Hier
    # liegt ihr Klartext ohnehin offen, also ist hier die Stelle, an der es
    # nichts extra kostet, ihn nachzurechnen — sonst blieben sie für immer
    # blind für Bedeutungsrang und Reiz.
    _vektoren_nachziehen(decoded)
    # **Die Abrufstaerke je Zeile**, einmal berechnet und danach zweimal
    # gebraucht: fuer die Darstellung (blass oder voll) und, falls das Budget
    # nicht reicht, als Teil der Auswahl. Die Vektoren liegen ohnehin schon an
    # den Zeilen; teuer ist hier nichts.
    query_tokens = _tokens(query)
    aehnlichkeiten = _similarities(query, [row for row, _ in decoded])
    ueberlappungen = [
        len(query_tokens & _tokens(f"{row.key} {value}")) for row, value in decoded
    ]
    # Der Reiz steht getrennt daneben, weil er zwei verschiedene Fragen
    # beantwortet: `abrufstaerke` braucht ihn als Untergrenze für die
    # Darstellung, und weiter unten entscheidet er darüber, ob dieser Eintrag
    # als **gebraucht** gilt.
    reize = [
        _reiz(aehnlichkeit, overlap)
        for aehnlichkeit, overlap in zip(aehnlichkeiten, ueberlappungen)
    ]
    staerken = [
        abrufstaerke(row, now, aehnlichkeit, overlap)
        for (row, _value), aehnlichkeit, overlap
        in zip(decoded, aehnlichkeiten, ueberlappungen)
    ]
    lines = [
        _memory_line(row, value, staerke)
        for (row, value), staerke in zip(decoded, staerken)
    ]
    total = sum(len(line) + 1 for line in lines)

    if total <= zeichen:
        selected = decoded
        truncated = vorgekuerzt
    else:
        scores = aehnlichkeiten
        ranked = sorted(
            zip(decoded, scores),
            key=lambda item: _relevance(
                item[0][0], item[0][1], query_tokens, now, item[1]
            ),
            reverse=True,
        )
        selected = []
        used = 0
        # Die Staerken nach Zeile nachschlagbar machen: `ranked` hat die
        # Reihenfolge geaendert, und ohne Zuordnung faende die Darstellung
        # unten ihre Staerke nicht wieder — der Eintrag stuende dann wieder
        # in voller Laenge da, obwohl er blass ist.
        staerke_je_zeile = {
            id(row): staerke for (row, _v), staerke in zip(decoded, staerken)
        }
        for (row, value), _score in ranked:
            line = _memory_line(row, value, staerke_je_zeile.get(id(row)))
            if used + len(line) + 1 > zeichen:
                continue
            selected.append((row, value))
            used += len(line) + 1
        # Die urspruengliche Reihenfolge lesbar halten, nicht die Rangfolge.
        selected.sort(key=lambda item: (item[0].scope, item[0].key))
        # `vorgekuerzt` gehoert mit hinein: hat schon die Vorauswahl Zeilen
        # weggelassen, fehlt etwas, auch wenn hier zufaellig alles Uebrige ins
        # Budget passt. `decoded` weiss davon nichts mehr — es kennt nur, was
        # ihm gegeben wurde.
        truncated = vorgekuerzt or len(selected) < len(decoded)

    if not selected:
        return None

    # **Gebraucht ist, wen die Frage getroffen hat — Anzeigen ist kein
    # Gebrauch.**
    #
    # Bis hierher zählte jede gezeigte Zeile hoch, und weil unterhalb des
    # Budgets *jede* sichtbare Zeile mitgeht, hieß das: eine Chatnachricht,
    # ein Zählschritt für alles. Das hob das Verblassen auf, und zwar
    # zweifach. Erstens sofort: `last_used_at = now` setzt die Frische auf 1.0,
    # ein blasser Eintrag stand nach genau einer Anzeige wieder voll da.
    # Zweitens dauerhaft: ab `use_count` 13 liegt allein die Vertrautheit über
    # `VERBLASSEN_AB`, danach kann der Eintrag nie wieder verblassen — und
    # dreizehn Nachrichten sind ein Vormittag. Ein Gedächtnis, das sich durch
    # bloßes Danebenliegen selbst auffrischt, verblasst nie.
    #
    # Die Regel ist deshalb dieselbe, an der auch die Darstellung hängt: nur
    # wer über Bedeutung oder Wortbezug getroffen wurde, war gebraucht. Der
    # Reiz stammt wie die Stärken aus dem Zustand *vor* dieser Runde.
    staerke_final = {
        id(row): staerke for (row, _v), staerke in zip(decoded, staerken)
    }
    reiz_je_zeile = {id(row): reiz for (row, _v), reiz in zip(decoded, reize)}
    for row, _value in selected:
        if reiz_je_zeile.get(id(row), 0.0) < VERBLASSEN_AB:
            continue
        row.use_count = int(row.use_count or 0) + 1
        row.last_used_at = now
    db.flush()

    block = "\n".join(
        _memory_line(row, value, staerke_final.get(id(row)))
        for row, value in selected
    )
    if truncated:
        # Ehrlich bleiben: das Modell soll wissen, dass es nicht alles sieht,
        # statt aus einer Luecke zu schliessen, es gebe nichts.
        block += (
            f"\n[Hinweis] {len(decoded) - len(selected)} weitere Eintraege wurden "
            "aus Platzgruenden ausgelassen."
        )
    return block


# Wie viele Treffer eine Suche hoechstens meldet. Bewusst knapp: die Liste
# landet im Chat und der Benutzer soll sie ueberblicken koennen, bevor er
# ueber das Loeschen entscheidet.
MAX_SEARCH_RESULTS = 15


def search_entries(
    db: Session, user: User, query: str, limit: int = MAX_SEARCH_RESULTS
) -> list[tuple[AiMemoryEntry, str, float]]:
    """Findet Eintraege nach Bedeutung, nicht nach Wortgleichheit.

    Dieselbe Bewertung wie beim Abruf in den Kontext — Vektoraehnlichkeit,
    Wortueberlappung, Nutzung, Aktualitaet. "alles ueber meinen Hund" findet
    damit auch einen Eintrag, in dem das Wort "Hund" gar nicht vorkommt, weil
    dort "Bello" steht.

    Gesucht wird ausschliesslich in dem, was der Benutzer ohnehin sehen darf:
    `_visible_scope_rows` ist derselbe Filter wie beim Lesen — **einschliesslich
    der Einwilligung.** Dass die hier fehlte, war ein Widerspruch: der Abruf in
    den Kontext respektierte sie, die Suche nicht, und `search_memory` legte
    dem Modell damit persoenliche Eintraege vor, denen nie jemand zugestimmt
    hatte. Eine Suche kann nichts aufdecken, was ohne sie verborgen waere.

    Der Rueckgabewert enthaelt den Klartext. Er ist die Grundlage der
    Entscheidung — wer loeschen soll, muss sehen was.

    Die gemeldeten Treffer gelten als **benutzt**, und das ist die eine Stelle,
    an der das unstrittig ist: hier hat jemand ausdrücklich gesucht und bekommt
    genau diese Zeilen vorgelegt. Der Abruf in den Kontext vermerkt dagegen nur,
    wen die Frage getroffen hat — dort geht vieles mit, um das niemand gebeten
    hat.
    """
    rows = _visible_scope_rows(db, user, persoenlich=preference(db, user.id))
    if not rows or not query.strip():
        return []

    now = datetime.now(timezone.utc)
    # Derselbe Deckel wie beim Abruf in den Kontext, und aus demselben Grund:
    # diese Funktion entschluesselte bisher **alles** Sichtbare, um am Ende
    # ``limit`` Treffer zurueckzugeben — bei 15 Treffern und zwanzig sichtbaren
    # Anlagen waren das Tausende Sidecar-Roundtrips fuer fuenfzehn Zeilen.
    # Die Vorauswahl bewertet nach denselben Kriterien, die auch hier gleich
    # angelegt werden, nur ohne den Wert; sie nimmt also nicht "irgendwelche
    # 300", sondern dieselben, die auch danach vorn laegen.
    #
    # Hier bleibt es bei der festen Zahl, während sie beim Abruf in den Kontext
    # mit dem Budget wächst: eine Suche meldet höchstens `MAX_SEARCH_RESULTS`
    # Treffer in den Chat und hängt an der Lesbarkeit, nicht am Kontextfenster
    # des Modells. Mehr Kandidaten zu öffnen kaufte hier nichts.
    rows, _vorgekuerzt = _vorauswahl(rows, query, now, MAX_CONTEXT_ROWS)
    decoded = _entschluesseln(rows)
    query_tokens = _tokens(query)
    scores = _similarities(query, [row for row, _ in decoded])
    ranked = sorted(
        zip(decoded, scores),
        key=lambda item: _relevance(item[0][0], item[0][1], query_tokens, now, item[1]),
        reverse=True,
    )
    treffer = [
        (row, value, _relevance(row, value, query_tokens, now, score))
        for (row, value), score in ranked[:limit]
    ]
    for row, _value, _rang in treffer:
        row.use_count = int(row.use_count or 0) + 1
        row.last_used_at = now
    db.flush()
    return treffer


def delete_by_keys(
    db: Session, user: User, *, scope: str, keys: list[str], team_id: int | None = None,
    server_id: int | None = None,
) -> list[str]:
    """Loescht genau die benannten Schluessel eines Bereichs.

    Bewusst **nicht** nach Suchbegriff. Eine unscharfe Aehnlichkeit entscheidet
    darueber, was ein Mensch zu sehen bekommt — sie darf nicht darueber
    entscheiden, was verschwindet. Der Weg ist deshalb zweistufig: erst suchen
    und zeigen, dann die gefundenen Schluessel ausdruecklich loeschen.

    Rechte wie beim Schreiben: persoenliche Eintraege gehoeren dem Benutzer,
    Team-Eintraege verlangen `can_manage_memory`, panelweite
    `panel.settings.write`, das Wissen einer Anlage `server.config.write`.
    """
    identity, _owner_id, normalized_server_id, normalized_team_id = scope_identity(
        db, user, scope, server_id, team_id
    )
    _assert_may_write(db, user, scope, normalized_team_id, normalized_server_id)

    wanted = [key for key in keys if isinstance(key, str) and key.strip()]
    if not wanted:
        return []
    rows = (
        db.query(AiMemoryEntry)
        .filter(
            AiMemoryEntry.scope_identity == identity,
            AiMemoryEntry.key.in_(wanted),
        )
        .all()
    )
    removed: list[str] = []
    for row in rows:
        audit_service.record_privileged_action(
            db, user_id=user.id, action="ai.memory.deleted", target_type="ai_memory",
            target_id=row.id,
            details={
                "scope": row.scope, "key": row.key,
                **({"server_id": row.server_id} if row.server_id else {}),
            },
            origin="ai",
        )
        removed.append(row.key)
        db.delete(row)
    db.commit()
    return sorted(removed)
