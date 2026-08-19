"""Ownership, DIS-Schutz, Secret-Abweisung und Abruf fuer AI-Memory."""

from datetime import datetime, timezone
import json
import logging
import re
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import AiMemoryEntry, AiMemoryPreference, Server, Team, User
from services import (
    ai_embedding_service,
    ai_limit_service,
    audit_service,
    permission_service,
)
from services.ai_redaction import redact_sensitive_text
from services.ai_embedding_service import EMBEDDING_DIMENSIONS
from services.ai_embedding_service import MODEL_TAG as _EMBEDDING_MODEL_TAG
from services.dis_client import DisClient, DisDecryptionError, DisSidecarError


logger = logging.getLogger(__name__)

#: Was diesem Benutzer gehoert und deshalb an seiner Einwilligung haengt.
#: `team` und `panel` gehoeren dem Team bzw. dem Betreiber.
PERSOENLICHE_SCOPES = ("user", "server")
MAX_CONTEXT_CHARS = 6_000
# Wieviele Eintraege eine *einzelne Anfrage* hoechstens entschluesselt.
#
# Das ist eine andere Zusage als `max_memory_entries`, und die eine ersetzt die
# andere nicht: jenes Rollenlimit deckelt einen **Bereich**, dieser Wert deckelt
# eine **Anfrage**. Der Unterschied ist der Multiplikator dazwischen, und der
# gehoert nicht dem Betreiber, sondern dem Benutzer: mit jedem Server, den er
# sehen darf, und jedem Team, das er gruendet, kommt ein weiterer Bereich hinzu.
# `provider_memory_context` filtert nur `server_shared` auf den einen aktuellen
# Server — die persoenlichen Servernotizen kommen fuer *alle* sichtbaren Server
# mit. Bei einem Rollenlimit von 1.000 und zwanzig Anlagen waren das ueber
# 21.000 Zeilen, und jede kostet in `_entschluesseln` einen synchronen
# HTTP-Roundtrip zum DIS-Sidecar — vor dem Schnitt auf `MAX_CONTEXT_CHARS`,
# weil sich erst am Klartext messen laesst, was ins Budget passt.
#
# 300 ist gegen genau dieses Budget gewaehlt: bei kurzen Eintraegen passen
# hoechstens rund 150 Zeilen in 6.000 Zeichen, der Deckel liegt also beim
# Doppelten dessen, was ueberhaupt je gezeigt werden koennte. Unterhalb davon
# aendert sich **nichts** — `_vorauswahl` reicht die Zeilen dann unveraendert
# durch. Er greift nur dort, wo das Budget ohnehin das meiste weggeworfen haette.
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
    if redact_sensitive_text(normalized) != normalized:
        raise HTTPException(status_code=422, detail="Memory darf keine Zugangsdaten enthalten")
    return normalized


def list_entries(
    db: Session, user: User, scope: str, server_id: int | None,
    team_id: int | None = None,
) -> list[tuple[AiMemoryEntry, str]]:
    identity, _, _, _ = scope_identity(db, user, scope, server_id, team_id)
    rows = db.query(AiMemoryEntry).filter(AiMemoryEntry.scope_identity == identity).order_by(AiMemoryEntry.key).all()
    return _entschluesseln_lesbare(rows)


def personal_entries(db: Session, user: User) -> list[tuple[AiMemoryEntry, str]]:
    """Alles, was diesem Benutzer selbst gehoert — persoenlich und serverbezogen.

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

    Bewusst **ohne** `MAX_CONTEXT_ROWS`: dieser Weg traegt denselben
    Multiplikator wie der Kontextaufbau (ein Bereich je sichtbarem Server), aber
    nicht dieselbe Frage. Hier hat der Benutzer genau diese Liste angefordert,
    einmal, und will sie aufraeumen — eine Ansicht, die stillschweigend 300 von
    400 Eintraegen zeigt, waere schlimmer als eine langsame: sie verstecke
    genau die Zeilen, die zu loeschen er gekommen ist. Der Deckel dort schuetzt
    *jede* Chatnachricht, dieser Aufruf geschieht auf Klick.
    """
    rows = (
        db.query(AiMemoryEntry)
        .filter(
            AiMemoryEntry.owner_user_id == user.id,
            AiMemoryEntry.scope.in_(PERSOENLICHE_SCOPES),
        )
        .order_by(AiMemoryEntry.scope, AiMemoryEntry.server_id, AiMemoryEntry.key)
        .all()
    )
    return _entschluesseln_lesbare(rows)


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
        bestand = db.query(AiMemoryEntry).filter(
            AiMemoryEntry.scope_identity == identity
        ).count()
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
    # `_stored_vector` je Zeile waere ein zweites JSON-Parsen.
    paare: list[tuple[AiMemoryEntry, list[float]]] = []
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
    # Der Reiz. `similarity` liegt in [-1, 1]; negativ heisst "hat nichts
    # miteinander zu tun" und darf nicht als Beitrag zaehlen.
    reiz = max(0.0, similarity) if similarity is not None else 0.0
    if overlap:
        # Wortueberlappung ist ein groberes, aber sehr sicheres Signal — im
        # Gameserver-Umfeld stehen Lehnwoerter (Backup, RAM, Ports) woertlich
        # in deutschen Eintraegen. Ein Treffer hebt auf mindestens die Haelfte,
        # zwei auf volle Praesenz.
        reiz = max(reiz, min(1.0, 0.5 + 0.25 * overlap))

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
    - **Nutzung.** Was oft abgerufen wurde, ist erfahrungsgemaess wichtig.
    - **Aktualitaet.** Frisch Gemerktes gewinnt gegen Altes, das nie gebraucht
      wurde — sonst kaeme ein neuer Eintrag nie zum Zug, weil ihm die
      Nutzungshistorie fehlt.

    ``similarity`` ist ``None``, wenn kein Modell geladen ist oder der Eintrag
    noch keinen Vektor hat. Dann entscheiden die drei uebrigen Kriterien; der
    Eintrag faellt nicht heraus.
    """
    return _bewertung(row, f"{row.key} {value}", query_tokens, now, similarity)


def _bewertung(
    row: AiMemoryEntry,
    text: str,
    query_tokens: set[str],
    now: datetime,
    similarity: float | None = None,
) -> float:
    """Die Formel hinter `_relevance` — mit dem Vergleichstext als Parameter.

    Sie steht getrennt, weil es zwei Zeitpunkte gibt, an denen bewertet wird,
    und nur einer davon den Klartext hat: `_vorauswahl` laeuft **vor** der
    Entschluesselung und kann nur `row.key` beisteuern, `_relevance` laeuft
    danach und hat Schluessel *und* Wert. Zwei getrennt gepflegte Formeln waeren
    hier besonders tueckisch — die Vorauswahl entschiede dann nach anderen
    Massstaeben, als die Auswahl unmittelbar danach anlegt, und die Zeile fiele
    in der ersten Runde heraus, die in der zweiten gewonnen haette.
    """
    overlap = len(query_tokens & _tokens(text))
    reference = row.last_used_at or row.updated_at or row.created_at
    age_days = max(0.0, (now - _utc(reference)).total_seconds() / 86_400)
    recency = 1.0 / (1.0 + age_days / RECENCY_HALFLIFE_DAYS)
    # Negative Aehnlichkeit heisst "hat nichts miteinander zu tun" und darf
    # einen Eintrag nicht unter einen ohne Vektor druecken.
    meaning = max(0.0, similarity) if similarity is not None else 0.0
    return meaning * 6.0 + overlap * 3.0 + min(row.use_count, 20) * 0.5 + recency * 2.0


def _vorauswahl(
    rows: list[AiMemoryEntry],
    query: str,
    now: datetime,
    limit: int,
) -> tuple[list[AiMemoryEntry], bool]:
    """Kuerzt die Zeilenmenge, **bevor** sie entschluesselt wird.

    Der Trick liegt darin, dass die Rangfolge den Klartext fast nicht braucht:
    `_similarities` liest den gespeicherten Vektor von der Zeile, Nutzung und
    Aktualitaet stehen als Spalten daneben, und `row.key` ist ohnehin Klartext —
    verschluesselt ist allein der Wert. Von den vier Kriterien in `_bewertung`
    sind hier also drei in voller Staerke da; nur die Wortueberlappung sieht den
    Schluessel statt Schluessel und Wert.

    Das ist der Preis, und er ist der richtige: die Alternative waere, alles zu
    entschluesseln, um es bewerten zu koennen — also genau der Aufwand, gegen
    den dieser Deckel steht. Und er faellt nur an, wo er kaum wiegt: unterhalb
    von ``limit`` gibt die Funktion die Liste **unveraendert** zurueck, nicht
    einmal neu sortiert.

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
        key=lambda paar: _bewertung(paar[0], paar[0].key, query_tokens, now, paar[1]),
        reverse=True,
    )
    return [row for row, _score in ranked[:limit]], True


def _stored_vector(row: AiMemoryEntry) -> list[float] | None:
    """Liest den gespeicherten Vektor, wenn er zum aktuellen Modell passt."""
    if not row.embedding_json or row.embedding_model != _EMBEDDING_MODEL_TAG:
        return None
    try:
        vector = json.loads(row.embedding_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(vector, list) or len(vector) != EMBEDDING_DIMENSIONS:
        return None
    return vector


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
    Eintrag würde bei knappem Platz weiterhin für Minecraft-Fragen hochgezogen —
    und nichts rechnet ihn je nach, denn `upsert_entry` ist der einzige Aufrufer
    und eine Nachberechnung gibt es für das Gedächtnis nicht. ``None`` heißt
    laut Modell "noch nicht berechnet"; `_stored_vector` kommt damit zurecht.
    """
    vectors = ai_embedding_service.encode([_embedding_source(row.key, value)])
    if not vectors:
        row.embedding_json = None
        row.embedding_model = None
        return
    row.embedding_json = json.dumps(vectors[0], separators=(",", ":"))
    row.embedding_model = _EMBEDDING_MODEL_TAG


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
    entschluesselt: list[tuple[AiMemoryEntry, str]] = []
    for row in rows:
        try:
            entschluesselt.append((row, DisClient.decrypt(row.value_encrypted, aad=_aad(row))))
        except DisSidecarError as exc:
            logger.warning(
                "Gedaechtniseintrag %s (%s) nicht lesbar, wird uebersprungen: %s",
                row.id, row.scope, type(exc).__name__,
            )
    return entschluesselt


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
    entschluesselt: list[tuple[AiMemoryEntry, str]] = []
    for row in rows:
        try:
            entschluesselt.append((row, DisClient.decrypt(row.value_encrypted, aad=_aad(row))))
        except DisDecryptionError as exc:
            logger.warning(
                "Gedächtniseintrag %s (%s) nicht lesbar, wird übersprungen: %s",
                row.id, row.scope, type(exc).__name__,
            )
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

    Der Nutzungszaehler laeuft mit. Diese Zeilen sind gelesen worden wie jede
    andere auch, und bei knappem Platz soll das mitentscheiden.
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
    for row, _wert in decoded:
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

    Ausgewaehlte Eintraege werden als benutzt vermerkt. Dieses Zaehlwerk ist
    das Gedaechtnis des Gedaechtnisses: es entscheidet beim naechsten Engpass
    mit, was bleibt — und es ist zugleich der Weg zurueck aus dem Verblassen.
    """
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
    # von 6.000 Zeichen von Betriebsanleitungen aufgebraucht, die mit der Frage
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
    rows, vorgekuerzt = _vorauswahl(rows, query, now, MAX_CONTEXT_ROWS)
    decoded = _entschluesseln(rows)
    # **Die Abrufstaerke je Zeile**, einmal berechnet und danach zweimal
    # gebraucht: fuer die Darstellung (blass oder voll) und, falls das Budget
    # nicht reicht, als Teil der Auswahl. Die Vektoren liegen ohnehin schon an
    # den Zeilen; teuer ist hier nichts.
    query_tokens = _tokens(query)
    aehnlichkeiten = _similarities(query, [row for row, _ in decoded])
    staerken = [
        abrufstaerke(
            row,
            now,
            aehnlichkeit,
            len(query_tokens & _tokens(f"{row.key} {value}")),
        )
        for (row, value), aehnlichkeit in zip(decoded, aehnlichkeiten)
    ]
    lines = [
        _memory_line(row, value, staerke)
        for (row, value), staerke in zip(decoded, staerken)
    ]
    total = sum(len(line) + 1 for line in lines)

    if total <= MAX_CONTEXT_CHARS:
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
            if used + len(line) + 1 > MAX_CONTEXT_CHARS:
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

    # **Die Nutzung wird vor dem Bauen vermerkt, aber nach dem Bewerten.**
    # Die Staerken oben stammen aus dem Zustand *vor* dieser Runde — sonst
    # haette sich jeder Eintrag durch das blosse Gezeigtwerden selbst
    # aufgefrischt, und "verblasst" gaebe es nie.
    staerke_final = {
        id(row): staerke for (row, _v), staerke in zip(decoded, staerken)
    }
    for row, _value in selected:
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
    rows, _vorgekuerzt = _vorauswahl(rows, query, now, MAX_CONTEXT_ROWS)
    decoded = _entschluesseln(rows)
    query_tokens = _tokens(query)
    scores = _similarities(query, [row for row, _ in decoded])
    ranked = sorted(
        zip(decoded, scores),
        key=lambda item: _relevance(item[0][0], item[0][1], query_tokens, now, item[1]),
        reverse=True,
    )
    return [
        (row, value, _relevance(row, value, query_tokens, now, score))
        for (row, value), score in ranked[:limit]
    ]


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
