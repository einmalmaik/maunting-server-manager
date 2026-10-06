"""Ownership, DIS-Schutz, Secret-Abweisung und Abruf fuer AI-Memory."""

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import logging
import os
import re
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query, Session

from config import settings
from models import (
    AiMemoryEntry,
    AiMemoryPreference,
    AiMemoryTopic,
    AiMemoryVersion,
    Server,
    Team,
    User,
)
from services import (
    ai_embedding_service,
    ai_limit_service,
    audit_service,
    permission_service,
)
from services.ai_redaction import enthaelt_zugangsdaten
from services.ai_embedding_service import EMBEDDING_BYTES
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
# sind das über 105.000 Zeilen, und jede muss in `_entschluesseln` durch den
# DIS-Sidecar — vor dem Schnitt auf `MAX_CONTEXT_CHARS`, weil sich erst am
# Klartext messen laesst, was ins Budget passt. Seit 06.10.2026 gehen sie
# gebuendelt (`_sammeln`); das spart Roundtrips, es begrenzt den Aufwand
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

#: Wieviel Abrufstaerke ein frisch gemerkter Eintrag mitbringt.
#:
#: Ohne diesen Startwert waere jeder neue Eintrag sofort blass: er hat noch
#: keine Nutzung, und `use_count` ist der staerkste Anteil der Formel. Genau
#: das war beim Vorgaenger der Fehler, gegen den `recency` eingebaut wurde.
NEUHEITSSCHUTZ_TAGE = 3.0
_WORD_RE = re.compile(r"[\w]+", re.UNICODE)

#: Wieviele Werte ein Sammelaufruf an den DIS-Sidecar hoechstens traegt.
#:
#: An einer Entschluesselung ist fast nichts Rechnung: der Sidecar oeffnet ein
#: paar hundert Byte AES-GCM, alles andere ist der Weg hin und zurueck. Bis
#: 06.10.2026 ging hier jede Zeile als eigener `/decrypt`, acht zugleich in
#: Threads; gemessen am 19.08.2026 kostete das bei 300 Zeilen 150 bis 600 ms
#: vor dem ersten Byte der Antwort. Seit es `/decrypt-many` gibt, gehen Text,
#: Titel und Name aller Zeilen in einem Aufruf (`_oeffnen`). Die beiden
#: Grenzen liegen unter denen des Sidecars (10.000 Werte, 8 MiB), mit Luft.
_STAPEL_WERTE = 2_000
_STAPEL_ZEICHEN = 4 * 1024 * 1024

# ── Gedächtnis v2: Sätze statt Schlüssel (06.10.2026) ───────────────────────
#
# Eine Erinnerung ist ein bis fünf Sätze, die ohne das Gespräch verständlich
# sind („Jonas trinkt Kaffee schwarz.“), mit Titel, Thema, Art, Quelle und
# Wichtigkeit daneben. Der Schlüssel war bis hierher ihre Identität; dieselbe
# Sache unter zwei Namen stand zweimal da, und eine Erzählung ließ sich in
# Namen gar nicht fassen. Alte Einträge behalten ihren Namen, bis die Pflege
# sie in Sätze umschreibt.

#: Was für eine Aussage eine Erinnerung ist. Dieselbe Liste hält die Datenbank
#: (`ck_ai_memory_entries_art`).
ARTEN = ("fakt", "vorliebe", "anweisung", "ereignis", "plan", "beziehung", "wissen")
#: Woher eine Erinnerung kommt (`ck_ai_memory_entries_quelle`).
QUELLEN = ("eingetragen", "gespraech", "import", "pflege")
#: Warum es eine frühere Fassung gibt (`ck_ai_memory_versionen_grund`).
GRUENDE = (
    "bearbeitet", "aktualisiert", "zusammengefuehrt", "aufgenommen",
    "umgeschrieben", "wiederhergestellt",
)
#: Wie lang Text, Titel und Thema sein dürfen. Der Text bleibt bei der alten
#: Grenze des Werts: fünf Sätze passen bequem hinein, ein Kapitel nicht, und
#: genau das ist gewollt.
MAX_TEXT_ZEICHEN = 2_000
MAX_TITEL_ZEICHEN = 120
MAX_THEMA_ZEICHEN = 60
#: Wieviele frühere Fassungen eine Erinnerung behält. Die älteste fällt, wenn
#: eine neue dazukommt; ohne Grenze wüchse eine oft geänderte Erinnerung mit
#: jeder Pflege weiter.
MAX_VERSIONEN = 20
#: Wie lange eine vergessene Erinnerung zurückgeholt werden kann.
VERGESSEN_TAGE = 30
#: Wieviele vergessene Erinnerungen ein Bereich höchstens aufhebt. Vergessene
#: zählen nicht gegen das Rollenlimit (sonst schüfe Vergessen keinen Platz),
#: ihr Vorrat braucht deshalb eine eigene Grenze: darüber fallen die ältesten
#: vor Ablauf der Frist.
MAX_VERGESSENE = 1_000


class MemoryScopeVoll(HTTPException):
    """409 mit dem Zählstand daneben, weil ein Text allein zu wenig trägt.

    Bleibt bewusst eine `HTTPException`: der Router in `routers/ai_memory.py`
    und jeder andere Aufrufer merken von dieser Klasse nichts, bekommen
    denselben 409 und dasselbe `detail` wie vorher — und dieses `detail` ist
    ein Satz für einen **Menschen**, denn über den Router legt der Benutzer
    selbst einen Eintrag an und liest die Meldung als Toast.

    Die drei Zahlen daneben sind für Aufrufer da, die „gesperrt“ von „genau
    voll“ von „nachträglich gesenkt“ unterscheiden wollen, ohne auf den
    Meldungstext zu horchen — jede Umformulierung hier änderte dort sonst
    stillschweigend das Verhalten. Bis Stufe 2 des Gedächtnisses machte
    `remember` daraus eine Anweisung an das Modell; der Gedächtnisschreiber
    kennt den freien Platz vorher (`ai_gedaechtnis_schreiber._bereiche`).
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


def _versions_aad(fassung: AiMemoryVersion, feld: str) -> str:
    """Wie `_aad`, für Text oder Titel einer früheren Fassung.

    Gebunden an Bereich, Erinnerung und Fassung. Ohne den Bereich bliebe eine
    Lücke, die `_aad` gerade schließt: wer eine Erinnerung per
    Datenbankzugriff in seinen eigenen Bereich umhängt, macht ihren Text
    unlesbar, ihre früheren Fassungen aber nicht.
    """
    return (
        f"msm:ai:memory:version:{feld}:{fassung.scope_identity}:"
        f"{fassung.memory_id}:{fassung.id}"
    )


# ── Der verschluesselte Name ────────────────────────────────────────────────
#
# Bis 26.09.2026 stand `key` im Klartext und war zugleich Suchschluessel und
# Eindeutigkeit je Bereich. Heute liegt er verschluesselt in `key_encrypted`,
# und gesucht wird ueber `key_index` (HMAC ueber Bereich und Namen, siehe
# Modell). Die Funktionen hier sind der einzige Weg dorthin: wer nach einem
# Namen sucht, nimmt `schluessel_bedingung`, nie einen eigenen Filter.


def _schluessel_indizes(identity: str, keys: list[str]) -> list[str]:
    return DisClient.blind_index([f"{identity}\n{key}" for key in keys])


def _stapel(paare: list[tuple[str, str]]) -> Iterator[list[tuple[str, str]]]:
    """Teilt Paare aus Chiffrat und AAD in Stapel unter den Grenzen des Sidecars."""
    stapel: list[tuple[str, str]] = []
    zeichen = 0
    for paar in paare:
        laenge = len(paar[0]) + len(paar[1])
        if stapel and (len(stapel) >= _STAPEL_WERTE or zeichen + laenge > _STAPEL_ZEICHEN):
            yield stapel
            stapel, zeichen = [], 0
        stapel.append(paar)
        zeichen += laenge
    if stapel:
        yield stapel


def _sammeln(paare: list[tuple[str, str]]) -> list[str | None]:
    """Oeffnet Paare aus Chiffrat und AAD in wenigen Aufrufen, in Reihenfolge.

    Ein unlesbarer Wert kommt als ``None`` zurueck (wie `DisClient.decrypt_many`).
    Antwortet der Sidecar nicht, fliegt `DisSidecarError`; was der Aufrufer
    daraus macht, entscheidet er.
    """
    ergebnis: list[str | None] = []
    for stapel in _stapel(paare):
        ergebnis.extend(DisClient.decrypt_many(stapel))
    return ergebnis


def _schluessel_laden(rows: list[AiMemoryEntry]) -> None:
    """Entschluesselt Namen und Titel vieler Zeilen mit einem Sidecar-Aufruf.

    Danach lesen `row.key` und `row.titel` aus dem Speicher der Zeile. Ohne
    diesen Schritt kostete jede Zeile einen eigenen Aufruf, bei 5.000
    Eintraegen rund 4 s. Ein unlesbarer Name wird zu ``""``, ein unlesbarer
    Titel zu ``None``; der Text daneben entscheidet dann, ob die Zeile
    ueberhaupt erscheint.
    """
    namen = [
        row for row in rows
        if "_key_klartext" not in row.__dict__
        and DisClient.ist_verschluesselt(row.key_encrypted or "")
    ]
    titel = [
        row for row in rows
        if "_titel_klartext" not in row.__dict__
        and DisClient.ist_verschluesselt(row.titel_encrypted or "")
    ]
    klartexte = _sammeln(
        [(row.key_encrypted, row.key_aad()) for row in namen]
        + [(row.titel_encrypted, row.titel_aad()) for row in titel]
    )
    for row, klar in zip(namen, klartexte[: len(namen)]):
        row._key_klartext = klar if klar is not None else ""
    for row, klar in zip(titel, klartexte[len(namen):]):
        row._titel_klartext = klar


def _index_nachziehen(db: Session, identity: str) -> None:
    """Gibt Altbestand eines Bereichs Index und verschluesselten Namen.

    Laeuft vor jeder Suche nach einem Namen. So findet die Suche auch eine
    Zeile von vor der Umstellung, bevor `schluessel_nachziehen` beim Start
    des Panels bei ihr angekommen ist, statt einen zweiten Eintrag mit
    demselben Namen anzulegen.
    """
    # Nur Zeilen **mit** Namen. Seit Gedaechtnis v2 haben neue Zeilen gar
    # keinen; ohne die zweite Bedingung bekaeme jede von ihnen den Index des
    # leeren Namens, und die zweite scheiterte am UNIQUE.
    alt = db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == identity,
        AiMemoryEntry.key_index.is_(None),
        AiMemoryEntry.key_encrypted.is_not(None),
    ).all()
    if not alt:
        return
    _schluessel_laden(alt)
    for row, index in zip(alt, _schluessel_indizes(identity, [row.key for row in alt])):
        if not DisClient.ist_verschluesselt(row.key_encrypted or ""):
            # Altbestand: der Getter liefert den Klartext, der Setter
            # verschluesselt ihn.
            row.key = row.key
        row.key_index = index
    db.flush()


def schluessel_bedingung(db: Session, identity: str, keys: list[str]):
    """SQL-Bedingung fuer "diese Namen in diesem Bereich"."""
    _index_nachziehen(db, identity)
    return and_(
        AiMemoryEntry.scope_identity == identity,
        AiMemoryEntry.key_index.in_(_schluessel_indizes(identity, keys)),
    )


def schluessel_nachziehen(db: Session) -> int:
    """Zieht alle Bereiche mit Altbestand nach. Laeuft beim Start des Panels."""
    bereiche = [
        kennung for (kennung,) in db.query(AiMemoryEntry.scope_identity)
        .filter(
            AiMemoryEntry.key_index.is_(None), AiMemoryEntry.key_encrypted.is_not(None)
        ).distinct().all()
    ]
    for kennung in bereiche:
        _index_nachziehen(db, kennung)
        db.commit()
    return len(bereiche)


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
        # antwortet "Bitte erneut versuchen". Das Modell, das bis Stufe 2 des
        # Gedaechtnisses selbst schrieb, befolgte diese Aufforderung und
        # wiederholte denselben aussichtslosen Aufruf, statt mit
        # `list_my_servers` nach der richtigen Nummer zu suchen. Die Diagnose
        # muss stimmen, sonst fuehrt sie jeden Leser in die Irre.
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


def _einschalten(db: Session, row: AiMemoryPreference) -> None:
    """Das Gedaechtnis wird eingeschaltet: der Schreiber liest ab jetzt.

    Was in der Zeit ohne Einwilligung gesagt wurde, liest niemand nach — wer
    den Schalter umlegt, soll nicht schlagartig ein Gedaechtnis ueber die Zeit
    davor bekommen (`ai_gedaechtnis_schreiber`). Die Marke jedes Gespraechs
    rueckt deshalb auf jetzt, und der Zeitpunkt bleibt an der Einwilligung
    stehen (`einwilligung_seit`): auch der Zusammenhang vor der Marke darf
    nicht aus der Zeit davor stammen. ``updated_at`` bleibt stehen: das
    Gespraech ist dadurch nicht neuer geworden.
    """
    from models import AiConversation
    from sqlalchemy import update

    jetzt = datetime.now(timezone.utc)
    db.execute(
        update(AiConversation)
        .where(AiConversation.user_id == row.user_id)
        .values(
            gedaechtnis_bis=jetzt,
            gedaechtnis_bis_id=None,
            updated_at=AiConversation.updated_at,
        )
        .execution_options(synchronize_session=False)
    )
    row.eingeschaltet_am = jetzt
    row.enabled = True


def einwilligung_seit(db: Session, user_id: int) -> datetime | None:
    """Seit wann das Gedaechtnis ohne Unterbrechung eingeschaltet ist; ``None``: aus.

    Fuer den Gedaechtnisschreiber: Persoenliches schreibt er nur aus dem, was
    seitdem gesagt wurde. Eine eingeschaltete Zeile ohne Zeitpunkt ist an
    `_einschalten` vorbei entstanden (die Migration hat jeder eingeschalteten
    Zeile einen gegeben); sie gilt als schon immer eingeschaltet — ab 1970,
    was jede Datenbank darstellen kann.
    """
    row = db.get(AiMemoryPreference, user_id)
    if row is None or not row.enabled:
        return None
    if row.eingeschaltet_am is None:
        return datetime.fromtimestamp(0, timezone.utc)
    return _utc(row.eingeschaltet_am)


def set_preference(db: Session, user: User, enabled: bool) -> AiMemoryPreference:
    row = _preference_row(db, user.id)
    if enabled and not row.enabled:
        _einschalten(db, row)
    row.enabled = enabled
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
    if enable and not row.enabled:
        _einschalten(db, row)
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
    rows = (
        db.query(AiMemoryEntry)
        .filter(AiMemoryEntry.scope_identity == identity, AiMemoryEntry.status == "aktiv")
        .order_by(AiMemoryEntry.created_at, AiMemoryEntry.id)
        .all()
    )
    # Nach dem Namen sortiert wird erst hier: in der Datenbank steht er
    # verschluesselt, eine SQL-Sortierung ordnete nach Chiffrat. Zeilen ohne
    # Namen (Gedaechtnis v2) bleiben in der Reihenfolge ihres Entstehens.
    return sorted(_entschluesseln_lesbare(rows), key=lambda paar: paar[0].key)


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
    #: Die Namen der Themen, die auf dieser Seite vorkommen (Kennung -> Name).
    themen: dict[str, str] = field(default_factory=dict)


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
#: ``last_used_at IS NULL`` als erstes Kriterium: ohne Angabe stellt PostgreSQL
#: NULL bei DESC nach vorn, und die nie benutzten Einträge sollen ans Ende.
_SEITENORDNUNG = (
    AiMemoryEntry.last_used_at.is_(None),
    AiMemoryEntry.last_used_at.desc(),
    # Bis 26.09.2026 stand hier der Name. Er ist seitdem verschluesselt und
    # sortierte nach Chiffrat; das Neueste zuerst ist bei Gleichstand ohnehin
    # die nuetzlichere Reihenfolge, und die ID macht sie eindeutig.
    AiMemoryEntry.created_at.desc(),
    AiMemoryEntry.id,
)


#: Was eine Ansicht zeigen kann: was gilt, oder was die KI vergessen hat und
#: sich noch zurueckholen laesst.
ANSICHTEN = ("aktiv", "vergessen")


def _ansicht(
    basis: Query, status: str, themen: list[str] | None
) -> Query:
    """Schraenkt eine Bestandsabfrage auf eine Ansicht und Themen ein.

    Vergessenes steht neuestes zuerst nach dem Zeitpunkt des Vergessens; die
    Frist laeuft ab dort.
    """
    if status not in ANSICHTEN:
        raise HTTPException(status_code=422, detail="Unbekannte Ansicht")
    abfrage = basis.filter(AiMemoryEntry.status == status)
    if themen:
        abfrage = abfrage.filter(AiMemoryEntry.thema_id.in_(themen))
    return abfrage


def _seite(
    basis: Query, offset: int, *, loeschbar: int | None = None, status: str = "aktiv"
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
    ordnung = (
        (AiMemoryEntry.vergessen_am.desc(), AiMemoryEntry.id)
        if status == "vergessen"
        else _SEITENORDNUNG
    )
    rows = (
        basis.order_by(*ordnung)
        .offset(max(0, offset))
        .limit(PERSONAL_PAGE_SIZE)
        .all()
    )
    gesamt = basis.count()
    eintraege = _entschluesseln_lesbare(rows)
    return Gedaechtnisseite(
        eintraege=eintraege,
        gesamt=gesamt,
        loeschbar=gesamt if loeschbar is None else loeschbar,
        themen=themennamen(
            basis.session, [(row.thema_id, row.scope_identity) for row, _wert in eintraege]
        ),
    )


def scope_entries(
    db: Session, user: User, scope: str, server_id: int | None = None,
    team_id: int | None = None, *, offset: int = 0, status: str = "aktiv",
    themen: list[str] | None = None,
) -> Gedaechtnisseite:
    """Eine Seite **eines** Bereichs — Team, Panel oder das Wissen einer Anlage.

    Dieselbe Auswahl wie `list_entries`, nur in Stücken. Von den drei Bereichen
    braucht das heute genau einer: `panel` und `server_shared` hängen an der
    festen `ai_limit_service.MAX_SYSTEM_SCOPE_ENTRIES` und passen damit immer
    auf eine Seite, `team` hängt am Rollenlimit seines Gründers und darf seit
    dem 19.08.2026 bis zu 5.000 Einträge fassen, seit dem 05.10.2026 bei
    „Unbegrenzt“ beliebig viele. Jeder davon kostet beim Öffnen
    einen eigenen Roundtrip zum DIS-Sidecar — gemessen 10,3 s bei 5.000 Zeilen,
    also dieselbe Wartezeit, gegen die die Profilansicht längst geschützt ist.

    Ein stiller Deckel wäre hier so falsch wie dort: wer diese Liste aufruft,
    will aufräumen, und eine Ansicht, die 300 von 5.000 zeigt, versteckt genau
    die Zeilen, wegen denen er gekommen ist. `gesamt` steht deshalb daneben, und
    die nächste Seite ist einen Klick entfernt.

    ``loeschbar`` zählt alles unter dieser Kennung, auch Vergessenes:
    `delete_all_entries` räumt genau diese eine Kennung ab, und die
    Bestätigungsfrage nennt die Zahl, die danach wirklich fehlt. In der
    Profilansicht ist das anders — dort liegen zwei Bereiche in einer Liste.
    """
    identity, _, _, _ = scope_identity(db, user, scope, server_id, team_id)
    bereich = db.query(AiMemoryEntry).filter(AiMemoryEntry.scope_identity == identity)
    return _seite(
        _ansicht(bereich, status, themen),
        offset,
        loeschbar=bereich.count(),
        status=status,
    )


def personal_entries(
    db: Session, user: User, *, offset: int = 0, status: str = "aktiv",
    themen: list[str] | None = None,
) -> Gedaechtnisseite:
    """Eine Seite von allem, was diesem Benutzer selbst gehoert.

    `list_entries` fragt genau eine Scope-Kennung ab und braucht dafuer bei
    serverbezogenen Notizen eine konkrete `server_id`. Damit war der Bereich
    ueber die Oberflaeche nicht erreichbar: die KI schreibt solche Notizen
    (scope='server'), sie fliessen in jeden Chat und zaehlen
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
        _ansicht(basis, status, themen),
        offset,
        # Die einzige Ansicht, in der "Alle löschen" weniger trifft, als die
        # Liste zeigt: die Servernotizen stehen mit drin und bleiben stehen.
        # Vergessenes unter `user:{id}` geht dagegen mit und zählt mit.
        loeschbar=basis.filter(AiMemoryEntry.scope == "user").count(),
        status=status,
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
    db: Session, user: User, scope: str, server_id: int | None, team_id: int | None
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

    Sitzung und Benutzer stehen in der Signatur wegen des Teams: dessen Name
    ist das einzige Stück dieses Satzes, das nicht schon in den Argumenten
    liegt — und wie er lauten muss, hängt daran, in welchen Teams der Benutzer
    sonst noch ist (siehe unten). Ohne beides wäre der Satz für ein Team
    wertlos.
    """
    if scope == "user":
        return "dein persönliches Gedächtnis"
    if scope == "server":
        return f"deine Notizen zu Server {server_id}"
    if scope == "server_shared":
        return f"das Wissen von Server {server_id}"
    if scope == "team":
        # Der Name, nicht die Nummer: diese Absage liest ein Mensch, und
        # „Team 42“ benennt für ihn nichts. Der **ansprechbare** Name statt
        # des blanken, weil Teamnamen nur je Gründer eindeutig sind — ist der
        # Benutzer in zwei Teams namens „Alpha“, benennt „Alpha“ beide, und
        # `ansprechbarer_name` hängt dann den Gründer an.
        ziel = db.get(Team, team_id) if team_id is not None else None
        if ziel is not None and ziel.name:
            from services import team_service

            gemeint = team_service.ansprechbarer_name(db, user, ziel)
            return f"das Wissen von Team „{gemeint}“"
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

    Eine Grenze, unverändert zu vorher: ein **leerer** Bereich hat keine Zeile
    zum Sperren, dort können zwei erste Einträge nebeneinander entstehen.
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

    Gezaehlt wird nur, was gilt. Eine vergessene Erinnerung belegt keinen
    Platz mehr: wer „vergiss das“ sagt, um Platz zu schaffen, soll ihn
    bekommen, auch wenn sich das Vergessene noch 30 Tage zurueckholen laesst.
    """
    _sperrzeile(db, identity).first()
    return _bestand(db, identity)


def _bestand(db: Session, identity: str) -> int:
    """Wieviele geltende Erinnerungen ein Bereich hat — ohne Sperre."""
    return int(
        db.query(func.count(AiMemoryEntry.id))
        .filter(AiMemoryEntry.scope_identity == identity, AiMemoryEntry.status == "aktiv")
        .scalar()
        or 0
    )


def _platz_sicherstellen(
    db: Session, user: User, scope: str, identity: str,
    server_id: int | None, team_id: int | None,
) -> None:
    """Wirft `MemoryScopeVoll`, wenn der Bereich keine weitere Erinnerung fasst.

    Gilt fuer jeden Weg, auf dem eine Erinnerung **dazukommt**: neu angelegt,
    aus dem Vergessen zurueckgeholt oder (Altbestand) unter einem neuen Namen
    gemerkt. Eine Aenderung an einer bestehenden kostet keinen Platz.

    Bei einer Grenze bleibt der Bereich bis zum Commit des Aufrufers gesperrt
    (`_bestand_unter_sperre`); die Zahl gilt also, bis die neue Zeile steht.
    """
    # Wieviel hier hineinpasst, entscheidet nicht mehr eine Konstante dieses
    # Moduls, sondern der Betreiber über das Rollenlimit — je nach Bereich
    # das des Schreibenden, das des Teamgründers oder die feste
    # Systemgrenze. Eine zweite Zahl hier daneben wäre eine zweite Wahrheit,
    # die mit der ersten auseinanderläuft.
    #
    # ``None`` heißt unbegrenzt — dann gibt es nichts zu zählen und nichts
    # zu sperren. Eine 0 ist dagegen eine Zahl und läuft durch die Zählung.
    # Das Ergebnis steht in einer Variablen, weil die Meldung es gleich
    # noch braucht.
    grenze = ai_limit_service.resolve_scope_memory_limit(
        db, scope, user, team_id=team_id, server_id=server_id,
    )
    bestand = _bestand_unter_sperre(db, identity) if grenze is not None else 0
    if grenze is not None and bestand >= grenze:
        # Hier steht die **Tatsache**, in Sätzen, die ein Mensch versteht —
        # und nichts sonst. Vorher stand hier eine Regieanweisung an das
        # Modell („Sag dem Benutzer …“, „Suche mit search_memory …“). Diese
        # Funktion hat aber zwei Adressaten: über `routers/ai_memory.py`
        # legt der Benutzer selbst einen Eintrag an, und `detail` wird ihm
        # als Toast vorgesetzt. Er las dort eine Anweisung an eine dritte
        # Instanz, über ihn selbst, mit Werkzeugnamen, die er nicht hat. Ein
        # Text, der beiden dienen soll, dient keinem. (Bis Stufe 2 übersetzte
        # `remember` die Tatsache für das Modell; der Gedächtnisschreiber
        # kennt den freien Platz heute vorher.)
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
        #   0-Fall, nur eine Stufe leiser.
        #
        # Verdrängt wird bewusst nichts von selbst: was ein Mensch gesagt
        # hat, wirft das Panel nicht ungefragt weg.
        bereich = _bereichsname(
            db, user, scope, server_id, team_id
        )
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


def upsert_entry(
    db: Session, *, user: User, scope: str, server_id: int | None, key: str, value: str,
    origin: str = "user", team_id: int | None = None, quelle: str | None = None,
) -> tuple[AiMemoryEntry, str]:
    """Legt einen Eintrag an oder ueberschreibt ihn unter demselben Schluessel.

    Das Ueberschreiben ist die Konfliktaufloesung des Gedaechtnisses: "ich will
    jetzt 16 GB" ersetzt "8 GB", statt beides nebeneinander stehen zu lassen.
    Deshalb ist der Schluessel die Identitaet eines Fakts — und deshalb bekam
    die KI, solange sie selbst schrieb (bis Stufe 2), die Anweisung, einen
    vorhandenen Schluessel wiederzuverwenden, statt einen aehnlichen anzulegen.

    ``origin`` unterscheidet eine Ansage des Benutzers von einer Ableitung der
    KI. Eine Ableitung ueberschreibt bewusst **keine** ausdrueckliche Ansage:
    was der Benutzer selbst gesagt hat, darf die KI nicht stillschweigend
    korrigieren. Die ausdrueckliche Korrektur (`replace_user_entry`) gab es
    nur fuer `remember`; seit Stufe 2 aendert der Gedaechtnisschreiber ueber
    `erinnerung_aendern`, mit der Nachricht des Menschen als Beleg.

    Der Weg der Namen, und damit Altbestand: seit Gedaechtnis v2 legt
    `erinnerung_anlegen` Erinnerungen ohne Namen an. Was hier entsteht, kommt
    mit ``quelle`` an (Vorgabe nach ``origin``: von der KI aus dem Gespraech,
    sonst eingetragen).
    """
    if origin not in {"user", "ai"}:
        raise HTTPException(status_code=422, detail="Unbekannte Memory-Herkunft")
    if quelle is not None and quelle not in QUELLEN:
        raise HTTPException(status_code=422, detail="Unbekannte Memory-Quelle")
    identity, owner_id, normalized_server_id, normalized_team_id = scope_identity(
        db, user, scope, server_id, team_id
    )
    _assert_may_write(db, user, scope, normalized_team_id, normalized_server_id)
    safe_value = _safe_value(value)
    row = db.query(AiMemoryEntry).filter(schluessel_bedingung(db, identity, [key])).first()
    action = "ai.memory.updated"
    if row is not None and row.status != "aktiv":
        # Unter diesem Namen steht eine vergessene Erinnerung. Wer ihn jetzt
        # wieder benutzt, merkt sich etwas neu: das kostet Platz wie eine neue
        # Zeile, und das Vergessene ist damit ueberschrieben.
        _platz_sicherstellen(
            db, user, scope, identity, normalized_server_id, normalized_team_id
        )
        row.status = "aktiv"
        row.vergessen_am = None
        row.origin = origin
        row.quelle = quelle or ("gespraech" if origin == "ai" else "eingetragen")
        action = "ai.memory.created"
    elif row is None:
        _platz_sicherstellen(
            db, user, scope, identity, normalized_server_id, normalized_team_id
        )
        row = AiMemoryEntry(
            id=str(uuid4()), owner_user_id=owner_id, server_id=normalized_server_id,
            team_id=normalized_team_id,
            scope=scope, scope_identity=identity, value_encrypted="",
            origin=origin, aad_version=2,
            quelle=quelle or ("gespraech" if origin == "ai" else "eingetragen"),
        )
        # Erst jetzt: die AAD des Namens braucht ID und Bereich der Zeile.
        row.key = key
        row.key_index = _schluessel_indizes(identity, [key])[0]
        db.add(row)
        action = "ai.memory.created"
    elif origin == "ai" and row.origin == "user":
        # Der Schutz gilt gegen die *stillschweigende* Korrektur: die KI leitet
        # nebenbei etwas ab und ueberschreibt damit, was der Benutzer selbst
        # gesagt hat.
        raise HTTPException(
            status_code=409,
            detail="Dieser Eintrag stammt vom Benutzer und wird nicht still überschrieben.",
        )
    else:
        # Die Herabstufung "user" -> "ai" hat der Zweig darueber schon
        # abgewiesen. Die Hochstufung "ai" -> "user" bleibt erlaubt: wer eine
        # Ableitung selbst bestaetigt, macht sie damit zu seiner eigenen Ansage
        # und sie verdient den Schutz.
        row.origin = origin
        # Der Stand davor bleibt als Fassung lesbar, wie bei jeder Aenderung
        # seit Gedaechtnis v2. Gelesen wird vor dem Umstellen der AAD unten.
        # Ein Stand, der sich nicht mehr oeffnen laesst, wird ohne Fassung
        # ueberschrieben; ihn zu behalten hiesse, den Eintrag zu sperren.
        try:
            bisher: str | None = DisClient.decrypt(row.value_encrypted, aad=_aad(row))
        except DisDecryptionError:
            bisher = None
        if bisher is not None and bisher != safe_value:
            _fassung_ablegen(
                db, row, bisher, row.titel,
                grund="aktualisiert" if origin == "ai" else "bearbeitet", von=origin,
            )
    # Jeder Schreibvorgang hebt den Eintrag auf die gebundene AAD. Bestandsdaten
    # aus Phase C wandern damit von selbst mit, sobald sie angefasst werden —
    # ohne Migrationsschritt, der den DIS-Sidecar voraussetzt.
    row.aad_version = 2
    row.value_encrypted = DisClient.encrypt(safe_value, aad=_aad(row))
    if action == "ai.memory.updated":
        row.fassung = int(row.fassung or 1) + 1
    # Der Vektor entsteht aus dem Klartext, bevor er verschluesselt wird —
    # danach waere er nicht mehr zu haben, ohne erneut zu entschluesseln.
    refresh_embedding(db, row, safe_value)
    row.updated_at = datetime.now(timezone.utc)
    audit_service.record_privileged_action(
        db, user_id=user.id, action=action, target_type="ai_memory", target_id=row.id,
        # Die Servernummer gehoert ins Protokoll, sobald der Bereich sie hat.
        # Ohne sie stuende dort "jemand hat Anlagenwissen geaendert" und nicht
        # bei welcher Anlage — und genau das ist die Frage, die ein Betreiber
        # spaeter stellt.
        #
        # Für das Team gilt derselbe Satz, und dort wiegt er schwerer: ein
        # Teameintrag hat bewusst keinen Besitzer (`owner_user_id` ist NULL),
        # und nach dem Löschen ist die Zeile weg. Das Protokoll ist damit der
        # einzige Ort, an dem die Zuordnung überlebt — bei einem Benutzer in
        # mehreren Teams sagte "scope: team" allein gar nichts.
        details={
            "scope": scope, "origin": origin,
            **({"server_id": normalized_server_id} if normalized_server_id else {}),
            **({"team_id": normalized_team_id} if normalized_team_id else {}),
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


def _titel_pruefen(titel: str | None) -> str | None:
    """Ein Titel ist eine Zeile, kurz und ohne Zugangsdaten — oder keiner."""
    if titel is None:
        return None
    normal = " ".join(str(titel).split())
    if not normal:
        return None
    if len(normal) > MAX_TITEL_ZEICHEN:
        raise HTTPException(status_code=422, detail="Der Titel ist zu lang")
    if enthaelt_zugangsdaten(normal):
        raise HTTPException(status_code=422, detail="Memory darf keine Zugangsdaten enthalten")
    return normal


def _themenname(name: str | None) -> str | None:
    """Ein Themenname in der Form, in der er verglichen und gezeigt wird."""
    if name is None:
        return None
    normal = " ".join(str(name).split())
    if not normal:
        return None
    if len(normal) > MAX_THEMA_ZEICHEN:
        raise HTTPException(status_code=422, detail="Der Themenname ist zu lang")
    if enthaelt_zugangsdaten(normal):
        raise HTTPException(status_code=422, detail="Memory darf keine Zugangsdaten enthalten")
    return normal


def _art_pruefen(art: str | None) -> str | None:
    if art is not None and art not in ARTEN:
        raise HTTPException(status_code=422, detail="Unbekannte Art der Erinnerung")
    return art


def _wichtigkeit_pruefen(wichtigkeit: int) -> int:
    # `True` ist in Python eine 1. Eine Wichtigkeit ist eine Zahl, kein
    # Schalter (AGENTS.md, Punkt 97).
    if isinstance(wichtigkeit, bool) or not isinstance(wichtigkeit, int) or not 1 <= wichtigkeit <= 5:
        raise HTTPException(status_code=422, detail="Wichtigkeit liegt zwischen 1 und 5")
    return wichtigkeit


def thema_fuer(
    db: Session, *, identity: str, owner_id: int | None, server_id: int | None,
    team_id: int | None, name: str | None,
) -> AiMemoryTopic | None:
    """Das Thema eines Bereichs mit diesem Namen — gefunden oder neu angelegt.

    Verglichen wird ohne Gross- und Kleinschreibung: „Familie“ und „familie“
    sind ein Thema, gezeigt wird die Schreibweise, mit der es entstand. Legen
    zwei Schreiber dasselbe Thema gleichzeitig an, gewinnt einer am UNIQUE,
    und der andere nimmt dessen Zeile.
    """
    normal = _themenname(name)
    if normal is None:
        return None
    index = DisClient.blind_index([f"{identity}\nthema\n{normal.casefold()}"])[0]
    jetzt = datetime.now(timezone.utc)
    # Gesperrt: räumt `vergessene_aufraeumen` dieses Thema gerade ab, wartet
    # die Abfrage auf dessen Ende und findet dann nichts, statt eine Zeile zu
    # benutzen, die es gleich nicht mehr gibt.
    thema = (
        db.query(AiMemoryTopic)
        .filter(AiMemoryTopic.scope_identity == identity, AiMemoryTopic.name_index == index)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if thema is not None:
        # Wer ein Thema benutzt, haelt es frisch: `vergessene_aufraeumen`
        # raeumt nur Themen ab, die eine Weile niemand mehr angefasst hat.
        thema.updated_at = jetzt
        return thema
    thema = AiMemoryTopic(
        id=str(uuid4()), scope_identity=identity, owner_user_id=owner_id,
        server_id=server_id, team_id=team_id, name_encrypted="", name_index=index,
        created_at=jetzt, updated_at=jetzt,
    )
    thema.name_encrypted = DisClient.encrypt(normal, aad=thema.name_aad())
    try:
        with db.begin_nested():
            db.add(thema)
    except IntegrityError:
        thema = (
            db.query(AiMemoryTopic)
            .filter(AiMemoryTopic.scope_identity == identity, AiMemoryTopic.name_index == index)
            .first()
        )
    return thema


def themennamen(db: Session, paare: Iterable[tuple[str | None, str]]) -> dict[str, str]:
    """Klartext der Themennamen, in einem Sidecar-Aufruf.

    ``paare`` sind (Thema, Bereich) der Erinnerungen, die gezeigt werden.
    Geöffnet wird ein Name nur, wenn das Thema in genau diesem Bereich steht.
    Die AAD des Namens bindet ihn an sein eigenes Thema, nicht an die
    Erinnerung: wer per Datenbankzugriff die `thema_id` seiner Erinnerung auf
    ein fremdes Thema zeigen ließ, bekam bis 06.10.2026 dessen Namen im
    Klartext — genau der Weg, den die AAD bei Text, Titel und Fassungen
    versperrt.

    Ein Name, der sich nicht oeffnen laesst, fehlt im Ergebnis; die
    Erinnerung erscheint dann ohne Thema, statt die Seite zu kippen.
    """
    gesucht = {(kennung, bereich) for kennung, bereich in paare if kennung}
    if not gesucht:
        return {}
    themen = [
        thema
        for thema in db.query(AiMemoryTopic)
        .filter(AiMemoryTopic.id.in_(sorted({kennung for kennung, _ in gesucht})))
        .all()
        if (thema.id, thema.scope_identity) in gesucht
    ]
    klartexte = _sammeln([(thema.name_encrypted, thema.name_aad()) for thema in themen])
    return {
        thema.id: klar for thema, klar in zip(themen, klartexte) if klar is not None
    }


@dataclass(frozen=True)
class Themenzeile:
    """Ein Thema einer Ansicht und wieviele geltende Erinnerungen darunter stehen."""

    id: str
    name: str
    anzahl: int


#: Wieviele Themen eine Ansicht hoechstens auflistet, die vollsten zuerst.
#: Ein Bereich mit 100.000 Erinnerungen kann tausende Themen haben; eine
#: Filterleiste mit tausend Eintraegen hilft niemandem und kostet je Aufruf
#: eine Entschluesselung je Name.
MAX_THEMEN_LISTE = 300


def _themen_der_ansicht(db: Session, basis: Query) -> list[Themenzeile]:
    # Nur Themen aus dem Bereich der Erinnerung selbst (siehe `themennamen`).
    zahlen = (
        basis.filter(AiMemoryEntry.status == "aktiv")
        .join(
            AiMemoryTopic,
            and_(
                AiMemoryTopic.id == AiMemoryEntry.thema_id,
                AiMemoryTopic.scope_identity == AiMemoryEntry.scope_identity,
            ),
        )
        .with_entities(
            AiMemoryEntry.thema_id, AiMemoryEntry.scope_identity, func.count(AiMemoryEntry.id)
        )
        .group_by(AiMemoryEntry.thema_id, AiMemoryEntry.scope_identity)
        .order_by(func.count(AiMemoryEntry.id).desc(), AiMemoryEntry.thema_id)
        .limit(MAX_THEMEN_LISTE)
        .all()
    )
    namen = themennamen(db, [(kennung, bereich) for kennung, bereich, _anzahl in zahlen])
    zeilen = [
        Themenzeile(id=kennung, name=namen[kennung], anzahl=int(anzahl))
        for kennung, _bereich, anzahl in zahlen
        if kennung in namen
    ]
    return sorted(zeilen, key=lambda zeile: (zeile.name.casefold(), zeile.id))


def scope_themen(
    db: Session, user: User, scope: str, server_id: int | None = None,
    team_id: int | None = None,
) -> list[Themenzeile]:
    """Die Themen eines Bereichs mit ihrer Zahl — die Filterleiste der Ansicht."""
    identity, _, _, _ = scope_identity(db, user, scope, server_id, team_id)
    return _themen_der_ansicht(
        db, db.query(AiMemoryEntry).filter(AiMemoryEntry.scope_identity == identity)
    )


def personal_themen(db: Session, user: User) -> list[Themenzeile]:
    """Die Themen der eigenen Erinnerungen, allgemein und zu Servern zusammen.

    Dasselbe Wort kann hier zweimal stehen ("Minecraft" allgemein und zu
    Server 62): es sind zwei Bereiche mit je eigenem Thema. Die Oberflaeche
    fasst gleiche Namen zu einem Filter zusammen.
    """
    return _themen_der_ansicht(
        db,
        db.query(AiMemoryEntry).filter(
            AiMemoryEntry.owner_user_id == user.id,
            AiMemoryEntry.scope.in_(PERSOENLICHE_SCOPES),
        ),
    )


def _fassung_ablegen(
    db: Session, row: AiMemoryEntry, text: str, titel: str | None, *, grund: str, von: str,
) -> AiMemoryVersion:
    """Legt den bisherigen Stand einer Erinnerung als fruehere Fassung ab.

    Danach bleiben die juengsten `MAX_VERSIONEN`; aeltere fallen weg.
    """
    if grund not in GRUENDE or von not in {"user", "ai"}:
        raise ValueError(f"unbekannte Fassung: {grund}/{von}")
    fassung = AiMemoryVersion(
        id=str(uuid4()), memory_id=row.id, scope_identity=row.scope_identity,
        text_encrypted="", grund=grund, von=von, created_at=datetime.now(timezone.utc),
    )
    fassung.text_encrypted = DisClient.encrypt(text, aad=_versions_aad(fassung, "text"))
    if titel:
        fassung.titel_encrypted = DisClient.encrypt(titel, aad=_versions_aad(fassung, "titel"))
    db.add(fassung)
    db.flush()
    ueberzaehlig = [
        kennung
        for (kennung,) in db.query(AiMemoryVersion.id)
        .filter(AiMemoryVersion.memory_id == row.id)
        .order_by(AiMemoryVersion.created_at.desc(), AiMemoryVersion.id.desc())
        .offset(MAX_VERSIONEN)
        .all()
    ]
    if ueberzaehlig:
        db.query(AiMemoryVersion).filter(AiMemoryVersion.id.in_(ueberzaehlig)).delete(
            synchronize_session=False
        )
    return fassung


def erinnerung_anlegen(
    db: Session,
    *,
    user: User,
    scope: str,
    text: str,
    server_id: int | None = None,
    team_id: int | None = None,
    titel: str | None = None,
    thema: str | None = None,
    art: str | None = None,
    wichtigkeit: int = 3,
    quelle: str = "eingetragen",
    quelle_ref: str | None = None,
    origin: str = "user",
    faellig_am: date | None = None,
    commit: bool = True,
) -> tuple[AiMemoryEntry, str]:
    """Legt eine Erinnerung an: ein bis fuenf Saetze, ohne Namen.

    Dieselben Grenzen wie jeder Schreibweg: Rechte des Bereichs
    (`_assert_may_write`), keine Zugangsdaten, und das Rollenlimit des
    Bereichs (`_platz_sicherstellen`): 100, eine eingestellte Zahl oder
    unbegrenzt, 0 heisst gesperrt.

    ``commit=False`` schreibt nur in die laufende Transaktion; der Aufrufer
    (Import, Pflege) schreibt dann mehrere Erinnerungen in einem Zug fest.
    """
    if origin not in {"user", "ai"}:
        raise HTTPException(status_code=422, detail="Unbekannte Memory-Herkunft")
    if quelle not in QUELLEN:
        raise HTTPException(status_code=422, detail="Unbekannte Memory-Quelle")
    _art_pruefen(art)
    _wichtigkeit_pruefen(wichtigkeit)
    identity, owner_id, sid, tid = scope_identity(db, user, scope, server_id, team_id)
    _assert_may_write(db, user, scope, tid, sid)
    sauber = _safe_value(text)
    sauberer_titel = _titel_pruefen(titel)
    _platz_sicherstellen(db, user, scope, identity, sid, tid)
    gefunden = thema_fuer(
        db, identity=identity, owner_id=owner_id, server_id=sid, team_id=tid, name=thema,
    )
    jetzt = datetime.now(timezone.utc)
    row = AiMemoryEntry(
        id=str(uuid4()), owner_user_id=owner_id, server_id=sid, team_id=tid,
        scope=scope, scope_identity=identity, value_encrypted="", origin=origin,
        aad_version=2, quelle=quelle, quelle_ref=quelle_ref, art=art,
        wichtigkeit=wichtigkeit, faellig_am=faellig_am, status="aktiv", fassung=1,
        thema_id=gefunden.id if gefunden is not None else None,
        created_at=jetzt, updated_at=jetzt,
    )
    row.value_encrypted = DisClient.encrypt(sauber, aad=_aad(row))
    row.titel = sauberer_titel
    refresh_embedding(db, row, sauber)
    db.add(row)
    audit_service.record_privileged_action(
        db, user_id=user.id, action="ai.memory.created", target_type="ai_memory",
        target_id=row.id,
        details=_protokolldetails(row, origin=origin, quelle=quelle),
        origin="ai" if origin == "ai" else "direct",
    )
    if not commit:
        db.flush()
        return row, sauber
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Die Erinnerung wurde gleichzeitig angelegt oder geändert. Bitte noch einmal versuchen.",
        ) from exc
    db.refresh(row)
    return row, sauber


#: Steht fuer "dieses Feld nicht anfassen" — im Unterschied zu ``None``, das
#: bei Titel und Thema "entfernen" heisst.
UNVERAENDERT = object()


def erinnerung_aendern(
    db: Session,
    *,
    user: User,
    entry_id: str,
    text: str | None = None,
    titel: object = UNVERAENDERT,
    thema: object = UNVERAENDERT,
    art: object = UNVERAENDERT,
    wichtigkeit: int | None = None,
    erwartete_fassung: int | None = None,
    von: str = "user",
    grund: str = "bearbeitet",
    ueberschreibt_mensch: bool = False,
    commit: bool = True,
) -> tuple[AiMemoryEntry, str]:
    """Aendert eine Erinnerung und legt den Stand davor als Fassung ab.

    ``erwartete_fassung`` ist die Fassung, die der Aendernde gesehen hat.
    Passt sie nicht mehr, hat inzwischen jemand anderes geschrieben (ein
    zweites Fenster, die Pflege im Hintergrund), und die Aenderung wird mit
    409 abgelehnt statt dessen Stand still zu ueberschreiben.

    Aendert ein Mensch den Text, ist es danach seine Aussage (``origin``
    wird ``user``). Die KI (``von='ai'``) aendert Text oder Titel einer
    Aussage des Menschen nur mit ``ueberschreibt_mensch``: derselbe Schutz wie
    beim Namen (`upsert_entry`). Thema, Art und
    Wichtigkeit ordnet sie dagegen frei zu; das sagt nichts anderes, als der
    Mensch gesagt hat.
    """
    if von not in {"user", "ai"}:
        raise ValueError("von ist 'user' oder 'ai'")
    row = _zeile(db, user, entry_id, aendern=True)
    if row.status != "aktiv":
        raise HTTPException(
            status_code=409,
            detail="Diese Erinnerung ist vergessen. Hol sie zuerst zurück.",
        )
    if erwartete_fassung is not None and int(row.fassung or 1) != erwartete_fassung:
        raise HTTPException(
            status_code=409,
            detail="Die Erinnerung wurde inzwischen geändert. Lade neu und versuch es noch einmal.",
        )
    alter_text = DisClient.decrypt(row.value_encrypted, aad=_aad(row))
    alter_titel = row.titel
    neuer_text = _safe_value(text) if text is not None else alter_text
    neuer_titel = alter_titel if titel is UNVERAENDERT else _titel_pruefen(titel)
    geaendert = False
    if neuer_text != alter_text or neuer_titel != alter_titel:
        if von == "ai" and row.origin == "user" and not ueberschreibt_mensch:
            raise HTTPException(
                status_code=409,
                detail="Diese Erinnerung stammt vom Benutzer und wird nicht still überschrieben.",
            )
        _fassung_ablegen(db, row, alter_text, alter_titel, grund=grund, von=von)
        row.aad_version = 2
        row.value_encrypted = DisClient.encrypt(neuer_text, aad=_aad(row))
        row.titel = neuer_titel
        refresh_embedding(db, row, neuer_text)
        if von == "user":
            row.origin = "user"
        geaendert = True
    if thema is not UNVERAENDERT:
        gefunden = thema_fuer(
            db, identity=row.scope_identity, owner_id=row.owner_user_id,
            server_id=row.server_id, team_id=row.team_id, name=thema,
        )
        neue_id = gefunden.id if gefunden is not None else None
        if neue_id != row.thema_id:
            row.thema_id = neue_id
            geaendert = True
    if art is not UNVERAENDERT and art != row.art:
        row.art = _art_pruefen(art)
        geaendert = True
    if wichtigkeit is not None and wichtigkeit != row.wichtigkeit:
        row.wichtigkeit = _wichtigkeit_pruefen(wichtigkeit)
        geaendert = True
    if geaendert:
        row.fassung = int(row.fassung or 1) + 1
        row.updated_at = datetime.now(timezone.utc)
        audit_service.record_privileged_action(
            db, user_id=user.id, action="ai.memory.updated", target_type="ai_memory",
            target_id=row.id,
            details=_protokolldetails(row, origin=von, grund=grund),
            origin="ai" if von == "ai" else "direct",
        )
    if not commit:
        db.flush()
        return row, neuer_text
    db.commit()
    db.refresh(row)
    return row, neuer_text


def _vergessene_begrenzen(db: Session, identity: str) -> None:
    """Haelt den Vorrat vergessener Erinnerungen eines Bereichs bei `MAX_VERGESSENE`."""
    ueberzaehlig = [
        kennung
        for (kennung,) in db.query(AiMemoryEntry.id)
        .filter(AiMemoryEntry.scope_identity == identity, AiMemoryEntry.status == "vergessen")
        .order_by(AiMemoryEntry.vergessen_am.desc(), AiMemoryEntry.id.desc())
        .offset(MAX_VERGESSENE)
        .all()
    ]
    if ueberzaehlig:
        db.query(AiMemoryEntry).filter(AiMemoryEntry.id.in_(ueberzaehlig)).delete(
            synchronize_session=False
        )


def erinnerung_vergessen(
    db: Session, *, user: User, entry_id: str, commit: bool = True
) -> AiMemoryEntry:
    """Die KI vergisst eine Erinnerung — zurueckholbar fuer `VERGESSEN_TAGE`.

    Ab sofort zaehlt sie nicht mehr, erscheint in keinem Kontext und keiner
    Suche mehr. Nur die Ansicht „Vergessen“ zeigt sie noch, mit der
    Moeglichkeit, sie zurueckzuholen oder endgueltig zu loeschen. Der Grund
    fuer die Frist: hier hat die KI einen Wunsch gedeutet, und eine Deutung
    kann danebenliegen.
    """
    row = _zeile(db, user, entry_id, aendern=True)
    if row.status != "vergessen":
        jetzt = datetime.now(timezone.utc)
        row.status = "vergessen"
        row.vergessen_am = jetzt
        row.updated_at = jetzt
        audit_service.record_privileged_action(
            db, user_id=user.id, action="ai.memory.forgotten", target_type="ai_memory",
            target_id=row.id, details=_protokolldetails(row), origin="ai",
        )
        db.flush()
        _vergessene_begrenzen(db, row.scope_identity)
    if commit:
        db.commit()
    return row


def erinnerung_zurueckholen(
    db: Session, *, user: User, entry_id: str
) -> tuple[AiMemoryEntry, str]:
    """Holt eine vergessene Erinnerung zurueck — wenn der Bereich sie noch fasst.

    Sie zaehlt danach wieder gegen das Rollenlimit. Ist der Bereich inzwischen
    voll, sagt `MemoryScopeVoll` das, statt die Grenze zu ueberschreiten.
    """
    row = _zeile(db, user, entry_id, aendern=True)
    if row.status != "aktiv":
        _platz_sicherstellen(
            db, user, row.scope, row.scope_identity, row.server_id, row.team_id
        )
        row.status = "aktiv"
        row.vergessen_am = None
        row.updated_at = datetime.now(timezone.utc)
        audit_service.record_privileged_action(
            db, user_id=user.id, action="ai.memory.restored", target_type="ai_memory",
            target_id=row.id, details=_protokolldetails(row), origin="direct",
        )
        db.commit()
        db.refresh(row)
    return row, DisClient.decrypt(row.value_encrypted, aad=_aad(row))


@dataclass(frozen=True)
class Fassung:
    """Eine fruehere Fassung im Klartext, wie die Oberflaeche sie zeigt."""

    id: str
    text: str
    titel: str | None
    grund: str
    von: str
    erstellt: datetime


def fassungen(db: Session, *, user: User, entry_id: str) -> list[Fassung]:
    """Die frueheren Fassungen einer Erinnerung, die juengste zuerst.

    Lesen darf sie, wer die Erinnerung sehen darf. Eine Fassung, die sich
    nicht oeffnen laesst, fehlt in der Liste.
    """
    row = _zeile(db, user, entry_id, aendern=False)
    zeilen = (
        db.query(AiMemoryVersion)
        .filter(AiMemoryVersion.memory_id == row.id)
        .order_by(AiMemoryVersion.created_at.desc(), AiMemoryVersion.id.desc())
        .limit(MAX_VERSIONEN)
        .all()
    )
    paare: list[tuple[str, str]] = []
    for zeile in zeilen:
        paare.append((zeile.text_encrypted, _versions_aad(zeile, "text")))
        if zeile.titel_encrypted:
            paare.append((zeile.titel_encrypted, _versions_aad(zeile, "titel")))
    klartexte = iter(_sammeln(paare))
    ergebnis: list[Fassung] = []
    for zeile in zeilen:
        text = next(klartexte)
        titel = next(klartexte) if zeile.titel_encrypted else None
        if text is None:
            continue
        ergebnis.append(Fassung(
            id=zeile.id, text=text, titel=titel, grund=zeile.grund, von=zeile.von,
            erstellt=_utc(zeile.created_at),
        ))
    return ergebnis


def fassung_zurueckholen(
    db: Session, *, user: User, entry_id: str, fassung_id: str,
    erwartete_fassung: int | None = None,
) -> tuple[AiMemoryEntry, str]:
    """Setzt eine Erinnerung auf eine fruehere Fassung zurueck.

    Der Stand davor wird selbst zu einer Fassung (``wiederhergestellt``): wer
    sich vergreift, kommt auf demselben Weg zurueck.
    """
    row = _zeile(db, user, entry_id, aendern=True)
    zeile = db.get(AiMemoryVersion, fassung_id)
    if zeile is None or zeile.memory_id != row.id:
        raise HTTPException(status_code=404, detail="Fassung nicht gefunden")
    text = DisClient.decrypt(zeile.text_encrypted, aad=_versions_aad(zeile, "text"))
    titel = (
        DisClient.decrypt(zeile.titel_encrypted, aad=_versions_aad(zeile, "titel"))
        if zeile.titel_encrypted
        else None
    )
    return erinnerung_aendern(
        db, user=user, entry_id=row.id, text=text, titel=titel,
        erwartete_fassung=erwartete_fassung, von="user", grund="wiederhergestellt",
    )


def erinnerung_aufnehmen(
    db: Session,
    *,
    user: User,
    ziel_id: str,
    quelle_id: str,
    ueberschreibt_mensch: bool = False,
    commit: bool = True,
) -> AiMemoryEntry:
    """Eine Erinnerung geht in einer anderen auf — die Haelfte einer Synthese.

    Die andere Haelfte, den neuen Text des Ziels, schreibt der Aufrufer vorher
    mit `erinnerung_aendern` (Grund ``zusammengefuehrt``). Hier wird der Text
    der aufgehenden Erinnerung zu einer frueheren Fassung **des Ziels**
    (Grund ``aufgenommen``) — im Verlauf des Ziels steht danach, woraus es
    entstanden ist —, und die aufgehende wird vergessen: 30 Tage
    zurueckholbar, wie alles, was die KI vergisst.

    Nur innerhalb eines Bereichs: eine persoenliche Notiz darf nicht im
    Teamwissen aufgehen, sonst laese sie das Team. Eine Erinnerung des
    Menschen geht nur mit ``ueberschreibt_mensch`` auf — derselbe Schutz wie
    beim Aendern ihres Textes.
    """
    ziel = _zeile(db, user, ziel_id, aendern=True)
    quelle = _zeile(db, user, quelle_id, aendern=True)
    if ziel.id == quelle.id or ziel.scope_identity != quelle.scope_identity:
        raise HTTPException(
            status_code=422,
            detail="Zusammengefuehrt wird nur innerhalb eines Bereichs.",
        )
    if ziel.status != "aktiv" or quelle.status != "aktiv":
        raise HTTPException(
            status_code=409,
            detail="Diese Erinnerung ist vergessen. Hol sie zuerst zurück.",
        )
    if quelle.origin == "user" and not ueberschreibt_mensch:
        raise HTTPException(
            status_code=409,
            detail="Diese Erinnerung stammt vom Benutzer und wird nicht still überschrieben.",
        )
    _schluessel_laden([quelle])
    text = DisClient.decrypt(quelle.value_encrypted, aad=_aad(quelle))
    _fassung_ablegen(
        db, ziel, text, quelle.titel or quelle.key, grund="aufgenommen", von="ai"
    )
    erinnerung_vergessen(db, user=user, entry_id=quelle.id, commit=False)
    if commit:
        db.commit()
    return ziel


def _name_als_titel(name: str) -> str | None:
    """Der alte Name als Titel: aus `backup.zeitpunkt` wird „Backup zeitpunkt“.

    Punkte und Unterstriche trennten im Namen Wörter, die ein Titel mit
    Leerzeichen trennt; mehr als den ersten Buchstaben fasst die Umstellung
    nicht an. Einen Titel daraus zu formulieren, ist Sache des Modells.
    """
    lesbar = " ".join(name.replace("_", " ").replace(".", " ").split())
    if not lesbar:
        return None
    try:
        return _titel_pruefen(lesbar[:1].upper() + lesbar[1:])
    except HTTPException:
        return None


def altbestand_umschreiben(
    db: Session,
    *,
    user: User,
    entry_id: str,
    text: str | None,
    titel: str | None = None,
    thema: object = UNVERAENDERT,
    art: object = UNVERAENDERT,
    wichtigkeit: int | None = None,
    erwartete_fassung: int | None = None,
) -> AiMemoryEntry:
    """Bringt eine Erinnerung mit Namen in die Form von Gedächtnis v2.

    Danach hat sie weder Namen noch Index und ist ein Satz wie jede neue
    (`ai_gedaechtnis_altbestand`). ``text`` ist der Satz, den das Modell aus
    Name und Wert gemacht hat; der Stand davor bleibt als Fassung (Grund
    ``umgeschrieben``), mit dem Namen als Titel — wer wissen will, was vorher
    dastand, findet es im Verlauf. Die Fassung entsteht auch, wenn der Text
    bleibt: sonst stünde der Name nirgends mehr. ``text=None`` stellt
    mechanisch um: der Wert bleibt der Text, der Name wird der Titel. Einen
    Titel, den die Erinnerung schon trägt, hat ein Mensch gesetzt; er bleibt.

    Die Herkunft bleibt, wie sie ist, und anders als in `erinnerung_aendern`
    gibt es keinen Schutz für die Aussage des Menschen: umgeschrieben wird
    ihre Form, nicht ihr Inhalt, und ihr Wortlaut steht daneben in der Fassung.
    Committet nicht.
    """
    row = _zeile(db, user, entry_id, aendern=True)
    if row.status != "aktiv":
        raise HTTPException(
            status_code=409,
            detail="Diese Erinnerung ist vergessen. Hol sie zuerst zurück.",
        )
    if erwartete_fassung is not None and int(row.fassung or 1) != erwartete_fassung:
        raise HTTPException(
            status_code=409,
            detail="Die Erinnerung wurde inzwischen geändert. Lade neu und versuch es noch einmal.",
        )
    if row.key_encrypted is None:
        return row
    _schluessel_laden([row])
    name = row.key
    alter_text = DisClient.decrypt(row.value_encrypted, aad=_aad(row))
    alter_titel = row.titel
    neuer_text = alter_text if text is None else _safe_value(text)
    neuer_titel = alter_titel or (_titel_pruefen(titel) if titel else None) or _name_als_titel(name)
    _fassung_ablegen(
        db, row, alter_text, name or alter_titel or None, grund="umgeschrieben", von="ai"
    )
    if neuer_text != alter_text:
        row.aad_version = 2
        row.value_encrypted = DisClient.encrypt(neuer_text, aad=_aad(row))
    if thema is not UNVERAENDERT:
        gefunden = thema_fuer(
            db, identity=row.scope_identity, owner_id=row.owner_user_id,
            server_id=row.server_id, team_id=row.team_id, name=thema,
        )
        row.thema_id = gefunden.id if gefunden is not None else None
    if art is not UNVERAENDERT:
        row.art = _art_pruefen(art)
    if wichtigkeit is not None:
        row.wichtigkeit = _wichtigkeit_pruefen(wichtigkeit)
    row.key_encrypted = None
    row.key_index = None
    row.__dict__.pop("_key_klartext", None)
    row.titel = neuer_titel
    # Nach dem Titel: der Vektor entsteht aus Titel und Text, nicht mehr aus
    # dem Namen (`_einbettungstext`).
    refresh_embedding(db, row, neuer_text)
    row.fassung = int(row.fassung or 1) + 1
    row.updated_at = datetime.now(timezone.utc)
    audit_service.record_privileged_action(
        db, user_id=user.id, action="ai.memory.updated", target_type="ai_memory",
        target_id=row.id,
        details=_protokolldetails(
            row, origin="ai", grund="umgeschrieben" if text is not None else "umgestellt",
        ),
        origin="ai",
    )
    db.flush()
    return row


def aehnlichkeit_zu_texten(
    db: Session, rows: list[AiMemoryEntry], texte: list[str]
) -> list[float | None]:
    """Je Zeile die hoechste Bedeutungsaehnlichkeit zu einem der Texte.

    Fuer den Gedaechtnisschreiber: er vergleicht den Bestand nicht mit einer
    Frage, sondern mit jeder Nachricht eines Gespraechsausschnitts einzeln.
    Ein Ausschnitt ueber drei Dinge, zu einem Vektor vermischt, traefe keines
    davon.

    ``None`` wie in `_similarities`: kein Vergleich moeglich (kein Modell,
    kein Vektor aus demselben Modell), nicht "unaehnlich". Gerechnet wird als
    eine Matrix, nicht je Text — bei einem unbegrenzten Vorrat sind das sonst
    hunderttausend Vektoren je Nachricht.
    """
    texte = [text for text in texte if text and text.strip()]
    if not rows or not texte:
        return [None] * len(rows)
    kodierung = ai_embedding_service.encode(texte, db=db)
    if kodierung is None or len(kodierung.vektoren) != len(texte):
        return [None] * len(rows)
    gespeichert = [_stored_vector(row, kodierung.modell) for row in rows]
    bekannt = [index for index, vektor in enumerate(gespeichert) if vektor is not None]
    if not bekannt:
        return [None] * len(rows)
    try:
        import numpy as np

        matrix = np.asarray([gespeichert[index] for index in bekannt], dtype="float32")
        fragen = np.asarray(kodierung.vektoren, dtype="float32")
        beste = (matrix @ fragen.T).max(axis=1).tolist()
    except Exception as exc:  # noqa: BLE001 - ohne Vergleich gilt die Ersatzordnung
        logger.warning("Aehnlichkeit zum Ausschnitt fehlgeschlagen error=%s", type(exc).__name__)
        return [None] * len(rows)
    ergebnis: list[float | None] = [None] * len(rows)
    for index, wert in zip(bekannt, beste):
        ergebnis[index] = float(wert)
    return ergebnis


def erinnerung_lesen(db: Session, *, user: User, entry_id: str) -> tuple[AiMemoryEntry, str]:
    """Eine Erinnerung, wie sie jetzt steht — fuer die Ansicht nach einem 409.

    Wer beim Speichern an einer neueren Fassung gescheitert ist, braucht genau
    diese eine Zeile, nicht die Seite: steht sie inzwischen auf einer anderen
    Seite (die Ordnung folgt dem letzten Gebrauch), fand die Ansicht sie dort
    nicht und hielt an der alten Fassung fest — jedes weitere Speichern bekam
    wieder 409. Eine Zeile, die sich nicht oeffnen laesst, heisst 404 wie auf
    der Seite, wo sie still fehlt.
    """
    row = _zeile(db, user, entry_id, aendern=False)
    geoeffnet = _entschluesseln_lesbare([row])
    if not geoeffnet:
        raise HTTPException(status_code=404, detail="Memory-Eintrag nicht gefunden")
    return geoeffnet[0]


#: Wieviele Zeilen ein Aufraeumlauf hoechstens loescht. Der Rest kommt im
#: naechsten Lauf dran; eine einzelne Anweisung ueber hunderttausend Zeilen
#: hielte Sperren, die der Chat danach zu spueren bekaeme.
_AUFRAEUMEN_JE_LAUF = 5_000


def vergessene_aufraeumen(db: Session, *, jetzt: datetime | None = None) -> int:
    """Loescht, was laenger als `VERGESSEN_TAGE` vergessen ist, und verwaiste Themen.

    Laeuft stuendlich im Hintergrund (`scheduler_service`). Ein Thema gilt als
    verwaist, wenn keine Erinnerung mehr darunter steht und es seit einer
    Stunde niemand angefasst hat: so faellt kein Thema weg, das ein Schreiber
    gerade erst angelegt hat und gleich benutzen will.
    """
    jetzt = jetzt or datetime.now(timezone.utc)
    frist = jetzt - timedelta(days=VERGESSEN_TAGE)
    abgelaufen = [
        kennung
        for (kennung,) in db.query(AiMemoryEntry.id)
        .filter(AiMemoryEntry.status == "vergessen", AiMemoryEntry.vergessen_am < frist)
        .limit(_AUFRAEUMEN_JE_LAUF)
        .all()
    ]
    # Gelöscht wird mit denselben Bedingungen, unter denen ausgewählt wurde.
    # Zwischen beiden Schritten kann jemand eine Erinnerung zurückholen oder
    # ein Thema wieder benutzen; beide halten dabei die Zeile gesperrt, und
    # PostgreSQL prüft die Bedingungen nach dem Warten an der neuen Fassung
    # der Zeile. Mit der Kennung allein fiele die zurückgeholte Erinnerung
    # trotzdem weg.
    geloescht = 0
    if abgelaufen:
        geloescht = db.query(AiMemoryEntry).filter(
            AiMemoryEntry.id.in_(abgelaufen),
            AiMemoryEntry.status == "vergessen",
            AiMemoryEntry.vergessen_am < frist,
        ).delete(synchronize_session=False)
    ruhig_seit = jetzt - timedelta(hours=1)
    benutzt = select(AiMemoryEntry.thema_id).where(AiMemoryEntry.thema_id.is_not(None))
    verwaist = [
        kennung
        for (kennung,) in db.query(AiMemoryTopic.id)
        .filter(AiMemoryTopic.updated_at < ruhig_seit, AiMemoryTopic.id.not_in(benutzt))
        .limit(_AUFRAEUMEN_JE_LAUF)
        .all()
    ]
    if verwaist:
        db.query(AiMemoryTopic).filter(
            AiMemoryTopic.id.in_(verwaist),
            AiMemoryTopic.updated_at < ruhig_seit,
            AiMemoryTopic.id.not_in(benutzt),
        ).delete(synchronize_session=False)
    db.commit()
    return geloescht


@dataclass(frozen=True)
class Importziel:
    """Wohin ein Import schreibt: der Bereich, geprüft wie beim Schreiben.

    ``grenze`` ist das Kontingent der Rolle für diesen Bereich — ``None``
    unbegrenzt, 0 gesperrt —, ``frei`` wieviele neue Erinnerungen er noch
    fasst (``None`` unbegrenzt). Gezählt wird wie in `_platz_sicherstellen`:
    nur, was gilt.
    """

    identity: str
    server_id: int | None
    team_id: int | None
    grenze: int | None
    frei: int | None


def importziel(
    db: Session, user: User, scope: str, server_id: int | None, team_id: int | None
) -> Importziel:
    """Prüft den Zielbereich eines Imports, bevor etwas gelesen wird.

    Dieselben Rechte wie beim Schreiben: wer in diesen Bereich nicht schreiben
    darf, bekommt die Absage vor dem Modellaufruf der Vorschau — nicht erst,
    nachdem der Text beim Anbieter war und der Benutzer ausgesucht hat.
    """
    identity, _owner, sid, tid = scope_identity(db, user, scope, server_id, team_id)
    _assert_may_write(db, user, scope, tid, sid)
    grenze = ai_limit_service.resolve_scope_memory_limit(
        db, scope, user, team_id=tid, server_id=sid,
    )
    bestand = (
        db.query(AiMemoryEntry.id)
        .filter(AiMemoryEntry.scope_identity == identity, AiMemoryEntry.status == "aktiv")
        .count()
    )
    return Importziel(
        identity=identity, server_id=sid, team_id=tid, grenze=grenze,
        frei=None if grenze is None else max(0, grenze - int(bestand)),
    )


def aehnlichkeit_je_text(
    db: Session, rows: list[AiMemoryEntry], texte: list[str]
) -> list[float | None]:
    """Je Text die höchste Bedeutungsähnlichkeit zu einer der Zeilen.

    Die Umkehrung von `aehnlichkeit_zu_texten`, für den Import: dort wird
    nicht gefragt, welcher Bestand zu einem Gespräch passt, sondern ob ein
    vorgeschlagener Satz schon dasteht. Alle Sätze in einem Aufruf des
    Modells — bei einem Ersatzanbieter für die Einbettung wäre einer je Satz
    ein HTTP-Aufruf je Satz. ``None`` heißt: kein Vergleich möglich.
    """
    if not rows or not texte:
        return [None] * len(texte)
    kodierung = ai_embedding_service.encode(texte, db=db)
    if kodierung is None or len(kodierung.vektoren) != len(texte):
        return [None] * len(texte)
    gespeichert = [_stored_vector(row, kodierung.modell) for row in rows]
    vorhanden = [vektor for vektor in gespeichert if vektor is not None]
    if not vorhanden:
        return [None] * len(texte)
    try:
        import numpy as np

        matrix = np.asarray(vorhanden, dtype="float32")
        fragen = np.asarray(kodierung.vektoren, dtype="float32")
        return [float(wert) for wert in (fragen @ matrix.T).max(axis=1).tolist()]
    except Exception as exc:  # noqa: BLE001 - ohne Vergleich bleibt der Satz in der Vorschau
        logger.warning("Aehnlichkeit der Importsaetze fehlgeschlagen error=%s", type(exc).__name__)
        return [None] * len(texte)


def _darf_aendern(db: Session, user: User, row: AiMemoryEntry) -> bool:
    """Darf dieser Benutzer diese eine Erinnerung aendern, vergessen oder loeschen?

    Dieselben Rechte wie beim Schreiben in ihren Bereich (`_assert_may_write`),
    nur fuer eine Zeile, die schon da ist.
    """
    if row.scope == "panel":
        return permission_service.has_global_permission(db, user, "panel.settings.write")
    if row.scope == "team":
        from services import team_service

        return row.team_id is not None and team_service.can_manage_team_memory(
            db, user, row.team_id
        )
    if row.scope == "server_shared":
        # Ein eigener Zweig und nicht der `else` darunter. Dort steht
        # `row.owner_user_id == user.id`, und Anlagenwissen hat bewusst keinen
        # Besitzer: der Vergleich waere gegen NULL immer False, der Eintrag
        # damit fuer **niemanden** loeschbar — auch nicht fuer den Betreiber.
        #
        # Und auch die Begruendung des `else` traegt hier nicht: sie gilt dem
        # eigenen Eintrag, den man behalten koennen soll, wenn der Serverzugang
        # wegfaellt. Fremdes Wissen zu loeschen, das man nicht mehr sehen darf,
        # ist das Gegenteil davon.
        return row.server_id is not None and permission_service.has_server_permission(
            db=db, user=user, server_id=row.server_id, key="server.config.write"
        )
    # Der eigene Eintrag, ohne zusaetzliche Serverbedingung. Vorher verlangte
    # eine serverbezogene Notiz weiterhin `server.view` — wer den Zugriff auf
    # einen Server verlor, konnte seine eigene Notiz dazu nicht mehr
    # loeschen. Sie blieb in der Datenbank, zaehlte gegen sein Kontingent
    # und war fuer ihn unerreichbar. Was gelesen wird, entscheidet weiterhin
    # `_visible_scope_rows` mit `server.view`; das ist eine andere Frage.
    return row.owner_user_id == user.id


def _darf_lesen(db: Session, user: User, row: AiMemoryEntry) -> bool:
    """Darf dieser Benutzer diese eine Erinnerung sehen (etwa ihren Verlauf)?

    Dieselbe Antwort wie die Bereichsansichten: das Panelwissen sieht jeder,
    der das Gedaechtnis benutzen darf (die Route prueft das), Teamwissen jedes
    Mitglied, Anlagenwissen wer den Server sehen darf, alles Persoenliche nur
    sein Besitzer.
    """
    if row.scope == "panel":
        return True
    if row.scope == "team":
        from services import team_service

        return row.team_id is not None and team_service.membership(
            db, row.team_id, user.id
        ) is not None
    if row.scope == "server_shared":
        return row.server_id is not None and permission_service.has_server_permission(
            db=db, user=user, server_id=row.server_id, key="server.view"
        )
    return row.owner_user_id == user.id


def _zeile(db: Session, user: User, entry_id: str, *, aendern: bool) -> AiMemoryEntry:
    """Laedt eine Erinnerung, die dieser Benutzer sehen bzw. aendern darf.

    Gibt es sie nicht oder darf er nicht, heisst die Antwort in beiden Faellen
    404: ob es eine fremde Erinnerung mit dieser Kennung gibt, geht ihn nichts
    an.
    """
    try:
        canonical = str(UUID(entry_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=404, detail="Memory-Eintrag nicht gefunden") from exc
    if aendern:
        # Gesperrt und frisch gelesen. Ohne Sperre lasen zwei gleichzeitige
        # Änderungen dieselbe `fassung`, bestanden beide die Prüfung, und die
        # zweite schrieb über die erste hinweg — deren Text stand danach weder
        # in der Erinnerung noch in ihren Fassungen. `populate_existing`, weil
        # `db.get` eine Zeile aus der Sitzung zurückgäbe, ohne die Datenbank
        # zu fragen.
        row = (
            db.query(AiMemoryEntry)
            .filter(AiMemoryEntry.id == canonical)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
    else:
        row = db.get(AiMemoryEntry, canonical)
    erlaubt = row is not None and (
        _darf_aendern(db, user, row) if aendern else _darf_lesen(db, user, row)
    )
    if not erlaubt:
        raise HTTPException(status_code=404, detail="Memory-Eintrag nicht gefunden")
    return row


def _protokolldetails(row: AiMemoryEntry, **weitere) -> dict:
    return {
        "scope": row.scope,
        **({"server_id": row.server_id} if row.server_id else {}),
        **({"team_id": row.team_id} if row.team_id else {}),
        **weitere,
    }


def delete_entry(db: Session, user: User, entry_id: str) -> None:
    """Loescht eine Erinnerung endgueltig, mit allen frueheren Fassungen.

    Das ist der Weg des Menschen in der Oberflaeche und deshalb sofort. Was die
    KI auf Wunsch vergisst, geht dagegen ueber `erinnerung_vergessen` und
    laesst sich 30 Tage zurueckholen.
    """
    row = _zeile(db, user, entry_id, aendern=True)
    audit_service.record_privileged_action(
        db, user_id=user.id, action="ai.memory.deleted", target_type="ai_memory",
        target_id=row.id,
        details=_protokolldetails(row),
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
            **({"team_id": normalized_team_id} if normalized_team_id else {}),
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


def _wortquelle(row: AiMemoryEntry, text: str = "") -> str:
    """Wogegen der Wortabgleich eine Zeile prueft: Name, Titel und Text.

    Vor dem Entschluesseln (`_vorauswahl`) gibt es nur Name und Titel; beide
    laedt `_schluessel_laden` in einem Aufruf.
    """
    return " ".join(teil for teil in (row.key, row.titel, text) if teil)


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
    return _bewertung(row, _wortquelle(row, value), query_tokens, now, similarity)


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
    db: Session,
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
    # Die Wortueberlappung unten liest den Namen jeder Zeile: alle in einem
    # Sidecar-Aufruf statt einem je Zeile.
    _schluessel_laden(rows)
    query_tokens = _tokens(query)
    scores = _similarities(db, query, rows)
    ranked = sorted(
        zip(rows, scores),
        key=lambda paar: _bewertung(
            paar[0], _wortquelle(paar[0]), query_tokens, now, paar[1], GEWICHTE_VORAUSWAHL
        ),
        reverse=True,
    )
    return [row for row, _score in ranked[:limit]], True


#: Länge der Nonce vor dem verschlüsselten Vektor — die Größe, für die AES-GCM
#: ausgelegt ist.
_VEKTOR_NONCE = 12


@lru_cache(maxsize=1)
def _vektorschluessel() -> AESGCM:
    """Der Schlüssel, unter dem die Vektorspalte liegt.

    Abgeleitet aus dem Panel-Secret und **nicht** über den DIS-Sidecar geholt.
    Das ist der einzige Grund, warum diese Verschlüsselung überhaupt tragbar
    ist: der Sidecar kostet je Zeile einen HTTP-Roundtrip, und die Rangfolge
    liest bis zu 5.000 Vektoren je Anfrage. Im Prozess gemessen sind es für
    dieselben 5.000 Zeilen 10 bis 17 ms.

    Was das schützt und was nicht, steht am Modellfeld (`models/ai_memory.py`):
    gegen bloßen Datenbankzugriff ja, gegen jemanden mit der Panel-Umgebung
    nein. Der Wert daneben bleibt stärker geschützt, weil sein Schlüssel im
    Sidecar liegt und nicht hier.

    Einmal abgeleitet und gehalten: `settings.secret_key` ändert sich innerhalb
    eines Prozesses nicht, und die Ableitung je Zeile kostete bei 5.000 Zeilen
    mehr als das Entschlüsseln selbst.
    """
    return AESGCM(
        hashlib.sha256(f"msm:ai:memory:vektor:{settings.secret_key}".encode()).digest()
    )


def _vektor_verschluesseln(roh: bytes) -> bytes:
    """Packt die float32-Bytes in die Form, in der sie in der Spalte stehen."""
    nonce = os.urandom(_VEKTOR_NONCE)
    return nonce + _vektorschluessel().encrypt(nonce, roh, None)


def _vektor_entschluesseln(gespeichert: bytes | None) -> bytes | None:
    """Öffnet die Vektorspalte, oder ``None``, wenn das nicht geht.

    ``None`` heißt hier "nicht unter diesem Schlüssel verpackt" und nicht
    "kaputt": eine Bestandszeile mit rohen Zahlen fällt genauso hierher wie
    eine, die unter einem gewechselten Panel-Secret entstanden ist. Was daraus
    folgt, entscheidet `_stored_vector` — nicht diese Funktion.
    """
    if not gespeichert or len(gespeichert) <= _VEKTOR_NONCE:
        return None
    try:
        return _vektorschluessel().decrypt(
            gespeichert[:_VEKTOR_NONCE], gespeichert[_VEKTOR_NONCE:], None
        )
    except InvalidTag:
        return None


def _liegt_im_klartext(row: AiMemoryEntry) -> bool:
    """Trägt diese Zeile ihren Vektor noch unverschlüsselt?

    Eine Form aus dem Bestand: die Bytespalte vor dem 23.08.2026, dann genau
    `EMBEDDING_BYTES` lang, ohne Nonce und ohne Siegel, und damit sauber von
    einer verpackten zu unterscheiden. (Die alte Textspalte `embedding_json`
    ist am 26.09.2026 weggefallen; Migration `20260926_08` hat ihren Rest in
    genau diese Form umgepackt.)

    Sie wird weiter **gelesen**. Sie einfach für ungültig zu erklären wäre
    der bequemere Weg gewesen und hätte das Gedächtnis schlechter gemacht, als
    es war: in einem Bereich mit tausenden Einträgen kommen je Anfrage nur
    `MAX_CONTEXT_ROWS` Zeilen bis zum Nachziehen, und was die Vorauswahl ohne
    Bedeutungsanteil nie nach vorn bringt, käme dort nie an — die Zeile bliebe
    dauerhaft ohne Vektor. Stattdessen zählt sie hier als **offen**: gelesen wie
    bisher, aber beim nächsten Abruf in den Kontext neu geschrieben, und dabei
    verpackt.
    """
    return (
        row.embedding_bytes is not None
        and len(row.embedding_bytes) == EMBEDDING_BYTES
    )


def _stored_vector(row: AiMemoryEntry, modell: str) -> Sequence[float] | None:
    """Liest den gespeicherten Vektor, wenn er aus dem Modell ``modell`` stammt.

    ``modell`` ist die Kennung der Frage, gegen die verglichen wird
    (`ai_embedding_service.Kodierung`), und keine Konstante: `encode` rechnet
    lokal oder im Google-Rückfall, und ein Vektor aus dem jeweils anderen Raum
    hat dieselbe Länge, aber keine vergleichbare Bedeutung.

    Geschrieben wird seit dem 19.08.2026 als float32-Bytes, seit dem
    23.08.2026 unter AES-GCM. Das Lesen kostet 4 ms bei 5.000 Einträgen, plus
    10 bis 17 ms fürs Entschlüsseln.

    Lässt sich die Bytespalte nicht öffnen, wird sie **roh** gelesen: dann ist
    es eine Bestandszeile mit unverpackten Zahlen, und `bytes_zu_vektor` erkennt
    an der Länge, ob sie das wirklich ist. Warum sie weiter gelesen wird und
    nicht einfach als fehlend gilt, steht an `_liegt_im_klartext`.
    """
    if row.embedding_model != modell:
        return None
    geoeffnet = _vektor_entschluesseln(row.embedding_bytes)
    vektor = ai_embedding_service.bytes_zu_vektor(
        row.embedding_bytes if geoeffnet is None else geoeffnet
    )
    return vektor


def _vektor_setzen(
    row: AiMemoryEntry, vektor: Sequence[float] | None, modell: str | None
) -> None:
    """Schreibt den Vektor einer Zeile, verpackt, oder verwirft ihn."""
    row.embedding_bytes = (
        None
        if vektor is None
        else _vektor_verschluesseln(ai_embedding_service.vektor_zu_bytes(vektor))
    )
    row.embedding_model = None if vektor is None else modell


def _embedding_source(key: str, value: str) -> str:
    """Der Text, aus dem der Vektor entsteht.

    Schluessel und Wert zusammen: der Schluessel traegt oft das Stichwort
    ("zeitzone"), der Wert den Inhalt. Punkte und Unterstriche werden zu
    Leerzeichen, damit `backup.zeitpunkt` als zwei Woerter gelesen wird.
    """
    readable_key = key.replace(".", " ").replace("_", " ").replace("-", " ")
    return f"{readable_key}: {value}"


def _einbettungstext(row: AiMemoryEntry, text: str) -> str:
    """Der Text, aus dem der Vektor einer Zeile entsteht.

    Titel und Text, wo es einen Titel gibt; der alte Name und der Wert bei
    Altbestand; sonst der Text allein. Der Satz traegt seit Gedaechtnis v2
    die Bedeutung selbst, Titel und Name sind nur Stichworte davor.
    """
    if row.titel:
        return f"{row.titel}: {text}"
    if row.key:
        return _embedding_source(row.key, text)
    return text


def refresh_embedding(db: Session, row: AiMemoryEntry, value: str) -> None:
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
    kodierung = ai_embedding_service.encode([_einbettungstext(row, value)], db=db)
    if kodierung is None or not kodierung.vektoren:
        _vektor_setzen(row, None, None)
        return
    _vektor_setzen(row, kodierung.vektoren[0], kodierung.modell)


def _vektoren_nachziehen(db: Session, decoded: list[tuple[AiMemoryEntry, str]]) -> None:
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
    Ein Aufruf für alle offenen Zeilen.

    Bewusst nur an dieser einen Stelle: `list_entries`, `scope_entries` und
    `personal_entries` sind Verwaltungsansichten, dort gehört kein Rechenschritt
    hin — auch nicht, seit die beiden letzten seitenweise laden und damit eine
    überschaubare Menge vor sich haben. Fehlt das Modell weiterhin, passiert
    schlicht nichts.

    **Eine Zeile mit unverpacktem Vektor gilt ebenfalls als offen**, auch wenn
    sie sich noch einwandfrei lesen lässt (`_liegt_im_klartext`). Seit dem
    23.08.2026 gehört der Vektor verschlüsselt in die Spalte, und sie nur zu
    lesen hieße, dass eine Bestandszeile ihre Klartextfassung behält, bis sie
    jemand von Hand anfasst. Neu gerechnet trägt sie dieselben Zahlen —
    `_vektor_setzen` räumt beide alten Formen dabei ab.
    """
    modell = ai_embedding_service.aktives_modell(db=db)
    if modell is None:
        return
    offen = [
        (row, value)
        for row, value in decoded
        if _stored_vector(row, modell) is None or _liegt_im_klartext(row)
    ]
    if not offen:
        return
    kodierung = ai_embedding_service.encode(
        [_einbettungstext(row, value) for row, value in offen], db=db
    )
    # Die Längenprüfung ist keine Formsache: käme weniger zurück als
    # hineingegeben, schriebe das `zip` den Vektor der einen Zeile an die
    # andere — eine falsche Bedeutung unter dem richtigen Schlüssel.
    if kodierung is None or len(kodierung.vektoren) != len(offen):
        return
    for (row, _value), vektor in zip(offen, kodierung.vektoren):
        _vektor_setzen(row, vektor, kodierung.modell)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _kontextordnung(row: AiMemoryEntry) -> tuple:
    """Die feste Reihenfolge der Zeilen im Block: Bereich, Name, Entstehen."""
    return (row.scope, row.key, _utc(row.created_at), row.id)


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
    # Was vergessen ist, gilt nicht mehr: es kommt in keinen Kontext und in
    # keine Suche, auch wenn es sich noch zurueckholen laesst.
    rows = db.query(AiMemoryEntry).filter(
        or_(*conditions), AiMemoryEntry.status == "aktiv"
    ).all()
    # Der Name ist verschluesselt; sortiert wird nach dem Klartext, alle Namen
    # und Titel in einem Sidecar-Aufruf. Zeilen ohne Namen ordnen sich nach
    # ihrem Entstehen: dieselbe Reihenfolge bei jedem Abruf.
    _schluessel_laden(rows)
    rows.sort(key=_kontextordnung)

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

    Auch ein Sidecar, der gar nicht antwortet, faellt hierunter: der Benutzer
    bekommt einen Assistenten ohne Gedaechtnis statt gar keinen. Sichtbar
    bleibt es ueber das Protokoll.
    """
    return _oeffnen(rows, still=True)


def _entschluesseln_lesbare(rows: list[AiMemoryEntry]) -> list[tuple[AiMemoryEntry, str]]:
    """Wie `_entschluesseln`, aber für die Verwaltungsansicht.

    Der Unterschied zum Helfer darüber ist **genau ein Fall**, und er ist
    Absicht:

    - Eine einzelne Zeile lässt sich nicht mehr öffnen (verdrehte AAD,
      gewechselter Schlüssel). Sie fällt in beiden Helfern still heraus, die
      übrigen bleiben sichtbar. Vorher nahm ein einziger solcher Eintrag die
      ganze Seite mit: der Router übersetzt ihn zu 503, und der Benutzer sah
      unter Profil > Memory dauerhaft "Memory ist nicht verfügbar" — auch für
      die vierzig intakten Einträge daneben, während der Chat sie unauffällig
      weiterbenutzte.
    - Der Sidecar antwortet gar nicht. Das läuft hier bewusst **weiter** bis
      zum Router und wird dort zu einem ehrlichen 503. Finge man es hier ab,
      zeigte die Verwaltungsansicht eine leere Liste: das Gedächtnis wäre
      angeblich leer, während jedes Schreiben scheitert.

    Der Chatweg (`_entschluesseln`) wählt im zweiten Fall andersherum, und aus
    demselben Grund: dort ist ein Assistent ohne Gedächtnis besser als gar
    keiner.
    """
    return _oeffnen(rows, still=False)


def _oeffnen(
    rows: list[AiMemoryEntry], *, still: bool
) -> list[tuple[AiMemoryEntry, str]]:
    """Der gemeinsame Rumpf der beiden Helfer darueber — alle Zeilen in einem Zug.

    Text, Titel und (bei Altbestand) Name aller Zeilen gehen zusammen durch
    `/decrypt-many`; vorher kostete jede Zeile einen eigenen `/decrypt`, acht
    zugleich in Threads (Zahlen an `_STAPEL_WERTE`). Was sich nicht oeffnen
    laesst, kommt als ``None`` zurueck: eine Zeile ohne lesbaren Text faellt
    heraus, ein unlesbarer Titel wird zu keinem Titel, ein unlesbarer Name zu
    ``""``.

    ``still`` entscheidet nur ueber den toten Sidecar: im Chat (``True``)
    heisst er "ohne Gedaechtnis", in der Verwaltung (``False``) fliegt der
    Fehler bis zum Router.
    """
    if not rows:
        return []
    paare: list[tuple[str, str]] = []
    ziele: list[tuple[AiMemoryEntry, str]] = []
    for row in rows:
        paare.append((row.value_encrypted, _aad(row)))
        ziele.append((row, "text"))
        if "_titel_klartext" not in row.__dict__ and DisClient.ist_verschluesselt(
            row.titel_encrypted or ""
        ):
            paare.append((row.titel_encrypted, row.titel_aad()))
            ziele.append((row, "titel"))
        if "_key_klartext" not in row.__dict__ and DisClient.ist_verschluesselt(
            row.key_encrypted or ""
        ):
            paare.append((row.key_encrypted, row.key_aad()))
            ziele.append((row, "name"))
    try:
        klartexte = _sammeln(paare)
    except DisSidecarError as exc:
        if not still:
            raise
        logger.warning(
            "Gedächtnis nicht lesbar, der Block bleibt leer: %s", type(exc).__name__
        )
        return []
    texte: dict[int, str | None] = {}
    for (row, feld), klar in zip(ziele, klartexte):
        if feld == "text":
            texte[id(row)] = klar
        elif feld == "titel":
            row._titel_klartext = klar
        else:
            row._key_klartext = klar if klar is not None else ""
    entschluesselt: list[tuple[AiMemoryEntry, str]] = []
    for row in rows:
        wert = texte.get(id(row))
        if wert is None:
            logger.warning(
                "Gedächtniseintrag %s (%s) nicht lesbar, wird übersprungen",
                row.id, row.scope,
            )
            continue
        entschluesselt.append((row, wert))
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
        # das ist kein Versehen. Dort liest ein Mensch mit, und dem sagt nur
        # der Name etwas. Hier geht es ums **Unterscheiden**: die Zeile soll sagen, dass zwei
        # widersprechende Antworten aus zwei verschiedenen Teams stammen, und
        # dafuer reicht eine beliebige stabile Marke.
        #
        # Der Name kostete hier, was er dort nicht kostet. `_memory_line` hat
        # keine Sitzung und ist eine reine Formatierung; ihn zu holen hiesse,
        # die Signatur und beide Kontextaufbauten zu aendern und im
        # schlechtesten Fall je Zeile ein `db.get(Team, …)` zu bezahlen — bei
        # jeder Nachricht neu, waehrend die Suche nur auf Nachfrage
        # laeuft. Dazu geht jede Zeile gegen dieselben 6.000 Zeichen; ein Name
        # je Zeile verdraengt Eintraege, statt welche zu erklaeren.
        scope = f"team:{row.team_id}"
        # **Die Herkunft zählt hier wie beim Anlagenwissen darüber**, und zwar
        # aus derselben Lage: eine Teamzeile schreibt die KI in der Sitzung
        # *eines* Mitglieds, gelesen wird sie von allen anderen. Für die heißt
        # "gemerkt" sonst "die KI hat es sich im Gespräch mit mir notiert" — sie
        # waren aber nicht dabei, und die Zeile kann aus einem fremden Lauf
        # stammen, der eine Logzeile gelesen hat. Bestätigt hat sie dort
        # niemand.
        #
        # "eingetragen" trifft zu, weil eine Teamzeile mit `origin='user'` nur
        # über die Oberfläche entsteht (`routers/ai_memory.py`); der KI-Weg
        # schreibt ausnahmslos mit `origin='ai'`. Verboten wird ihr damit
        # nichts — sie darf weiter ins Teamwissen schreiben, der nächste Lauf
        # sieht nur, wie fest der Boden ist.
        origin = "eingetragen" if row.origin == "user" else "unbestätigt"
    else:
        scope = row.scope
    # **Verblasst statt weg.** Liegt die Abrufstaerke unter der Schwelle, geht
    # nur der Anfang mit — der Schluessel bleibt immer vollstaendig, damit das
    # Modell weiss, *dass* es die Notiz gibt, und mit `search_memory`
    # nachfassen kann. Genau der Weg, den ein Mensch nimmt, wenn ihm etwas
    # "auf der Zunge liegt".
    #
    # Seit Gedaechtnis v2 steht vorn kein Name mehr, sondern gleich der Satz:
    # er ist ohne Gespraech verstaendlich, ein Name davor waere nur ein zweites
    # Stichwort, das Platz kostet. Altbestand behaelt seinen Namen, bis die
    # Pflege ihn umschreibt.
    vorn = f"{row.key}: " if row.key else ""
    if staerke is not None and staerke < VERBLASSEN_AB and len(flattened) > VERBLASST_ZEICHEN:
        flattened = flattened[:VERBLASST_ZEICHEN].rstrip() + " …"
        return f"[{scope}/{origin}/blass] {vorn}{flattened}"
    return f"[{scope}/{origin}] {vorn}{flattened}"


def _similarities(
    db: Session, query: str, rows: list[AiMemoryEntry]
) -> list[float | None]:
    """Bedeutungsaehnlichkeit der Eintraege zur Frage, oder lauter ``None``.

    ``None`` steht fuer "kein Vergleich moeglich" und nicht fuer "unaehnlich":
    ohne Modell, ohne Frage oder ohne gespeicherten Vektor soll ein Eintrag
    nach den uebrigen Kriterien bewertet werden, statt hinten anzustehen.
    """
    if not query.strip():
        return [None] * len(rows)
    kodierung = ai_embedding_service.encode([query], db=db)
    if kodierung is None or not kodierung.vektoren:
        return [None] * len(rows)

    # Nur Vektoren aus demselben Modell wie die Frage. Ein Eintrag aus dem
    # anderen gilt als vektorlos — `None`, nicht unähnlich — bis
    # `_vektoren_nachziehen` ihn neu gerechnet hat.
    stored = [_stored_vector(row, kodierung.modell) for row in rows]
    known = [vector for vector in stored if vector is not None]
    if not known:
        return [None] * len(rows)

    scores = ai_embedding_service.similarity(kodierung.vektoren[0], known)
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
    aehnlichkeiten = _similarities(db, query, [row for row, _ in decoded])
    for (row, wert), aehnlichkeit in zip(decoded, aehnlichkeiten):
        treffer = _reiz(aehnlichkeit, len(query_tokens & _tokens(_wortquelle(row, wert))))
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
    # Was die Vorauswahl wegwirft, weiß danach niemand mehr: `rows` wird
    # überschrieben, und `decoded` kennt nur, was ihm gegeben wurde. Die Zahl
    # muss deshalb hier festgehalten werden — der Hinweis unten nennt sie.
    vor_der_vorauswahl = len(rows)
    rows, vorgekuerzt = _vorauswahl(db, rows, query, now, zeilen)
    vorab_verworfen = vor_der_vorauswahl - len(rows)
    decoded = _entschluesseln(rows)
    # Zeilen aus einer Ausfallphase des Modells tragen keinen Vektor. Hier
    # liegt ihr Klartext ohnehin offen, also ist hier die Stelle, an der es
    # nichts extra kostet, ihn nachzurechnen — sonst blieben sie für immer
    # blind für Bedeutungsrang und Reiz.
    _vektoren_nachziehen(db, decoded)
    # **Die Abrufstaerke je Zeile**, einmal berechnet und danach zweimal
    # gebraucht: fuer die Darstellung (blass oder voll) und, falls das Budget
    # nicht reicht, als Teil der Auswahl. Die Vektoren liegen ohnehin schon an
    # den Zeilen; teuer ist hier nichts.
    query_tokens = _tokens(query)
    aehnlichkeiten = _similarities(db, query, [row for row, _ in decoded])
    ueberlappungen = [
        len(query_tokens & _tokens(_wortquelle(row, value))) for row, value in decoded
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
        selected.sort(key=lambda item: _kontextordnung(item[0]))
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
        #
        # Gezählt werden **beide** Engstellen. Hier stand allein
        # `len(decoded) - len(selected)`, und im Zweig darüber ist das
        # ausnahmslos 0: passte nach der Vorauswahl alles ins Budget, meldete
        # der Block "0 weitere Eintraege wurden ausgelassen", nachdem die
        # Vorauswahl bei 5.000 Einträgen 4.700 Zeilen weggeworfen hatte. Eine 0
        # behauptet Vollständigkeit — das ist das Gegenteil dessen, wofür es
        # diesen Hinweis gibt.
        #
        # Nicht mitgezählt wird, was `_entschluesseln` übersprungen hat: eine
        # unlesbare Zeile fehlt nicht "aus Platzgruenden".
        block += (
            f"\n[Hinweis] {vorab_verworfen + len(decoded) - len(selected)} weitere "
            "Eintraege wurden aus Platzgruenden ausgelassen."
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
    rows, _vorgekuerzt = _vorauswahl(db, rows, query, now, MAX_CONTEXT_ROWS)
    decoded = _entschluesseln(rows)
    query_tokens = _tokens(query)
    scores = _similarities(db, query, [row for row, _ in decoded])
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
