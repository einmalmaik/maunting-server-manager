"""Ownership, DIS-Schutz, Secret-Abweisung und Abruf fuer AI-Memory."""

from datetime import datetime, timezone
import json
import re
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import AiMemoryEntry, AiMemoryPreference, User
from services import ai_embedding_service, audit_service, permission_service
from services.ai_redaction import redact_sensitive_text
from services.ai_embedding_service import EMBEDDING_DIMENSIONS
from services.dis_client import DisClient


MAX_ENTRIES_PER_SCOPE = 100
MAX_CONTEXT_CHARS = 6_000
# Kennung des Modells, mit dem ein gespeicherter Vektor entstanden ist. Wechselt
# der Betreiber das Modell, passen alte Vektoren nicht mehr — sie werden dann
# ignoriert statt falsche Aehnlichkeiten zu liefern.
_EMBEDDING_MODEL_TAG = "potion-multilingual-128M"
# Nach so vielen Tagen ohne Nutzung haelbiert sich der Aktualitaetsbonus. Grob
# an "eine Arbeitswoche" angelehnt; der Wert entscheidet nur bei Platzmangel.
RECENCY_HALFLIFE_DAYS = 7.0
_WORD_RE = re.compile(r"[\w]+", re.UNICODE)


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
    if scope == "server":
        if team_id is not None:
            raise HTTPException(status_code=422, detail="Server-Memory akzeptiert kein Team")
        if server_id is None or not permission_service.has_server_permission(
            db=db, user=user, server_id=server_id, key="server.view"
        ):
            raise HTTPException(status_code=404, detail="Server nicht gefunden")
        return f"server:{server_id}:user:{user.id}", user.id, server_id, None
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
    return [(row, DisClient.decrypt(row.value_encrypted, aad=_aad(row))) for row in rows]


def _assert_may_write(db: Session, user: User, scope: str, team_id: int | None) -> None:
    """Wer einen geteilten Bereich veraendern darf.

    Persoenliche Eintraege brauchen keine Pruefung — sie verlassen den Benutzer
    nie. Alles Geteilte verlangt ein Recht, und zwar *dasselbe*, das ein Mensch
    fuer denselben Schritt braeuchte. Genau darin liegt die Zusicherung, die
    ueber jedem KI-Schreibvorgang steht: **die KI kann nie mehr teilen, als der
    Benutzer selbst teilen duerfte.**
    """
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
    _assert_may_write(db, user, scope, normalized_team_id)
    safe_value = _safe_value(value)
    row = db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == identity, AiMemoryEntry.key == key
    ).first()
    action = "ai.memory.updated"
    if row is None:
        if db.query(AiMemoryEntry).filter(AiMemoryEntry.scope_identity == identity).count() >= MAX_ENTRIES_PER_SCOPE:
            raise HTTPException(status_code=409, detail="Memory-Scope ist voll")
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
        details={"scope": scope, "origin": origin},
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
    else:
        allowed = row.owner_user_id == user.id and (
            row.server_id is None or permission_service.has_server_permission(db, user, row.server_id, "server.view")
        )
    if not allowed:
        raise HTTPException(status_code=404, detail="Memory-Eintrag nicht gefunden")
    audit_service.record_privileged_action(
        db, user_id=user.id, action="ai.memory.deleted", target_type="ai_memory",
        target_id=row.id, details={"scope": row.scope}, origin="direct",
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
    identity, _, _, _ = scope_identity(db, user, scope, server_id, team_id)
    _assert_may_write(db, user, scope, team_id)
    rows = db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == identity
    ).all()
    if not rows:
        return 0
    for row in rows:
        db.delete(row)
    audit_service.record_privileged_action(
        db, user_id=user.id, action="ai.memory.cleared", target_type="ai_memory",
        target_id=None, details={"scope": scope, "count": len(rows)}, origin="direct",
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
    overlap = len(query_tokens & _tokens(f"{row.key} {value}"))
    reference = row.last_used_at or row.updated_at or row.created_at
    age_days = max(0.0, (now - _utc(reference)).total_seconds() / 86_400)
    recency = 1.0 / (1.0 + age_days / RECENCY_HALFLIFE_DAYS)
    # Negative Aehnlichkeit heisst "hat nichts miteinander zu tun" und darf
    # einen Eintrag nicht unter einen ohne Vektor druecken.
    meaning = max(0.0, similarity) if similarity is not None else 0.0
    return meaning * 6.0 + overlap * 3.0 + min(row.use_count, 20) * 0.5 + recency * 2.0


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

    Schlaegt es fehl, bleibt der alte Wert stehen und der Eintrag wird eben
    ohne Bedeutungsanteil bewertet. Ein Gedaechtniseintrag darf nicht daran
    scheitern, dass ein Modell fehlt.
    """
    vectors = ai_embedding_service.encode([_embedding_source(row.key, value)])
    if not vectors:
        return
    row.embedding_json = json.dumps(vectors[0], separators=(",", ":"))
    row.embedding_model = _EMBEDDING_MODEL_TAG


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


#: Was diesem Benutzer gehoert und deshalb an seiner Einwilligung haengt.
PERSOENLICHE_SCOPES = ("user", "server")


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
    """
    from services import team_service

    team_ids = team_service.user_team_ids(db, user)
    conditions = [AiMemoryEntry.scope_identity == "panel"]
    if persoenlich:
        conditions.append(AiMemoryEntry.scope_identity == f"user:{user.id}")
        conditions.append(
            and_(
                AiMemoryEntry.scope == "server",
                AiMemoryEntry.owner_user_id == user.id,
            )
        )
    if team_ids:
        conditions.append(
            and_(AiMemoryEntry.scope == "team", AiMemoryEntry.team_id.in_(team_ids))
        )
    rows = db.query(AiMemoryEntry).filter(
        or_(*conditions)
    ).order_by(AiMemoryEntry.scope, AiMemoryEntry.key).all()

    visible: list[AiMemoryEntry] = []
    for row in rows:
        if row.scope == "server":
            if row.server_id is None or not permission_service.has_server_permission(
                db=db, user=user, server_id=row.server_id, key="server.view"
            ):
                continue
        visible.append(row)
    return visible


def _memory_line(row: AiMemoryEntry, value: str) -> str:
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
    elif row.scope == "team":
        scope = f"team:{row.team_id}"
    else:
        scope = row.scope
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


def provider_memory_context(
    db: Session,
    user: User,
    query: str = "",
) -> str | None:
    """Baut den Memory-Block fuer eine konkrete Anfrage.

    Passt alles ins Budget, kommt alles mit — das ist der Normalfall bei
    hoechstens 100 Eintraegen je Scope und zugleich der sprachunabhaengigste:
    das Sprachmodell sieht jeden Eintrag und stellt den Bezug selbst her, egal
    in welcher Sprache er formuliert ist.

    Erst wenn es *nicht* passt, wird ausgewaehlt — nach Bedeutung, Bezug zur
    Frage, Nutzung und Aktualitaet. Vorher wurde an dieser Stelle alphabetisch
    nach Schluessel sortiert und bei 6.000 Zeichen abgeschnitten: ein Eintrag
    "zeitzone" fiel damit systematisch raus, "backup" blieb immer drin.

    Ausgewaehlte Eintraege werden als benutzt vermerkt. Dieses Zaehlwerk ist
    das Gedaechtnis des Gedaechtnisses: es entscheidet beim naechsten Engpass
    mit, was bleibt.
    """
    # Die Einwilligung gilt dem **eigenen** Gedaechtnis. Teamwissen gehoert dem
    # Team und panelweites dem Betreiber; wer diesen Schalter umlegt, trifft
    # eine Entscheidung ueber sich, nicht ueber seine Kollegen. Vorher endete
    # die Funktion hier komplett — ein Mitglied ohne Einwilligung arbeitete
    # unbemerkt ohne das Wissen seiner Teams.
    rows = _visible_scope_rows(db, user, persoenlich=preference(db, user.id))
    if not rows:
        return None

    decoded = [(row, DisClient.decrypt(row.value_encrypted, aad=_aad(row))) for row in rows]
    lines = [_memory_line(row, value) for row, value in decoded]
    total = sum(len(line) + 1 for line in lines)

    if total <= MAX_CONTEXT_CHARS:
        selected = decoded
        truncated = False
    else:
        now = datetime.now(timezone.utc)
        query_tokens = _tokens(query)
        scores = _similarities(query, [row for row, _ in decoded])
        ranked = sorted(
            zip(decoded, scores),
            key=lambda item: _relevance(
                item[0][0], item[0][1], query_tokens, now, item[1]
            ),
            reverse=True,
        )
        selected = []
        used = 0
        for (row, value), _score in ranked:
            line = _memory_line(row, value)
            if used + len(line) + 1 > MAX_CONTEXT_CHARS:
                continue
            selected.append((row, value))
            used += len(line) + 1
        # Die urspruengliche Reihenfolge lesbar halten, nicht die Rangfolge.
        selected.sort(key=lambda item: (item[0].scope, item[0].key))
        truncated = len(selected) < len(decoded)

    if not selected:
        return None

    now = datetime.now(timezone.utc)
    for row, _value in selected:
        row.use_count = int(row.use_count or 0) + 1
        row.last_used_at = now
    db.flush()

    block = "\n".join(_memory_line(row, value) for row, value in selected)
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

    decoded = [(row, DisClient.decrypt(row.value_encrypted, aad=_aad(row))) for row in rows]
    now = datetime.now(timezone.utc)
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
    `panel.settings.write`.
    """
    identity, _owner_id, _server_id, normalized_team_id = scope_identity(
        db, user, scope, server_id, team_id
    )
    _assert_may_write(db, user, scope, normalized_team_id)

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
            target_id=row.id, details={"scope": row.scope, "key": row.key},
            origin="ai",
        )
        removed.append(row.key)
        db.delete(row)
    db.commit()
    return sorted(removed)
