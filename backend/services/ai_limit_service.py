"""Auflösung und Persistenz rollenbasierter KI-Limits.

Die Regeln sind absichtlich klein und deterministisch:
- hat *keine* Rolle des Benutzers eine Konfiguration, gilt „unbegrenzt“,
- unter den konfigurierten Rollen gewinnt der höchste Wert.

Die zweite Regel ist die einzige, die für *jedes* Feld gilt. Was ein leeres Feld
wert ist, hängt dagegen am Feld: bei den Kontingenten ist ``None`` selbst ein
Wert, nämlich „unbegrenzt“ und damit der höchste — deshalb gewinnt er. Beim
Memory-Vorrat ist ``None`` gar kein Wert, sondern eine Abwesenheit, und eine
Abwesenheit trägt nichts bei. Hier stand früher „ein explizites ``None`` gewinnt
als unbegrenzt“ als dritte Regel; das galt, solange jedes Feld ein Kontingent
war. Welche Felder wie gelesen werden, steht bei ``FELDER_OHNE_UNBEGRENZT``.

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
# Hoechster Rang aus `ai_reasoning.RANGFOLGE` (minimal..max). Bewusst als Zahl
# hier statt als Import: dieses Modul soll nicht von der Denklogik abhaengen,
# und `test_ai_reasoning_limits.py` sichert zu, dass beide Werte gleich bleiben.
MAX_REASONING_EFFORT_MAX = 6
# Deckel für das konfigurierbare Rollenlimit. Er begrenzt **einen Bereich**,
# nicht eine Anfrage — und er ist eine Obergrenze für das, was der Betreiber
# überhaupt einstellen darf, nicht eine Zahl, die irgendwo von selbst gilt. Wer
# nichts hinterlegt, bleibt bei `MAX_SYSTEM_SCOPE_ENTRIES`; das Anheben dieser
# Zahl verändert deshalb keine bestehende Installation, es macht nur mehr
# einstellbar. Hier stand vorher, 1_000 statt 10_000 verhindere eine Selbst-DoS
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
# Teuer sind davon aber nur die Zeilen, die auch entschlüsselt werden, und die
# sind gedeckelt: `provider_memory_context` kürzt die geladene Menge in
# `_vorauswahl` auf `MAX_CONTEXT_ROWS`, **bevor** `_entschluesseln` sie beim
# DIS-Sidecar öffnet — seit dem 19.08.2026 zu acht gleichzeitig statt Zeile für
# Zeile, was die Wartezeit teilt, aber nichts an der Zahl der Roundtrips
# ändert: gedeckelt bleibt sie durch `MAX_CONTEXT_ROWS`. Bewerten kann `_vorauswahl`
# ohne Klartext, weil Vektor, Nutzung, Aktualität und der Schlüssel
# unverschlüsselt an der Zeile stehen. Eine Chatanfrage kostet damit so viele
# Roundtrips und nicht „Bereiche × Deckel". Die Zahl steht bewusst nicht hier:
# sie gehört zum Kontextaufbau und wird dort begründet. Nachzulesen statt zu
# glauben ist das in `test_eine_anfrage_entschluesselt_nie_mehr_als_der_deckel_erlaubt`
# (backend/tests/test_ai_memory_recall.py).
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
# wäre keine dieser beiden.
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
# Dieselbe Zahl ist zugleich der Rueckfall fuer die rollengebundenen Bereiche,
# solange der Betreiber dort nichts hinterlegt hat — es ist genau die Grenze,
# die vorher fest im Memory-Service stand, siehe `resolve_scope_memory_limit`.
MAX_SYSTEM_SCOPE_ENTRIES = 100

LIMIT_FIELDS = (
    "daily_token_limit",
    "weekly_token_limit",
    "monthly_token_limit",
    "requests_per_minute",
    "concurrent_operations",
    "monthly_cost_limit_cents",
    # Kein Kontingent, sondern eine Obergrenze fuer die Denktiefe — passt aber
    # in genau dieselbe Aufloesung: "None heisst unbegrenzt", "der hoechste
    # Wert der konfigurierten Rollen gewinnt", "keine Rolle konfiguriert heisst
    # unbegrenzt". Eine zweite Aufloesung daneben waere eine zweite Wahrheit.
    "max_reasoning_effort",
    # Auch kein Kontingent, sondern ein Vorrat: wieviele Memory-Eintraege je
    # Bereich bestehen duerfen. Steht hier, weil ein Tarif den Vorrat ueber die
    # Rolle verkaufen koennen soll, statt dass eine Konstante im Memory-Service
    # fuer alle entscheidet. Von den Regeln oben passt die tragende — "der
    # hoechste Wert der konfigurierten Rollen gewinnt" — unveraendert; nur
    # "None heisst unbegrenzt" passt nicht, siehe FELDER_OHNE_UNBEGRENZT.
    "max_memory_entries",
)
# Felder, in denen ein leeres Feld *kein* Wert ist. Ueberall sonst ist ``None``
# der groesste denkbare Wert („unbegrenzt“) und gewinnt deshalb ueber jede Zahl.
# Beim Memory-Vorrat ist es umgekehrt: ``resolve_scope_memory_limit`` loest
# ``None`` zu MAX_SYSTEM_SCOPE_ENTRIES auf, und diese 100 ist der *kleinste*
# sinnvolle Ausgang, nicht der groesste. Liesse man ``None`` dort gewinnen, kaeme
# genau das Gegenteil der Zusage heraus, unter der dieses Feld gebaut wurde:
#   - ein VIP mit 800 fiele auf 100, sobald er zusaetzlich eine Bestandsrolle
#     bekommt, deren Feld die Migration auf NULL gesetzt hat — eine weitere
#     Rolle wuerde ihm also etwas wegnehmen, statt zu erhoehen;
#   - eine ausdrueckliche 0 („diese Rolle darf sich nichts merken“) verloere
#     gegen jede beliebige leere Rolle, und die Sperre waere wirkungslos.
# Ein leeres Feld heisst hier deshalb „diese Rolle sagt zum Vorrat nichts“ und
# traegt genau soviel bei wie eine Rolle ganz ohne Zeile: nichts. Sagen alle
# Rollen nichts, bleibt es bei ``None`` — eine Zahl macht daraus erst
# ``resolve_scope_memory_limit``.
# Bewusst eine benannte Menge und kein Feldname mitten in ``_resolve_field``:
# ein kuenftiges Feld soll sich hier ausdruecklich einordnen muessen, statt die
# Lesart des Nachbarn stillschweigend zu erben.
FELDER_OHNE_UNBEGRENZT = frozenset({"max_memory_entries"})
LIMIT_MAXIMA = {
    "daily_token_limit": TOKEN_LIMIT_MAX,
    "weekly_token_limit": TOKEN_LIMIT_MAX,
    "monthly_token_limit": TOKEN_LIMIT_MAX,
    "requests_per_minute": REQUESTS_PER_MINUTE_MAX,
    "concurrent_operations": CONCURRENT_OPERATIONS_MAX,
    "monthly_cost_limit_cents": MONTHLY_COST_LIMIT_CENTS_MAX,
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
    #: Hoechste erlaubte Denkstufe als Rang; ``None`` heisst unbegrenzt.
    max_reasoning_effort: int | None
    #: Rohe Rollenaufloesung des Memory-Vorrats je Bereich. ``None`` heisst hier
    #: nicht „unbegrenzt“ wie bei den Feldern darueber, sondern „der Betreiber
    #: hat nichts hinterlegt“ — und zwar in *keiner* Rolle des Benutzers; eine
    #: einzelne leere Rolle neben einer gesetzten ergibt kein ``None``, siehe
    #: ``FELDER_OHNE_UNBEGRENZT``. Es ist damit genau das leere Feld, das der
    #: Betreiber in den Einstellungen sieht, und ebenfalls anders als oben
    #: *nicht* die Zahl, die beim Merken gilt: die macht erst
    #: ``resolve_scope_memory_limit`` daraus. Wer hier vergleicht statt dort,
    #: laesst den Vorrat unbegrenzt.
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

    Aufgelöst wird für alle Felder gleich: der höchste Wert gewinnt.
    Unterschiedlich ist allein, ob ein leeres Feld überhaupt ein Wert ist. Für
    die Felder aus ``FELDER_OHNE_UNBEGRENZT`` ist es keiner, deshalb wird dort
    über die *gesetzten* Werte maximiert statt beim ersten ``None``
    auszusteigen — sonst nähme eine zusätzliche leere Rolle einer Rolle mit Zahl
    ihren Vorrat weg. Sind dort alle Felder leer, bleibt es bei ``None``: „keine
    der Rollen hat etwas hinterlegt“ ist weiterhin eine eigene Aussage und nicht
    heimlich schon die Zahl, die später beim Merken gilt.
    """
    configured = [getattr(row, field) for row in rows]
    if field not in FELDER_OHNE_UNBEGRENZT and any(value is None for value in configured):
        return None
    gesetzt = [int(value) for value in configured if value is not None]
    return max(gesetzt) if gesetzt else None


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
) -> int:
    """Wieviele Memory-Eintraege in *diesem* Bereich stehen duerfen.

    Der Rueckgabewert ist immer eine Zahl; ein „unbegrenzt“ gibt es hier nicht.
    Sagt keine einzige Rolle des Benutzers etwas zum Vorrat — die
    Rollenaufloesung also ``None`` —, gilt ``MAX_SYSTEM_SCOPE_ENTRIES``.

    ``max_memory_entries`` ist damit das einzige Feld des Moduls, bei dem ein
    leeres Feld **nicht** „unbegrenzt“ heisst; genau deshalb steht es in
    ``FELDER_OHNE_UNBEGRENZT``. Das hat einen Grund. Bei den Kontingenten kostet
    „unbegrenzt“ Geld, und ob er das ausgeben will, ist die Entscheidung des
    Betreibers — ein nicht gesetztes Tokenlimit schadet niemandem ausser seiner
    eigenen Rechnung. Ein Memory-Eintrag kostet dagegen Latenz in **jeder**
    Anfrage: auch in denen, die mit ihm gar nichts zu tun haben, und bei
    fremden Benutzern, sobald er in einem geteilten Bereich steht. „Der
    Betreiber hat nichts hinterlegt“ kann deshalb nicht „gar keine Grenze“
    heissen; er wuerde eine Politik bezahlen, die er nie gewaehlt hat.

    Vorher stand diese Grenze als feste 100 im Memory-Service. Sie verschwindet
    nicht, sie wird verschiebbar: 100 ist ab jetzt der Ausgangswert, den der
    Betreiber bis ``MAX_MEMORY_ENTRIES_MAX`` anheben oder bis auf 0 senken
    kann. Solange er nichts setzt, aendert sich fuer niemanden etwas.

    ``EffectiveAiLimits.max_memory_entries`` behaelt dabei seine alte
    Bedeutung: das ist die rohe Rollenaufloesung fuer die Einstellungsmaske,
    ``None`` heisst dort „nichts hinterlegt“. Erst hier wird daraus die Zahl,
    die beim Merken gilt — die beiden sind nicht dasselbe.

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
        rollenlimit = resolve_effective_limits(db, user).max_memory_entries
        # Ausdruecklich gegen ``None`` und nicht ueber ``x or FALLBACK``: ein
        # ``or`` verschluckt auch die 0 und macht aus dem hinterlegten „diese
        # Rolle darf sich gar nichts merken“ stillschweigend die 100 — das
        # genaue Gegenteil dessen, was der Betreiber eingetragen hat. Diese
        # Pruefung lief eine Fassung lang ins Leere, weil die 0 hier gar nicht
        # mehr ankam: sie verlor in ``_resolve_field`` gegen jede leere
        # Nachbarrolle. Beide Haelften gehoeren zusammen.
        return MAX_SYSTEM_SCOPE_ENTRIES if rollenlimit is None else rollenlimit
    if scope == "team":
        team = db.get(Team, team_id) if team_id is not None else None
        founder = db.get(User, team.owner_user_id) if team is not None else None
        if founder is not None:
            # Dieselbe ausdrueckliche Pruefung wie oben, und aus demselben
            # Grund: eine 0 des Gruenders ist eine Ansage, kein fehlender Wert.
            rollenlimit = resolve_effective_limits(db, founder).max_memory_entries
            return MAX_SYSTEM_SCOPE_ENTRIES if rollenlimit is None else rollenlimit
    return MAX_SYSTEM_SCOPE_ENTRIES
