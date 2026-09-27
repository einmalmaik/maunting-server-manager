"""Verschluesselte Spalten werden gebuendelt entschluesselt, nicht Zeile fuer Zeile.

Bis 27.09.2026 kostete jeder verschluesselte Wert einen eigenen Aufruf beim
Sidecar, nacheinander. Die Chatseite (200 Nachrichten, vier verschluesselte
Spalten) waren bis zu 800 Aufrufe, und der Chat fragt sie alle 20 Sekunden.
Gezaehlt wird hier, wie oft ``DisClient.decrypt`` einzeln gerufen wird.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import AiConversation, AiMessage, User
from models import dis_text
from models.dis_text import vorab_entschluesselt
from routers.ai_chat import _verlauf_seite
from services.dis_client import DisClient, DisSidecarError


def _gespraech_mit(db: Session, user: User, anzahl: int) -> AiConversation:
    gespraech = AiConversation(id=str(uuid4()), user_id=user.id, title="Titel")
    db.add(gespraech)
    beginn = datetime.now(timezone.utc) - timedelta(minutes=anzahl)
    for i in range(anzahl):
        db.add(
            AiMessage(
                id=str(uuid4()),
                conversation_id=gespraech.id,
                role="assistant",
                content=f"Antwort {i}",
                reasoning=f"Gedanke {i}",
                sections_json="[]",
                created_at=beginn + timedelta(minutes=i),
            )
        )
    db.commit()
    db.expire_all()
    return gespraech


@pytest.fixture
def einzeln_gezaehlt(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    aufrufe: list[str] = []
    echt = DisClient.decrypt

    def zaehle(ciphertext: str, aad: str | None = None) -> str:
        aufrufe.append(ciphertext)
        return echt(ciphertext, aad)

    monkeypatch.setattr(DisClient, "decrypt", staticmethod(zaehle))
    return aufrufe


def test_chatseite_entschluesselt_ohne_einzelaufrufe(
    db: Session, regular_user: User, einzeln_gezaehlt: list[str]
) -> None:
    gespraech = _gespraech_mit(db, regular_user, 12)
    # Der Titel des Gespraechs liegt schon im Speicher, bevor die Seite laedt.
    gespraech = db.get(AiConversation, gespraech.id)
    einzeln_gezaehlt.clear()

    seite = _verlauf_seite(db, gespraech, None)

    assert [m.content for m in seite.messages] == [f"Antwort {i}" for i in range(12)]
    assert einzeln_gezaehlt == []


def test_faellt_auf_den_einzelweg_zurueck_wenn_der_stapel_scheitert(
    db: Session, regular_user: User, einzeln_gezaehlt: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ein Stapel, der scheitert, darf keinen Wert verlieren: dann laeuft es
    # wie vorher, einzeln und mit denselben Fehlern.
    gespraech = _gespraech_mit(db, regular_user, 3)

    def scheitert(_items):
        raise DisSidecarError("Sidecar weg")

    monkeypatch.setattr(DisClient, "decrypt_many", staticmethod(scheitert))
    abfrage = db.query(AiMessage).filter(AiMessage.conversation_id == gespraech.id)
    with vorab_entschluesselt(db, abfrage, AiMessage.content):
        zeilen = abfrage.all()

    assert sorted(z.content for z in zeilen) == ["Antwort 0", "Antwort 1", "Antwort 2"]
    assert len(einzeln_gezaehlt) >= 3


def test_klartexte_leben_nur_im_block(db: Session, regular_user: User) -> None:
    gespraech = _gespraech_mit(db, regular_user, 2)
    abfrage = db.query(AiMessage).filter(AiMessage.conversation_id == gespraech.id)

    with vorab_entschluesselt(db, abfrage, AiMessage.content):
        assert dis_text._vorab.get()

    assert dis_text._vorab.get() is None


def test_nimmt_nur_verschluesselte_spalten(db: Session) -> None:
    with pytest.raises(TypeError):
        with vorab_entschluesselt(db, db.query(AiMessage), AiMessage.role):
            pass
