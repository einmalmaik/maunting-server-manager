"""Auflösung und Persistenz rollenbasierter KI-Limits.

Die Regeln sind absichtlich klein und deterministisch:
- hat *keine* Rolle des Benutzers eine Konfiguration, gilt „unbegrenzt“,
- unter den konfigurierten Rollen gewinnt der höchste Wert.

Ein leeres Feld (``None``) ist in **jedem** Feld ein Wert, nämlich „unbegrenzt“
und damit der höchste — deshalb gewinnt er. Das gilt seit dem 05.10.2026 auch
für den Memory-Vorrat: dort hieß ``None`` vorher „nichts hinterlegt“ und wurde
beim Merken zur festen Systemgrenze von 100. Der Betreiber sah in der Maske
aber „Unbegrenzt“ eingeschaltet und bekam 100 — eine Zusage, die das Panel
nicht hielt. „Unbegrenzt“ heißt jetzt überall unbegrenzt, 0 heißt überall 0.

Die erste Regel ist bewusst so und war früher anders: eine leere Zeilenmenge
ergab über ``max(..., default=0)`` ein effektives Limit von **0** und damit eine
KI, die auf jeder frischen Installation jede Anfrage mit „Kontingent
ausgeschöpft“ abwies — auch für Owner und Admin. Das war kein sicherer Default,
sondern ein stiller Totalausfall. Die Zugangsgrenze zur KI ist ``ai.chat.use``;
die Limits hier sind Kostensteuerung. Solange der Betreiber dazu gar nichts
hinterlegt hat, darf MSM ihm keine Politik unterstellen.

Sobald **mindestens eine** Rolle des Benutzers konfiguriert ist, gilt wieder die
alte Auflösung: unkonfigurierte Rollen tragen nichts bei, der höchste Wert der
konfigurierten gewinnt. Eine zusätzliche, privilegierte Rolle erhöht damit das
Kontingent (Zielpunkt 6.1) und eine bewusst auf 0 gesetzte Rolle sperrt, solange
keine andere Rolle mehr erlaubt.

Verbrauch wird erst an den späteren Provider-/Chat-Endpunkten gezählt. Dieses
Modul stellt dafür die zentrale, backendseitige Grenzauflösung bereit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from models import Role, RoleAiLimit, Team, User
from services.role_service import effective_user_role_ids


# Genau die Breite von PostgreSQL INTEGER (2^31-1). Die drei Tokenspalten in
# `models/role_ai_limit.py` sind INTEGER; eine höhere Obergrenze hier hätte die
# Oberfläche Werte anbieten lassen, die beim Speichern in einen
# NumericValueOutOfRange laufen — den der Router als „gleichzeitige Änderung“
# (HTTP 409) meldet, also mit einer Ursache, die es gar nicht gibt.
TOKEN_LIMIT_MAX = 2_147_483_647
REQUESTS_PER_MINUTE_MAX = 10_000
CONCURRENT_OPERATIONS_MAX = 100
MONTHLY_COST_LIMIT_CENTS_MAX = 1_000_000_000
DICTATION_MINUTES_LIMIT_MAX = 100_000
# Hoechster Rang aus `ai_reasoning.RANGFOLGE` (minimal..max). Bewusst als Zahl
# hier statt als Import: dieses Modul soll nicht von der Denklogik abhaengen,
# und `test_ai_reasoning_limits.py` sichert zu, dass beide Werte gleich bleiben.
MAX_REASONING_EFFORT_MAX = 6
# Deckel für das konfigurierbare Rollenlimit. Er begrenzt **einen Bereich**,
# nicht eine Anfrage — und er ist eine Obergrenze für das, was der Betreiber
# als **Zahl** einstellen darf, nicht eine Zahl, die irgendwo von selbst gilt.
# Über ihm liegt nur noch „Unbegrenzt“ (``None``) — wie bei den Kontingenten
# auch dann, wenn keine Rolle des Benutzers etwas hinterlegt hat. Wer die
# Kosten unten begrenzen will, trägt eine Zahl ein. Hier stand vorher, 1_000 statt 10_000 verhindere eine Selbst-DoS
# — eine Schutzwirkung, die diese Zahl nicht leisten kann, und deshalb war sie
# das Gefährlichste, was hier stehen konnte: sie beruhigt an der Stelle, an der
# jemand nachrechnen müsste.
#
# Gezählt wird je ``scope_identity``: der persönliche Vorrat ist ein Bereich,
# jeder sichtbare Server einer und jedes gegründete Team einer. Wieviele
# Bereiche ein Benutzer hat, bestimmt damit er selbst, und die Zeilenmenge, die
# eine Anfrage aus der Datenbank holt, ist Bereiche × Deckel. Ein VIP mit 5_000
# und `server.view` auf zwanzig Anlagen bringt so über 105.000 Zeilen mit; fünf
# sichtbare Server reichen für 30.000. Dagegen hilft eine Zahl je Bereich
# grundsätzlich nicht.
#
# Seit Gedächtnis v2, Stufe 4 lädt eine Chatanfrage davon nichts mehr am
# Stück (`ai_gedaechtnis_abruf`): „im Kopf“ wählt die Datenbank und öffnet
# höchstens `MAX_CONTEXT_ROWS`, was zur Frage passt, kommt aus Vektorspeicher
# und Wortindex über höchstens 2 × 60 Kandidaten. Nachzulesen in
# `backend/tests/test_ai_gedaechtnis_abruf.py`. Die Messung unten beschreibt
# den Abruf davor.
#
# Warum dann überhaupt eine Grenze, und warum diese? Drei Kosten wachsen mit
# dem Bestand, und zwei davon zahlt nicht der, der ihn angehäuft hat. Gemessen
# am 19.08.2026 (local-plans/mess-gedaechtnis.py, SQLite im Speicher):
#
#   - Der Abruf lädt **alle** Zeilen der sichtbaren Bereiche, bevor
#     `_vorauswahl` überhaupt auswählen kann — 208 ms Rechenzeit bei 1.000
#     Einträgen, 717 ms bei 5.000, linear mit dem Bestand. Das ist Zeit vor dem
#     ersten Byte an den Anbieter, in **jeder** Anfrage, auch in denen, die mit
#     dem Vorrat nichts zu tun haben.
#   - Die Verwaltungsansicht `personal_entries` (ai_memory_service.py)
#     entschlüsselt bewusst jede Zeile — mit gutem Grund, siehe dort — und das
#     ist je Zeile ein Sidecar-Roundtrip ohne jeden Deckel: 5.000 Einträge sind
#     dort 2,8 s bei 0,5 ms je Roundtrip und 10,3 s bei 2 ms, und das mal der
#     Zahl der Bereiche. Sie ist die erste Stelle, die eine Anhebung merkt.
#   - In geteilten Bereichen (`team`, `server_shared`, `panel`) trägt beides
#     jeder mit, der den Bereich sieht, und nicht der Schreiber.
#
# Was dabei **nicht** wächst, ist das, was beim Modell ankommt: der Block ist
# auf `MAX_CONTEXT_CHARS` begrenzt und war in derselben Messung bei 100 wie bei
# 5.000 Einträgen rund 6.060 Zeichen lang, also etwa 90 Zeilen. Ein größerer
# Vorrat macht die KI nicht klüger — er gibt der Auswahl mehr zu tun. Wer diese
# Zahl weiter anhebt, verschiebt also nicht die Antwortqualität, sondern nur
# die drei Kosten oben; ab hier lohnt sich zuerst eine bessere Auswahl.
#
# 5.000 ist danach die Zahl, bei der der Abruf im Zehntelsekundenbereich
# bleibt und die Verwaltungsansicht in Sekunden statt Minuten. „Unbegrenzt"
# garantiert keine dieser beiden — es ist trotzdem wählbar, weil die Rechnung
# dem gehört, der den Schalter umlegt, und weil der Abruf seit Stufe 4 nicht
# mehr mit dem Vorrat wächst.
MAX_MEMORY_ENTRIES_MAX = 5_000
# Feste Systemgrenze fuer die Bereiche, die an keiner Benutzerrolle haengen:
# `server_shared` gehoert der Anlage, `panel` dem Betreiber. Das Kontingent des
# gerade schreibenden Benutzers waere dort das falsche Mass — es haengt daran,
# wer den Eintrag zufaellig anlegt, und nicht daran, wem der Vorrat gehoert.
# Eine Obergrenze braucht es trotzdem, und deshalb steht hier eine feste Zahl
# statt ``None``: `panel` fliesst in *jeden* Prompt, `server_shared` in jeden
# mit Serverbezug. Hier stand "beide Bereiche fliessen in jeden Prompt"; fuer
# `server_shared` stimmt das seit dem Serverfilter in
# `provider_memory_context` nicht mehr — von zwanzig sichtbaren Anlagen kommt
# nur die eine mit, um die es gerade geht, und ohne Serverbezug gar keine. Am
# Schluss aendert das nichts: unbegrenzt hiesse in beiden Faellen unbegrenzter
# Prefill, und weil an diesen Bereichen keine Rolle haengt, gaebe es auch
# niemanden, ueber den der Betreiber es wieder einfangen koennte.
# Fuer die rollengebundenen Bereiche ist diese Zahl seit dem 05.10.2026 *kein*
# Rueckfall mehr: dort heisst ein leeres Feld unbegrenzt, siehe
# `resolve_scope_memory_limit`. Sie gilt nur noch fuer Bereiche ohne Rolle und
# fuer einen Bereich, der sich nicht aufloesen laesst.
MAX_SYSTEM_SCOPE_ENTRIES = 100

LIMIT_FIELDS = (
    "daily_token_limit",
    "weekly_token_limit",
    "monthly_token_limit",
    "requests_per_minute",
    "concurrent_operations",
    "monthly_cost_limit_cents",
    "monthly_realtime_cost_limit_cents",
    "monthly_dictation_minutes_limit",
    # Kein Kontingent, sondern eine Obergrenze fuer die Denktiefe — passt aber
    # in genau dieselbe Aufloesung: "None heisst unbegrenzt", "der hoechste
    # Wert der konfigurierten Rollen gewinnt", "keine Rolle konfiguriert heisst
    # unbegrenzt". Eine zweite Aufloesung daneben waere eine zweite Wahrheit.
    "max_reasoning_effort",
    # Auch kein Kontingent, sondern ein Vorrat: wieviele Memory-Eintraege je
    # Bereich bestehen duerfen. Steht hier, weil ein Tarif den Vorrat ueber die
    # Rolle verkaufen koennen soll, statt dass eine Konstante im Memory-Service
    # fuer alle entscheidet. Es folgt denselben Regeln wie die Kontingente:
    # der hoechste Wert gewinnt, ``None`` heisst unbegrenzt. Bis zum
    # 05.10.2026 hiess ``None`` hier „nichts hinterlegt“ und wurde beim Merken
    # zu ``MAX_SYSTEM_SCOPE_ENTRIES`` — waehrend die Maske „Unbegrenzt“ zeigte.
    "max_memory_entries",
)
LIMIT_MAXIMA = {
    "daily_token_limit": TOKEN_LIMIT_MAX,
    "weekly_token_limit": TOKEN_LIMIT_MAX,
    "monthly_token_limit": TOKEN_LIMIT_MAX,
    "requests_per_minute": REQUESTS_PER_MINUTE_MAX,
    "concurrent_operations": CONCURRENT_OPERATIONS_MAX,
    "monthly_cost_limit_cents": MONTHLY_COST_LIMIT_CENTS_MAX,
    "monthly_realtime_cost_limit_cents": MONTHLY_COST_LIMIT_CENTS_MAX,
    "monthly_dictation_minutes_limit": DICTATION_MINUTES_LIMIT_MAX,
    "max_reasoning_effort": MAX_REASONING_EFFORT_MAX,
    "max_memory_entries": MAX_MEMORY_ENTRIES_MAX,
}


@dataclass(frozen=True)
class EffectiveAiLimits:
    """Unveränderliche effektive KI-Grenzen eines Benutzers."""

    daily_token_limit: int | None
    weekly_token_limit: int | None
    monthly_token_limit: int | None
    requests_per_minute: int | None
    concurrent_operations: int | None
    monthly_cost_limit_cents: int | None
    monthly_realtime_cost_limit_cents: int | None
    #: Monatliches Diktier- und Transkriptionszeitlimit in Minuten; ``None`` heisst unbegrenzt.
    monthly_dictation_minutes_limit: int | None
    #: Hoechste erlaubte Denkstufe als Rang; ``None`` heisst unbegrenzt.
    max_reasoning_effort: int | None
    #: Memory-Vorrat je rollengebundenem Bereich; ``None`` heisst unbegrenzt.
    #: Welche Grenze in welchem Bereich gilt (Team am Gruender, Systembereiche
    #: fest), entscheidet ``resolve_scope_memory_limit``.
    max_memory_entries: int | None


def get_role_limit(db: Session, role_id: int) -> RoleAiLimit | None:
    """Liest eine explizite Rollenkonfiguration oder ``None``."""
    return db.query(RoleAiLimit).filter(RoleAiLimit.role_id == role_id).first()


def set_role_limit(
    db: Session,
    role_id: int,
    values: dict[str, int | None],
) -> RoleAiLimit:
    """Ersetzt alle KI-Limits einer existierenden Rolle in der offenen Transaktion."""
    if db.query(Role.id).filter(Role.id == role_id).first() is None:
        raise ValueError("Rolle nicht gefunden")
    if set(values) != set(LIMIT_FIELDS):
        raise ValueError("Unvollständige KI-Limit-Konfiguration")
    for field, maximum in LIMIT_MAXIMA.items():
        value = values[field]
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
            raise ValueError(f"Ungültiger Wert für {field}")

    row = get_role_limit(db, role_id)
    if row is None:
        row = RoleAiLimit(role_id=role_id, **values)
        db.add(row)
    else:
        for field in LIMIT_FIELDS:
            setattr(row, field, values[field])
        row.updated_at = datetime.now(timezone.utc)
    db.flush()
    return row


def _resolve_field(rows: list[RoleAiLimit], field: str) -> int | None:
    """Löst ein Feld unter den *konfigurierten* Rollen auf.

    ``rows`` ist hier garantiert nicht leer — den leeren Fall behandelt
    ``resolve_effective_limits`` vorher, weil er eine andere Bedeutung hat
    („gar keine Politik hinterlegt“ statt „auf 0 gesetzt“).

    Aufgelöst wird für alle Felder gleich: der höchste Wert gewinnt, und
    ``None`` („unbegrenzt“) ist der höchste.
    """
    configured = [getattr(row, field) for row in rows]
    if any(value is None for value in configured):
        return None
    return max(int(value) for value in configured)


UNLIMITED_AI_LIMITS = EffectiveAiLimits(**{field: None for field in LIMIT_FIELDS})


def resolve_effective_limits(db: Session, user: User) -> EffectiveAiLimits:
    """Vereinigt Limits aller effektiven Rollen, ohne eine Anfrage zu zählen."""
    role_ids = effective_user_role_ids(db, user)
    rows = (
        db.query(RoleAiLimit).filter(RoleAiLimit.role_id.in_(role_ids)).all()
        if role_ids
        else []
    )
    if not rows:
        # Keine einzige Rolle des Benutzers hat ein KI-Kontingent hinterlegt.
        # Siehe Modul-Docstring: das ist „nicht konfiguriert“, nicht „gesperrt“.
        return UNLIMITED_AI_LIMITS
    return EffectiveAiLimits(
        **{field: _resolve_field(rows, field) for field in LIMIT_FIELDS}
    )


def resolve_scope_memory_limit(
    db: Session,
    scope: str,
    user: User,
    team_id: int | None = None,
    server_id: int | None = None,
) -> int | None:
    """Wieviele Memory-Eintraege in *diesem* Bereich stehen duerfen.

    ``None`` heisst unbegrenzt — dieselbe Lesart wie bei jedem anderen Feld
    dieses Moduls und dieselbe, die der Schalter „Unbegrenzt“ in der Maske
    verspricht. Eine 0 ist eine Sperre („diese Rolle darf sich nichts
    merken“), kein fehlender Wert.

    Bis zum 05.10.2026 kam hier immer eine Zahl heraus: ein leeres Feld wurde
    zu ``MAX_SYSTEM_SCOPE_ENTRIES``. Wer in der Maske „Unbegrenzt“ eingeschaltet
    hatte, konnte trotzdem nur 100 Eintraege speichern. Die Kosten eines grossen
    Vorrats stehen bei ``MAX_MEMORY_ENTRIES_MAX``; der Abruf in den Kontext
    waechst seit Stufe 4 nicht mehr mit ihm (``ai_gedaechtnis_abruf``).

    Wem der Vorrat gehoert, entscheidet der Bereich:

    - ``user`` und ``server`` sind der persoenliche Vorrat des Schreibenden, sie
      haengen an seinem Rollenlimit. ``server_id`` aendert daran nichts; es
      steht nur in der Signatur, damit der Aufrufer nicht raten muss, welcher
      Bezug zu welchem Bereich gehoert, und beide Bezuege an derselben Stelle
      uebergibt.
    - ``team`` haengt am **Gruender**, nicht am schreibenden Mitglied. Andernfalls
      haette das schwaechste Mitglied das Sagen ueber den Vorrat des Teams: ein
      Kunde mit knappem Tarif koennte im Team eines Grosskunden nichts mehr
      merken, und sein blosser Beitritt wuerde das Limit eines fremden Teams
      senken, sobald er der naechste Schreiber ist. Das Team gehoert seinem
      Gruender, also gehoert ihm auch der Vorrat — und der bleibt stabil, egal
      wer gerade schreibt. ``teams.owner_user_id`` ist NOT NULL und
      ondelete=RESTRICT: solange das Team existiert, existiert auch der Gruender.
    - ``server_shared`` und ``panel`` haengen an gar keiner Rolle, siehe
      ``MAX_SYSTEM_SCOPE_ENTRIES``.

    Ein unbekannter Bereich sowie ein Team, das sich nicht aufloesen laesst,
    fallen auf dieselbe feste Systemgrenze zurueck: ein Tippfehler im
    Bereichsnamen oder eine ins Leere zeigende ``team_id`` darf den Vorrat
    weder oeffnen noch sperren.
    """
    if scope in ("user", "server"):
        return resolve_effective_limits(db, user).max_memory_entries
    if scope == "team":
        team = db.get(Team, team_id) if team_id is not None else None
        founder = db.get(User, team.owner_user_id) if team is not None else None
        if founder is not None:
            return resolve_effective_limits(db, founder).max_memory_entries
    return MAX_SYSTEM_SCOPE_ENTRIES
