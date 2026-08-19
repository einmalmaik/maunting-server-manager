"""Das Gedaechtnis vergisst nicht, es verblasst.

Der Betreiber am 19.08.2026:

    "Ältere Einträge [gehen] irgendwann in den Hintergrund, die werden nicht
    mehr so oft aufgerufen, aber wenn jetzt ein Trigger kommt, dann werden die
    aufgerufen. Und das gilt nicht nur für ältere Einträge, sondern auch
    Einträge, die nicht so oft genutzt werden. […] Die Einträge werden nicht
    einfach gelöscht."

Vorgefunden wurde ein **binaeres** System: bis zur Budgetgrenze kam alles
gleich stark mit, erst darueber wurde ausgewaehlt. Gemessen lag die
Auslastung bei 14,6 % (874 von 6.000 Zeichen) — es haette rund 41 Eintraege
gebraucht, bevor ueberhaupt etwas bewertet worden waere. Bis dahin stand der
Eintrag von gestern gleichberechtigt neben dem von vor drei Monaten.

Diese Tests pruefen das **Verhalten**, nicht die Formel: dass ein alter
Eintrag leiser wird, dass ein Reiz ihn zurueckholt, und vor allem, dass er
dabei nie verschwindet.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session

from models import User
from services import ai_memory_service as mem


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


class _Zeile:
    """Eine Gedaechtniszeile, so wie die Bewertung sie sieht.

    Bewusst kein echtes ORM-Objekt: `abrufstaerke` liest genau vier Felder,
    und ein Stellvertreter macht sichtbar, welche das sind. Ein Test, der
    dafuer eine Datenbank braucht, prueft die Datenbank mit.
    """

    def __init__(
        self,
        *,
        use_count: int = 0,
        tage_her: float = 0.0,
        key: str = "notiz",
        scope: str = "user",
    ) -> None:
        self.use_count = use_count
        self.last_used_at = _jetzt() - timedelta(days=tage_her)
        self.updated_at = self.last_used_at
        self.created_at = self.last_used_at
        self.key = key
        self.scope = scope
        self.server_id = None
        self.team_id = None
        self.origin = "user"


def _benutzer(db: Session, name: str) -> User:
    """Ein Benutzer mit eingeschaltetem Gedaechtnis."""
    user = User(
        username=name,
        email_encrypted="x",
        email_hash=name,
        password_hash="x",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    mem.set_preference(db, user, True)
    return user


# ── Was verblassen laesst ─────────────────────────────────────────────


def test_a_fresh_entry_is_fully_present() -> None:
    """Frisch Gemerktes ist da, auch ohne jede Nutzung.

    Ohne den Neuheitsschutz waere ein neuer Eintrag sofort blass: er hat noch
    keine Nutzung, und Vertrautheit ist der staerkste Anteil der Ruheformel.
    """
    staerke = mem.abrufstaerke(_Zeile(use_count=0, tage_her=0.0), _jetzt())

    assert staerke >= mem.VERBLASSEN_AB, (
        "ein gerade gemerkter Eintrag darf nicht sofort verblassen"
    )


def test_an_old_unused_entry_fades() -> None:
    """Lange nicht gebraucht heisst leiser — aber nicht weg."""
    staerke = mem.abrufstaerke(_Zeile(use_count=0, tage_her=90.0), _jetzt())

    assert staerke < mem.VERBLASSEN_AB
    assert staerke > 0.0, "verblasst ist nicht geloescht"


def test_a_young_but_unused_entry_fades_too() -> None:
    """**Alter allein ist nicht das Kriterium.**

    Genau die Unterscheidung, auf der der Betreiber bestanden hat: auch ein
    Eintrag, den nie jemand gebraucht hat, gehoert nach hinten — sonst
    verdraengt blosse Menge das Wichtige.
    """
    jung_ungenutzt = mem.abrufstaerke(_Zeile(use_count=0, tage_her=21.0), _jetzt())
    alt_genutzt = mem.abrufstaerke(_Zeile(use_count=18, tage_her=200.0), _jetzt())

    assert jung_ungenutzt < alt_genutzt, (
        "Nutzung muss schwerer wiegen als das blosse Datum"
    )


def test_a_much_used_entry_stays_present_for_years() -> None:
    """Was staendig gebraucht wird, altert nicht.

    Beim Menschen dasselbe: die eigene Telefonnummer faellt einem ein, ohne
    dass jemand danach fragt.
    """
    staerke = mem.abrufstaerke(_Zeile(use_count=20, tage_her=400.0), _jetzt())

    assert staerke >= mem.VERBLASSEN_AB


# ── Der Reiz holt zurueck ─────────────────────────────────────────────


def test_a_matching_question_brings_a_faded_entry_straight_back() -> None:
    """**Der Kern der Sache.** Ein Treffer sticht Alter und Nutzung.

    Ein Eintrag, der drei Monate geschlafen hat, ist bei der passenden Frage
    sofort wieder vollstaendig da — ohne Zwischenschritt, in derselben Runde.
    """
    alt = _Zeile(use_count=0, tage_her=90.0)

    ohne_reiz = mem.abrufstaerke(alt, _jetzt())
    mit_bedeutung = mem.abrufstaerke(alt, _jetzt(), similarity=0.82)
    mit_wortbezug = mem.abrufstaerke(alt, _jetzt(), overlap=2)

    assert ohne_reiz < mem.VERBLASSEN_AB
    assert mit_bedeutung >= mem.VERBLASSEN_AB
    assert mit_wortbezug >= mem.VERBLASSEN_AB


def test_an_unrelated_question_does_not_wake_anything() -> None:
    """Negative Aehnlichkeit heisst "hat nichts miteinander zu tun".

    Sie darf einen Eintrag nicht unter einen ohne Vektor druecken — und ihn
    erst recht nicht wecken.
    """
    alt = _Zeile(use_count=0, tage_her=90.0)

    assert mem.abrufstaerke(alt, _jetzt(), similarity=-0.4) < mem.VERBLASSEN_AB


def test_the_stimulus_is_a_floor_not_a_summand() -> None:
    """Der Reiz steht **neben** der Ruheformel, nicht darin.

    In einer Summe koennte ein blasser Eintrag trotz perfektem Treffer unter
    der Schwelle bleiben, weil ihm Nutzung und Frische fehlen — und genau
    dieser Fall ist der, um den es geht.
    """
    ganz_blass = _Zeile(use_count=0, tage_her=365.0)

    assert mem.abrufstaerke(ganz_blass, _jetzt(), similarity=0.95) >= 0.95


# ── Wie es im Block aussieht ──────────────────────────────────────────


def test_a_faded_entry_is_shortened_but_still_findable() -> None:
    """Verblasst heisst kuerzer, nicht verschwunden.

    Der Schluessel bleibt vollstaendig: das Modell soll wissen, *dass* es die
    Notiz gibt, und mit `search_memory` nachfassen koennen. Genau der Weg,
    den ein Mensch nimmt, wenn ihm etwas "auf der Zunge liegt".
    """
    zeile = _Zeile(key="ark.startzeit")
    lang = (
        "Der Server braucht nach einem Neustart etwa vier Minuten, bis die "
        "Spielerliste vollstaendig geladen ist, und meldet vorher falsche "
        "Spielerzahlen an die Serverliste."
    )

    voll = mem._memory_line(zeile, lang, 0.9)
    blass = mem._memory_line(zeile, lang, 0.1)

    assert lang in voll
    assert len(blass) < len(voll)
    assert "ark.startzeit" in blass, "der Schluessel muss lesbar bleiben"
    assert "…" in blass, "die Kuerzung muss sichtbar sein"
    assert "blass" in blass, "das Modell soll den Zustand erkennen koennen"


def test_a_short_entry_is_never_cut() -> None:
    """Unterhalb der Kuerzungsgrenze bleibt alles, wie es ist.

    Eine Kuerzung, die nichts spart, wuerde nur Zeichen kosten (das Etikett)
    und Information vernichten.
    """
    zeile = _Zeile(key="ram")
    kurz = "immer 8 GB"

    assert kurz in mem._memory_line(zeile, kurz, 0.05)


def test_without_a_strength_nothing_changes() -> None:
    """Die alte Signatur bleibt gueltig.

    `_memory_line` wird auch aus Pfaden gerufen, die keine Staerke kennen —
    dort darf sich das Verhalten nicht aendern.
    """
    zeile = _Zeile(key="notiz")
    text = "x" * 200

    assert text in mem._memory_line(zeile, text)


# ── Am echten Kontextaufbau ───────────────────────────────────────────


@pytest.mark.usefixtures("db")
def test_the_whole_block_keeps_every_entry(db: Session) -> None:
    """**Der Integrationstest: nichts geht verloren.**

    Ein alter und ein frischer Eintrag, beide im Block — der alte verkuerzt,
    der frische vollstaendig. Das ist der Unterschied zu einem System, das
    aufraeumt: hier bleibt jede Zeile sichtbar.
    """
    user = _benutzer(db, "verblassen")

    mem.upsert_entry(
        db, user=user, scope="user", server_id=None,
        key="frisch", value="Der Betreiber arbeitet abends nach der Arbeit.",
    )
    alt, _ = mem.upsert_entry(
        db, user=user, scope="user", server_id=None,
        key="alt",
        value=(
            "Vor Monaten festgehalten: die Zeitzone der Anlage steht auf "
            "Europe/Berlin und wird von den Spielservern uebernommen."
        ),
    )
    # Den alten Eintrag altern lassen, ohne ihn anzufassen.
    alt.last_used_at = _jetzt() - timedelta(days=120)
    alt.updated_at = alt.last_used_at
    alt.created_at = alt.last_used_at
    alt.use_count = 0
    db.flush()

    block = mem.provider_memory_context(db, user, query="", server_id=None)

    assert block is not None
    # Beide sind da — das ist die Zusage.
    assert "frisch" in block and "alt" in block
    # Aber nicht gleich stark.
    assert "blass" in block, "der alte Eintrag haette verblassen muessen"
    zeilen = {z.split("]")[1].split(":")[0].strip(): z for z in block.splitlines() if "]" in z}
    assert "blass" not in zeilen["frisch"]
    assert "blass" in zeilen["alt"]


@pytest.mark.usefixtures("db")
def test_a_matching_query_restores_the_faded_entry(db: Session) -> None:
    """Und der Weg zurueck: dieselbe Lage, nur mit passender Frage.

    Ohne diesen Test waere "verblassen" von "verlieren" nicht zu
    unterscheiden.
    """
    user = _benutzer(db, "reiz")

    alt, _ = mem.upsert_entry(
        db, user=user, scope="user", server_id=None,
        key="zeitzone",
        value=(
            "Die Zeitzone der Anlage steht auf Europe/Berlin und wird von den "
            "Spielservern uebernommen."
        ),
    )
    alt.last_used_at = _jetzt() - timedelta(days=120)
    alt.updated_at = alt.last_used_at
    alt.created_at = alt.last_used_at
    alt.use_count = 0
    db.flush()

    ohne = mem.provider_memory_context(db, user, query="", server_id=None)
    assert ohne is not None and "blass" in ohne

    # Zuruecksetzen, was der erste Aufruf an Nutzung vermerkt hat: geprueft
    # werden soll der Reiz, nicht der Nebeneffekt des vorigen Aufrufs.
    alt.last_used_at = _jetzt() - timedelta(days=120)
    alt.use_count = 0
    db.flush()

    mit = mem.provider_memory_context(db, user, query="zeitzone", server_id=None)

    assert mit is not None
    assert "Europe/Berlin" in mit, "der Reiz haette den Eintrag zurueckholen muessen"
    assert "blass" not in mit
