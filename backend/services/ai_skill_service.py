"""Skills als Prosa: mitgeliefert, vom Betreiber gepflegt oder selbst gelernt.

Ein Skill ist eine Textdatei mit zwei Pflichtangaben im Kopf (`name`,
`description`) und beliebigem Fliesstext darunter. Das Modell liest den Text
und entscheidet weiter selbst — es fuehrt ihn nicht aus.

**Stufenweises Laden.** Dauerhaft mit im Kontext stehen nur Name und
Beschreibung, rund hundert Tokens je Skill — als eigene, als Daten
gekennzeichnete Nachricht direkt hinter dem Systemprompt
(`ai_context_service._skill_index_message`). Der Text kommt erst, wenn das
Modell ihn mit `read_skill` anfordert. Fuenfzig Skills kosten damit nichts,
solange keiner passt.

**Drei Herkuenfte, ein Verzeichnis.**

- *mitgeliefert* — Dateien in `backend/ai_skills/`. Sie liegen bewusst nicht in
  der Datenbank: so verbessert ein MSM-Update die KI jeder Installation, ohne
  Migration. Nicht aenderbar, aber abschaltbar.
- *global* — vom Betreiber gepflegt oder von der KI gelernt. Eine globale
  Datenbankzeile mit demselben Schluessel **ersetzt** die mitgelieferte Datei;
  das ist der Weg, eine Vorgabe zu ueberschreiben, ohne sie zu verlieren.
- *Team* — gehoert genau einem Team und erreicht nur dessen Mitglieder.

Persoenliche Skills gibt es bewusst nicht: wer allein arbeitet, hat sein
Ein-Mann-Team, und damit gilt ueberall dieselbe Regel.

**Warum Prosa und kein Makro.** Der Vorgaenger war eine gespeicherte Folge von
Tool-Aufrufen. Prosa fuehrt nichts aus — ein selbst gelernter Skill kann damit
nichts, was das Modell nicht ohnehin duerfte, er aendert nur die
Herangehensweise. Genau deshalb ist Selbstlernen hier vertretbar, waehrend das
automatische Erzeugen ausfuehrbarer Schrittfolgen es nicht waere.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

try:
    import yaml
except ImportError:  # pragma: no cover - exercised on systems before deps install
    # Weich, weil dieses Modul im Startpfad liegt: main -> routers -> ai_skills
    # -> hier. Ein harter Import macht aus einem fehlenden Parser einen
    # Totalausfall des Panels, obwohl `_parse_shipped` unten ausdruecklich
    # zusichert, dass nicht einmal eine beschaedigte Skill-Datei den Start
    # verhindern darf. Ohne yaml faellt jede mitgelieferte Datei aus dem
    # Verzeichnis — Skills aus der Datenbank tragen eigene Spalten und laufen
    # weiter.
    yaml = None  # type: ignore[assignment]
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import AiSkill, User
from services import ai_embedding_service, audit_service, permission_service, team_service
from services.ai_embedding_service import EMBEDDING_DIMENSIONS
from services.ai_embedding_service import MODEL_TAG as _EMBEDDING_MODEL_TAG
from services.ai_redaction import enthaelt_zugangsdaten


logger = logging.getLogger(__name__)

MAX_BODY_CHARS = 12_000
MAX_NAME_CHARS = 100
MAX_DESCRIPTION_CHARS = 500
# So viele Skills stehen hoechstens gleichzeitig im Skill-Verzeichnis (die
# Datennachricht hinter dem Systemprompt). Darueber entscheidet die
# Bedeutungsaehnlichkeit zur Frage, welche mitkommen.
MAX_INDEXED_SKILLS = 25
MAX_SKILLS_PER_SCOPE = 200
# Wartende Zeilen bekommen ein eigenes, viel kleineres Kontingent. Sie
# entstehen ohne Entscheidung eines Menschen — unter der Lernpolitik `review`
# legt jedes Kundengespraech welche an — und duerfen deshalb nicht dasselbe
# Kontingent aufbrauchen wie die freigegebenen Skills des Betreibers.
MAX_PENDING_PER_SCOPE = 25
_SKILL_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", re.DOTALL)

_shipped_lock = threading.Lock()
_shipped_cache: dict[str, "ShippedSkill"] | None = None


@dataclass(frozen=True)
class ShippedSkill:
    """Ein mit MSM ausgelieferter Skill. Liegt als Datei vor, nie in der DB."""

    skill_key: str
    name: str
    description: str
    body: str


@dataclass(frozen=True)
class SkillView:
    """Ein Skill, wie ihn Modell und Oberflaeche sehen — Datei oder Datenbank."""

    skill_key: str
    name: str
    description: str
    scope: str            # "shipped" | "global" | "team"
    origin: str           # "shipped" | "operator" | "ai"
    team_id: int | None
    status: str           # "active" | "pending"
    enabled: bool
    editable: bool
    id: str | None = None
    #: Wann die Datenbankzeile zuletzt geschrieben wurde. ``None`` heißt
    #: mitgeliefert — eine Datei hat kein Änderungsdatum. Gebraucht wird das
    #: Feld allein für den Notschnitt in `_neueste_zuerst`; die Oberfläche
    #: zeigt es nicht.
    updated_at: datetime | None = None


def shipped_directory() -> Path:
    return Path(__file__).resolve().parent.parent / "ai_skills"


def _parse_shipped(path: Path) -> ShippedSkill | None:
    """Liest eine Skill-Datei. Bei Formfehlern ``None`` statt eines Absturzes.

    Eine beschaedigte mitgelieferte Datei darf das Panel nicht am Start
    hindern — sie faellt aus dem Verzeichnis, wird protokolliert, und alles
    Uebrige laeuft weiter. Dasselbe gilt fuer einen fehlenden Parser: ohne
    `yaml` ist jede Datei unlesbar, keine ist ein Grund zum Abbruch.
    """
    if yaml is None:
        logger.warning(
            "yaml fehlt — mitgelieferte Skills bleiben aus. "
            "Behebung: venv/bin/pip install -r requirements.txt"
        )
        return None

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Skill-Datei %s nicht lesbar (%s)", path.name, type(exc).__name__)
        return None

    match = _FRONTMATTER_RE.match(raw)
    if match is None:
        logger.warning("Skill-Datei %s hat keinen gueltigen Kopf", path.name)
        return None
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        logger.warning("Skill-Datei %s hat einen ungueltigen Kopf", path.name)
        return None
    if not isinstance(meta, dict):
        logger.warning("Skill-Datei %s hat einen ungueltigen Kopf", path.name)
        return None

    name = str(meta.get("name") or "").strip()
    description = str(meta.get("description") or "").strip()
    body = match.group(2).strip()
    key = path.stem.lower()
    if not name or not description or not body or not _SKILL_KEY_RE.match(key):
        logger.warning("Skill-Datei %s ist unvollstaendig", path.name)
        return None
    return ShippedSkill(
        skill_key=key,
        name=name[:MAX_NAME_CHARS],
        description=description[:MAX_DESCRIPTION_CHARS],
        body=body[:MAX_BODY_CHARS],
    )


def shipped_skills() -> dict[str, ShippedSkill]:
    """Alle mitgelieferten Skills, einmal gelesen und dann gehalten.

    Die Dateien aendern sich nur bei einem Update, und ein Update startet den
    Prozess neu. Ein Zwischenspeicher spart damit bei jeder Chatnachricht ein
    halbes Dutzend Dateizugriffe, ohne je veraltet zu sein.
    """
    global _shipped_cache
    if _shipped_cache is not None:
        return _shipped_cache
    with _shipped_lock:
        if _shipped_cache is not None:
            return _shipped_cache
        directory = shipped_directory()
        found: dict[str, ShippedSkill] = {}
        if directory.is_dir():
            for path in sorted(directory.glob("*.md")):
                parsed = _parse_shipped(path)
                if parsed is not None:
                    found[parsed.skill_key] = parsed
        else:
            logger.warning("Skill-Verzeichnis %s fehlt", directory)
        _shipped_cache = found
        return found


def reset_shipped_cache_for_tests() -> None:
    global _shipped_cache
    with _shipped_lock:
        _shipped_cache = None


# ── Sichtbarkeit ──────────────────────────────────────────────────────


def _scope_identity(team_id: int | None) -> str:
    return "global" if team_id is None else f"team:{team_id}"


def _overlay_rank(row: AiSkill) -> tuple[str, int, int]:
    """Der Rang einer Zeile bei der Ueberlagerung: eng schlaegt weit.

    Warum das hier steht und nicht als `ORDER BY` in der Abfrage: die Schleife
    in `_overlay` ueberschreibt, der zuletzt gelesene Eintrag gewinnt. Ohne
    festen Rang entscheidet die Datenbank, welche Fassung ein Benutzer sieht —
    und PostgreSQL sagt bei Gleichstand nichts zu. Derselbe Schluessel koennte
    von Nachricht zu Nachricht einen anderen Text liefern.

    Der Rang: global zuerst, Team danach. Ein Team-Skill ist die engere Aussage
    und darf die panelweite Vorgabe ueberschreiben, so wie eine globale Zeile
    die mitgelieferte Datei ueberschreibt. Gehoert jemand zwei Teams an, die
    denselben Schluessel belegen, gewinnt das zuletzt angelegte — die hoehere
    Team-Id. Diese letzte Regel ist willkuerlich, aber sie ist festgelegt, und
    genau darum geht es: zweimal dieselbe Frage muss zweimal denselben Skill
    finden.
    """
    return (row.skill_key, 0 if row.team_id is None else 1, row.team_id or 0)


def _overlay(by_key: dict[str, SkillView], rows: list[AiSkill]) -> None:
    """Legt die Datenbankzeilen ueber die mitgelieferten Dateien.

    Eine eigene Funktion, weil die Reihenfolge sonst nicht pruefbar waere:
    unter SQLite kommt die globale Zeile zufaellig zuerst, unter PostgreSQL
    nicht zwingend. Ein Test gegen die Abfrage haette den Fehler also nie
    gesehen — gegen diese Funktion sieht er ihn, weil die Zeilen als Liste
    hereinkommen und der Test die unguenstige Reihenfolge selbst waehlen kann.
    """
    for row in sorted(rows, key=_overlay_rank):
        if row.status != "active":
            # Wartet auf Freigabe: gilt nicht — und verdeckt auch nichts.
            continue
        if not row.enabled:
            standing = by_key.get(row.skill_key)
            # Abschalten verdeckt nur die mitgelieferte Datei. Vorher nahm der
            # Pop weg, was gerade unter dem Schluessel stand: kam die Zeile
            # eines Teams zuerst, loeschte das Abschalten einer globalen Zeile
            # den eingeschalteten Skill dieses Teams aus dem Verzeichnis —
            # abgeschaltet hatte ihn niemand.
            if row.team_id is None and standing is not None and standing.scope == "shipped":
                del by_key[row.skill_key]
            continue
        by_key[row.skill_key] = SkillView(
            id=row.id, skill_key=row.skill_key, name=row.name, description=row.description,
            scope="global" if row.team_id is None else "team",
            origin=row.origin, team_id=row.team_id, status=row.status,
            enabled=row.enabled, editable=True, updated_at=row.updated_at,
        )


def visible_skills(db: Session, user: User) -> list[SkillView]:
    """Das vollstaendige Skill-Verzeichnis fuer diesen Benutzer.

    Reihenfolge der Ueberlagerung: mitgeliefert zuerst, danach die Datenbank.
    Eine globale Zeile mit demselben Schluessel ersetzt die Datei — das ist der
    Weg, eine MSM-Vorgabe zu ueberschreiben. Eine **abgeschaltete** globale Zeile
    blendet die Datei aus, ohne selbst zu gelten: sonst koennte man eine
    mitgelieferte Vorgabe nicht loswerden.

    Eine **wartende** Zeile tut das ausdruecklich nicht. Der Unterschied sieht
    klein aus und ist es nicht: `pending` entsteht ohne jede Entscheidung eines
    Menschen — unter der Lernpolitik `review` legt jedes Kundengespraech solche
    Zeilen an. Wuerden sie ausblenden, koennte ein einziger `learn_skill`-Aufruf
    unter dem Schluessel `portkonflikt` die mitgelieferte Anleitung fuer **alle**
    Benutzer des Panels abschalten, bevor irgendjemand sie gesehen hat. Genau
    davor soll die Warteschlange schuetzen.

    Abschalten ist eine Handlung des Betreibers, Warten ist keine. Nur die
    Handlung darf wirken.

    Teamgrenzen entstehen hier ueber `scope_identity`, nicht ueber eine
    nachtraegliche Filterung — wer nicht im Team ist, dessen Abfrage fragt gar
    nicht erst danach.

    Welche Fassung bei gleichem Schluessel gewinnt, entscheidet `_overlay` und
    nicht die Datenbank; die Begruendung steht dort.
    """
    by_key: dict[str, SkillView] = {}
    for skill in shipped_skills().values():
        by_key[skill.skill_key] = SkillView(
            skill_key=skill.skill_key, name=skill.name, description=skill.description,
            scope="shipped", origin="shipped", team_id=None, status="active",
            enabled=True, editable=False,
        )

    team_ids = team_service.user_team_ids(db, user)
    scopes = ["global", *[f"team:{team_id}" for team_id in team_ids]]
    # Bewusst ohne `order_by`: die Ordnung, auf die es ankommt, macht
    # `_overlay` selbst. Zwei Stellen, die beide eine Reihenfolge zusagen,
    # waeren eine Stelle zu viel.
    rows = db.query(AiSkill).filter(AiSkill.scope_identity.in_(scopes)).all()
    _overlay(by_key, rows)
    return sorted(by_key.values(), key=lambda item: item.skill_key)


def read_body(db: Session, user: User, skill_key: str) -> tuple[SkillView, str]:
    """Laedt den Text eines Skills — Stufe zwei des schrittweisen Ladens."""
    key = (skill_key or "").strip().lower()
    view = next((item for item in visible_skills(db, user) if item.skill_key == key), None)
    if view is None:
        raise HTTPException(status_code=404, detail="Skill nicht gefunden")
    if view.id is not None:
        row = db.get(AiSkill, view.id)
        if row is None:
            raise HTTPException(status_code=404, detail="Skill nicht gefunden")
        return view, row.body
    shipped = shipped_skills().get(key)
    if shipped is None:
        raise HTTPException(status_code=404, detail="Skill nicht gefunden")
    return view, shipped.body


# ── Auswahl fuer den Systemprompt ─────────────────────────────────────


def _index_text(skill_key: str, name: str, description: str) -> str:
    """Der Text, aus dem der Auswahlvektor entsteht — eine Stelle fuer beide Seiten.

    Vorher stand derselbe Satz zweimal im Modul: einmal beim Speichern, einmal
    beim Auswaehlen. Solange der gespeicherte Vektor nie gelesen wurde, fiel ein
    Auseinanderlaufen nicht auf. Jetzt wird er gelesen — und ein Vektor, der aus
    einem anderen Text stammt als der Vergleichstext, waere ein stiller Fehler
    in der Auswahl.
    """
    readable = skill_key.replace("-", " ")
    return f"{readable}: {name}. {description}"


def _index_source(view: SkillView) -> str:
    return _index_text(view.skill_key, view.name, view.description)


def _stored_vector(row: AiSkill) -> list[float] | None:
    """Liest den gespeicherten Auswahlvektor, wenn er zum geladenen Modell passt.

    Die fehlende Gegenstelle zu `refresh_embedding`, gebaut wie die des
    Gedaechtnisses (`ai_memory_service._stored_vector`). Passt der Modellname
    nicht oder stimmt die Laenge nicht, gilt der Vektor als nicht vorhanden und
    der Skill wird frisch kodiert: ein Modellwechsel heilt sich damit von
    selbst, ohne Migration und ohne falsche Aehnlichkeiten in der Zwischenzeit.
    """
    if not row.embedding_json or row.embedding_model != _EMBEDDING_MODEL_TAG:
        return None
    try:
        vector = json.loads(row.embedding_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(vector, list) or len(vector) != EMBEDDING_DIMENSIONS:
        return None
    return vector


def refresh_embedding(row: AiSkill) -> None:
    """Berechnet den Auswahlvektor neu, falls ein Modell geladen ist.

    Schlaegt es fehl, bleibt der alte Wert stehen und der Skill wird eben
    alphabetisch eingeordnet. Ein Skill darf nicht daran scheitern, dass ein
    Modell fehlt.
    """
    vectors = ai_embedding_service.encode(
        [_index_text(row.skill_key, row.name, row.description)]
    )
    if not vectors:
        return
    row.embedding_json = json.dumps(vectors[0], separators=(",", ":"))
    row.embedding_model = _EMBEDDING_MODEL_TAG


def _candidate_vectors(db: Session, views: list[SkillView]) -> list[list[float]] | None:
    """Die Vergleichsvektoren aller sichtbaren Skills — gespeichert, wo es geht.

    Diese Funktion laeuft bei **jeder** Chatnachricht, sobald mehr Skills
    sichtbar sind als in den Prompt passen. Bis hierher wurde dabei jedes Mal
    alles neu kodiert, obwohl `upsert_skill` fuer jede Datenbankzeile laengst
    einen Vektor abgelegt hatte: die Spalte wurde geschrieben und von keiner
    Stelle je gelesen.

    Neu kodiert wird jetzt nur noch, was keinen brauchbaren Vektor hat — die
    mitgelieferten Skills, die als Datei gar keine Zeile besitzen, und Zeilen
    aus der Zeit eines anderen Embeddingmodells.

    ``None`` heisst "keine Auswahl moeglich"; dann bleibt es bei der
    alphabetischen Reihenfolge, genau wie ohne Modell.
    """
    stored: dict[str, list[float]] = {}
    ids = [view.id for view in views if view.id is not None]
    if ids:
        for row in db.query(AiSkill).filter(AiSkill.id.in_(ids)).all():
            vector = _stored_vector(row)
            if vector is not None:
                stored[row.id] = vector

    missing = [view for view in views if stored.get(view.id) is None]
    fresh: list[list[float]] = []
    if missing:
        encoded = ai_embedding_service.encode([_index_source(view) for view in missing])
        if encoded is None or len(encoded) != len(missing):
            return None
        fresh = encoded

    nachschub = iter(fresh)
    candidates: list[list[float]] = []
    for view in views:
        vector = stored.get(view.id)
        candidates.append(vector if vector is not None else next(nachschub))
    return candidates


def _neueste_zuerst(views: list[SkillView]) -> list[SkillView]:
    """Der Notschnitt, wenn die Bedeutungsauswahl nicht greift.

    Hier stand viermal `views[:MAX_INDEXED_SKILLS]`, und `visible_skills` gibt
    alphabetisch sortiert zurück. Der Schnitt war damit keine Reihenfolge,
    sondern eine **Auswahl**: was hinter Position 25 stand, kam nie in den
    Systemprompt — das Modell erfuhr seine Existenz nicht und forderte ihn
    folglich nie mit `read_skill` an. Ein frisch gelernter Skill blieb dauerhaft
    wirkungslos, allein weil sein Schlüssel spät im Alphabet steht, während die
    Oberfläche ihn als aktiv führt.

    Stattdessen: die mitgelieferten Skills bleiben — sie haben kein
    Änderungsdatum und sind die Störungsdrehbücher —, die übrigen Plätze gehen
    an die zuletzt geänderten. Zurück kommt wieder alphabetisch; am sichtbaren
    Verzeichnis ändert sich dadurch nichts.

    Bewusst **kein** zweites Auswahlverfahren mit Nutzungszählern und
    Halbwertszeiten wie beim Gedächtnis: ein Feld und ein Sortierschlüssel
    reichen, um "nie" in "später" zu verwandeln.
    """
    if len(views) <= MAX_INDEXED_SKILLS:
        return list(views)
    mitgeliefert = [item for item in views if item.updated_at is None]
    geaendert = sorted(
        (item for item in views if item.updated_at is not None),
        key=lambda item: item.updated_at,
        reverse=True,
    )
    gewaehlt = [*mitgeliefert, *geaendert][:MAX_INDEXED_SKILLS]
    return sorted(gewaehlt, key=lambda item: item.skill_key)


def skill_index(db: Session, user: User, query: str = "") -> list[SkillView]:
    """Die Skills, die in das Skill-Verzeichnis kommen — eine eigene, als Daten
    gekennzeichnete Nachricht direkt hinter dem Systemprompt
    (`ai_context_service._skill_index_message`).

    Passt alles ins Budget, kommt alles mit — der Normalfall. Erst darueber
    entscheidet die Bedeutungsaehnlichkeit zur Frage, und zwar ueber dasselbe
    mehrsprachige Modell wie beim Gedaechtnis: eine englische Frage findet damit
    einen deutsch beschriebenen Skill.

    Ohne Modell entscheidet das Änderungsdatum (`_neueste_zuerst`). Schlechter
    als Bedeutung, aber besser als gar kein Verzeichnis — und vor allem besser
    als der alphabetische Schnitt, der hier einmal stand: der sperrte dieselben
    Schlüssel dauerhaft aus, statt nur schlechter zu ordnen.

    **Die Aehnlichkeit ordnet, sie waehlt nicht aus.** Der naheliegende Ausbau
    — eine Mindestaehnlichkeit, unter der ein Skill aus dem Verzeichnis faellt —
    ist gemessen worden und traegt nicht. Gegen die sechs mitgelieferten Skills:
    eine reine Konfigurationsfrage ("wieviel Holz bekomme ich pro Baum") liegt
    bei `node-problem` auf 0,49, waehrend die passende Frage nach Erreichbarkeit
    ihren eigenen Skill nur auf 0,37 bringt. Statische Embeddings messen die
    thematische Naehe zu "Gameserver", nicht die Frage "ist das ueberhaupt eine
    Stoerung" — und genau die entscheidet hier. Jede Schwelle wuerde also
    richtige Treffer verwerfen und falsche behalten.
    Die Unterscheidung steht deshalb dort, wo sie hingehoert: in den
    Beschreibungen (jede sagt auch, wann sie *nicht* gilt) und in der Kopfzeile
    des Verzeichnisses (`ai_context_service._skill_index_block`).
    """
    views = visible_skills(db, user)
    if len(views) <= MAX_INDEXED_SKILLS or not query.strip():
        return _neueste_zuerst(views)

    query_vectors = ai_embedding_service.encode([query])
    if not query_vectors:
        return _neueste_zuerst(views)
    candidates = _candidate_vectors(db, views)
    if candidates is None:
        return _neueste_zuerst(views)

    scores = ai_embedding_service.similarity(query_vectors[0], candidates)
    if len(scores) != len(views):
        return _neueste_zuerst(views)
    ranked = sorted(zip(views, scores), key=lambda item: item[1], reverse=True)
    selected = [view for view, _score in ranked[:MAX_INDEXED_SKILLS]]
    # Lesbare Reihenfolge wiederherstellen statt die Rangfolge zu zeigen.
    return sorted(selected, key=lambda item: item.skill_key)


# ── Schreiben ─────────────────────────────────────────────────────────


def _safe_text(value: str, limit: int, label: str) -> str:
    text = (value or "").strip()
    if not text or len(text) > limit:
        raise HTTPException(status_code=422, detail=f"{label} ist leer oder zu lang")
    # Dieselbe Pruefung wie beim Gedaechtnis (`_safe_value`): abgewiesen wird,
    # was Zugangsdaten TRAEGT — nicht alles, was die Schwaerzung anfassen
    # wuerde. Ein Skill "Rechnungslauf" mit einer Beispieladresse oder einem
    # Zahlenkontingent ist kein Geheimnis­transport.
    if enthaelt_zugangsdaten(text):
        raise HTTPException(status_code=422, detail=f"{label} darf keine Zugangsdaten enthalten")
    return text


def _normalized_key(value: str) -> str:
    key = (value or "").strip().lower()
    if not _SKILL_KEY_RE.match(key):
        raise HTTPException(
            status_code=422,
            detail="Skill-Schluessel: Kleinbuchstaben, Ziffern und Bindestriche, 2 bis 64 Zeichen",
        )
    return key


def assert_may_write(db: Session, user: User, team_id: int | None) -> None:
    """Wer diesen Bereich veraendern darf.

    Global verlangt `ai.skills.manage` — ein globaler Skill wirkt fuer jeden
    Benutzer des Panels, einschliesslich aller Kunden eines Hosters. Team
    verlangt den Schalter in der Mitgliedschaft. In beiden Faellen gilt
    derselbe Satz wie beim Gedaechtnis: **die KI kann nie mehr teilen, als der
    Benutzer selbst teilen duerfte.**
    """
    if team_id is None:
        if not permission_service.has_global_permission(db, user, "ai.skills.manage"):
            raise HTTPException(status_code=403, detail="Keine Berechtigung")
        return
    if not team_service.can_manage_team_skills(db, user, team_id):
        raise HTTPException(
            status_code=403, detail="Du darfst die Skills dieses Teams nicht veraendern"
        )


def upsert_skill(
    db: Session, *, user: User, skill_key: str, name: str, description: str, body: str,
    team_id: int | None, origin: str = "operator", status: str = "active",
    enabled: bool = True, skip_permission_check: bool = False,
) -> AiSkill:
    """Legt einen Skill an oder ersetzt ihn unter demselben Schluessel.

    Bewusst ohne Versionierung: ein Skill ist Text, kein Vertrag. Wer die
    Entwicklung nachvollziehen will, findet sie im Audit-Log; wer eine aeltere
    Fassung braucht, hat sie im Panel-Backup. Eine Versionstabelle haette dafuer
    eine eigene Oberflaeche gebraucht, die niemand verlangt hat.

    ``skip_permission_check`` gibt es fuer genau einen Fall: einen global
    gelernten Skill, der ohne `ai.skills.manage` entsteht und deshalb als
    `pending` in der Warteschlange landet. Der Aufrufer hat die Lage dort
    bereits geprueft; ohne diesen Ausweg waere die Warteschlange unerreichbar.
    """
    if origin not in {"operator", "ai"}:
        raise HTTPException(status_code=422, detail="Unbekannte Skill-Herkunft")
    if status not in {"active", "pending"}:
        raise HTTPException(status_code=422, detail="Unbekannter Skill-Status")
    if not skip_permission_check:
        assert_may_write(db, user, team_id)

    key = _normalized_key(skill_key)
    identity = _scope_identity(team_id)
    safe_name = _safe_text(name, MAX_NAME_CHARS, "Skill-Name")
    safe_description = _safe_text(description, MAX_DESCRIPTION_CHARS, "Skill-Beschreibung")
    safe_body = _safe_text(body, MAX_BODY_CHARS, "Skill-Text")

    row = (
        db.query(AiSkill)
        .filter(AiSkill.scope_identity == identity, AiSkill.skill_key == key)
        .first()
    )
    action = "ai.skill.updated"
    if row is None:
        # Getrennt gezaehlt: freigegebene Skills gegen das Bereichskontingent,
        # wartende gegen ihr eigenes. Vorher zaehlte beides zusammen — und
        # wartende Zeilen entstehen ohne Zutun eines Menschen: unter der
        # Lernpolitik `review` legt jedes Kundengespraech welche an, und die
        # Berechtigungspruefung entfaellt dabei ausdruecklich
        # (`skip_permission_check`). Zweihundert Vorschlaege haetten dem
        # Betreiber damit das Anlegen eigener globaler Skills gesperrt, obwohl
        # kein einziger davon je gewirkt hat. Eine Vorschlagsliste darf
        # niemandem etwas wegnehmen.
        limit = MAX_PENDING_PER_SCOPE if status == "pending" else MAX_SKILLS_PER_SCOPE
        count = (
            db.query(AiSkill)
            .filter(AiSkill.scope_identity == identity, AiSkill.status == status)
            .count()
        )
        if count >= limit:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Die Warteschlange fuer globale Skills ist voll. Der Betreiber "
                    "muss erst freigeben oder verwerfen — lege die Erkenntnis "
                    "solange mit scope='team' an."
                    if status == "pending"
                    else "Skill-Bereich ist voll"
                ),
            )
        row = AiSkill(
            id=str(uuid4()), scope_identity=identity, team_id=team_id, skill_key=key,
            name=safe_name, description=safe_description, body=safe_body,
            origin=origin, status=status, enabled=enabled, created_by=user.id,
        )
        db.add(row)
        action = "ai.skill.created"
    elif origin == "ai" and row.origin == "operator":
        # Dieselbe Regel wie beim Gedaechtnis: was ein Mensch geschrieben hat,
        # ueberschreibt die KI nicht stillschweigend.
        #
        # Die Meldung riet einmal "Verwende einen anderen Schlüssel." — und das
        # war genau der falsche Ausweg. Sie geht als Werkzeugantwort an das
        # Modell, dessen Systemprompt daneben sagt, es solle für eine passende
        # Erkenntnis **denselben** Schlüssel nehmen. Das Modell legte also
        # `valheim-ram-2` an, und im Verzeichnis standen ab da zwei Skills zum
        # selben Thema mit widersprechenden Zahlen. Die Schranke bleibt; nur der
        # Ausweg ist jetzt einer, der kein Duplikat erzeugt.
        raise HTTPException(
            status_code=409,
            detail=(
                "Dieser Skill stammt von einem Menschen und wird nicht automatisch "
                "überschrieben. Lege keinen ähnlichen zweiten an — sag dem "
                "Benutzer, was du herausgefunden hast."
            ),
        )
    elif status == "pending" and row.status == "active":
        # Die Warteschlange ist fuer **neue** Erkenntnisse da, nicht zum
        # Zuruecknehmen freigegebener. Ohne diese Regel setzt der Update-Zweig
        # unten `row.status = "pending"` — ein bereits freigegebener, panelweit
        # wirksamer Skill waere damit aus einem beliebigen Kundengespraech heraus
        # abschaltbar, und zwar ohne dass jemand etwas bestaetigt haette.
        #
        # Denselben Weg gab es einen Schritt frueher schon einmal: eine wartende
        # Zeile blendete die mitgelieferte Datei desselben Schluessels aus. Beides
        # ist derselbe Fehler in zwei Gestalten — Warten ist keine Entscheidung
        # und darf nichts entwerten.
        raise HTTPException(
            status_code=409,
            detail=(
                "Dieser Skill ist bereits panelweit freigegeben und laesst sich "
                "ohne die Berechtigung `ai.skills.manage` nicht aendern. Lege die "
                "Erkenntnis mit scope='team' an."
            ),
        )
    else:
        row.name = safe_name
        row.description = safe_description
        row.body = safe_body
        row.origin = origin
        row.status = status
        row.enabled = enabled
    refresh_embedding(row)
    row.updated_at = datetime.now(timezone.utc)

    audit_service.record_privileged_action(
        db, user_id=user.id, action=action, target_type="ai_skill", target_id=row.id,
        details={"skill_key": key, "scope": identity, "origin": origin, "status": status},
        origin="ai" if origin == "ai" else "direct",
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Skill wurde parallel geaendert. Bitte erneut versuchen.",
        ) from exc
    db.refresh(row)
    return row


def get_skill(db: Session, skill_id: str) -> AiSkill:
    try:
        canonical = str(UUID(skill_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=404, detail="Skill nicht gefunden") from exc
    row = db.get(AiSkill, canonical)
    if row is None:
        raise HTTPException(status_code=404, detail="Skill nicht gefunden")
    return row


def set_enabled(db: Session, *, user: User, skill_id: str, enabled: bool) -> AiSkill:
    row = get_skill(db, skill_id)
    assert_may_write(db, user, row.team_id)
    row.enabled = enabled
    row.updated_at = datetime.now(timezone.utc)
    audit_service.record_privileged_action(
        db, user_id=user.id, action="ai.skill.toggled", target_type="ai_skill",
        target_id=row.id, details={"skill_key": row.skill_key, "enabled": enabled},
        origin="direct",
    )
    db.commit()
    db.refresh(row)
    return row


def approve(db: Session, *, user: User, skill_id: str) -> AiSkill:
    """Gibt einen global gelernten Skill frei, der auf Freigabe wartet."""
    row = get_skill(db, skill_id)
    if row.team_id is not None:
        raise HTTPException(status_code=409, detail="Nur globale Skills brauchen eine Freigabe")
    if not permission_service.has_global_permission(db, user, "ai.skills.manage"):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    row.status = "active"
    row.updated_at = datetime.now(timezone.utc)
    audit_service.record_privileged_action(
        db, user_id=user.id, action="ai.skill.approved", target_type="ai_skill",
        target_id=row.id, details={"skill_key": row.skill_key}, origin="direct",
    )
    db.commit()
    db.refresh(row)
    return row


def delete_skill(db: Session, *, user: User, skill_id: str) -> None:
    row = get_skill(db, skill_id)
    assert_may_write(db, user, row.team_id)
    audit_service.record_privileged_action(
        db, user_id=user.id, action="ai.skill.deleted", target_type="ai_skill",
        target_id=row.id, details={"skill_key": row.skill_key}, origin="direct",
    )
    db.delete(row)
    db.commit()


def manageable_skills(db: Session, user: User) -> list[AiSkill]:
    """Alle Zeilen, die dieser Benutzer verwalten darf — auch abgeschaltete.

    Global nur mit `ai.skills.manage`; Teams nur die, in denen der Schalter
    gesetzt ist. Wer nichts verwalten darf, bekommt eine leere Liste und keine
    Fehlermeldung: die Oberflaeche zeigt den Bereich dann schlicht nicht.
    """
    scopes: list[str] = []
    if permission_service.has_global_permission(db, user, "ai.skills.manage"):
        scopes.append("global")
    for team_id in team_service.user_team_ids(db, user):
        if team_service.can_manage_team_skills(db, user, team_id):
            scopes.append(f"team:{team_id}")
    if not scopes:
        return []
    return (
        db.query(AiSkill)
        .filter(AiSkill.scope_identity.in_(scopes))
        .order_by(AiSkill.scope_identity, AiSkill.skill_key)
        .all()
    )


def pending_skills(db: Session) -> list[AiSkill]:
    """Global gelernte Skills, die auf die Freigabe des Betreibers warten."""
    return (
        db.query(AiSkill)
        .filter(AiSkill.scope_identity == "global", AiSkill.status == "pending")
        .order_by(AiSkill.created_at.desc())
        .all()
    )
