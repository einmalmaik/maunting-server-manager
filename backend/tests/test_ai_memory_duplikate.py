"""Die Duplikatpruefung: erkennt sie denselben Fakt unter neuem Namen?

Der Bestand am 19.08.2026 zeigte das Problem im Kleinen: `reply_style` und
`antwortstil.ausfuehrlich` sagen beide, dass der Betreiber knappe Antworten
mag — zwei Schluessel, ein Fakt. Konflikte loest das Gedaechtnis ueber den
Schluessel; wird der nicht wiedergefunden, entsteht ein Doppel.

Diese Tests brauchen das Embeddingmodell. Ohne Modell gibt
`aehnlicher_eintrag` bewusst ``None`` zurueck (raten waere schlimmer als
nichts tun) — die Tests ueberspringen sich dann selbst.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from models import User
from services import ai_embedding_service
from services import ai_memory_service as mem

pytestmark = pytest.mark.skipif(
    not ai_embedding_service.is_available(),
    reason="Embeddingmodell nicht vorhanden — Bedeutungsvergleich nicht moeglich",
)


def _benutzer(db: Session, name: str) -> User:
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


def test_the_same_fact_under_a_new_key_is_found(db: Session) -> None:
    """**Der Kern.** Zwei Namen, ein Sachverhalt.

    Genau der Fall, der im echten Bestand steht: `reply_style` und
    `antwortstil.ausfuehrlich`.
    """
    user = _benutzer(db, "dublette")
    mem.upsert_entry(
        db, user=user, scope="user", server_id=None,
        key="reply_style",
        value="Der Nutzer bevorzugt kurze, knappe Antworten ohne Einleitung.",
    )

    treffer = mem.aehnlicher_eintrag(
        db,
        scope_kennung=f"user:{user.id}",
        key="antwortstil",
        value="Der Nutzer moechte knappe Antworten, keine langen Einleitungen.",
    )

    assert treffer is not None, "das Doppel haette auffallen muessen"
    vorhanden, wert = treffer
    assert vorhanden.key == "reply_style"
    assert wert >= mem.DUPLIKAT_AB


def test_a_genuinely_different_fact_passes(db: Session) -> None:
    """Und die Gegenprobe — sonst waere die Pruefung wertlos.

    Ein faelschlich zusammengelegter Fakt ist teurer als ein doppelter:
    er vernichtet Information, statt sie zu verdoppeln.
    """
    user = _benutzer(db, "verschieden")
    mem.upsert_entry(
        db, user=user, scope="user", server_id=None,
        key="ram.vorgabe", value="Fuer neue Server immer 8 GB Arbeitsspeicher.",
    )

    treffer = mem.aehnlicher_eintrag(
        db,
        scope_kennung=f"user:{user.id}",
        key="arbeitszeit",
        value="Der Betreiber arbeitet meist nachts.",
    )

    assert treffer is None, "zwei verschiedene Fakten duerfen nicht verschmelzen"


def test_the_same_key_is_not_its_own_duplicate(db: Session) -> None:
    """Ein Eintrag ist nie sein eigenes Doppel.

    Sonst blockierte die Pruefung genau den Weg, den sie empfiehlt: das
    Aktualisieren unter demselben Schluessel.
    """
    user = _benutzer(db, "selbst")
    mem.upsert_entry(
        db, user=user, scope="user", server_id=None,
        key="ram.vorgabe", value="Fuer neue Server immer 8 GB Arbeitsspeicher.",
    )

    treffer = mem.aehnlicher_eintrag(
        db,
        scope_kennung=f"user:{user.id}",
        key="ram.vorgabe",
        value="Fuer neue Server immer 16 GB Arbeitsspeicher.",
    )

    assert treffer is None


def test_the_check_never_looks_across_scopes(db: Session) -> None:
    """**Sicherheitsinvariante.** Die Pruefung ist kein Leseweg.

    Ein persoenlicher Eintrag darf nie gegen einen fremden geprueft werden —
    sonst verriete schon das Vorhandensein eines Treffers, dass jemand
    anderes etwas Aehnliches notiert hat.
    """
    einer = _benutzer(db, "einer")
    anderer = _benutzer(db, "anderer")
    mem.upsert_entry(
        db, user=einer, scope="user", server_id=None,
        key="reply_style", value="Bevorzugt kurze, knappe Antworten.",
    )

    treffer = mem.aehnlicher_eintrag(
        db,
        scope_kennung=f"user:{anderer.id}",
        key="antwortstil",
        value="Bevorzugt kurze, knappe Antworten.",
    )

    assert treffer is None, "der Bereich des anderen darf nicht sichtbar sein"


def test_an_entry_without_a_vector_is_skipped(db: Session) -> None:
    """Bestandsdaten ohne Vektor blockieren nichts.

    Wer vor der Modellinstallation gemerkt wurde, hat keinen Vektor. Die
    Pruefung ueberspringt ihn, statt ihn als "unaehnlich" zu werten — er
    faellt damit nicht faelschlich durch, sondern gar nicht auf.
    """
    user = _benutzer(db, "ohnevektor")
    row, _ = mem.upsert_entry(
        db, user=user, scope="user", server_id=None,
        key="reply_style", value="Bevorzugt kurze, knappe Antworten.",
    )
    row.embedding_json = None
    row.embedding_model = None
    db.flush()

    treffer = mem.aehnlicher_eintrag(
        db,
        scope_kennung=f"user:{user.id}",
        key="antwortstil",
        value="Bevorzugt kurze, knappe Antworten.",
    )

    assert treffer is None
