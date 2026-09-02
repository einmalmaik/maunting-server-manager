"""DSGVO-Zusage der Kontolöschung für KI-Daten — als Test festgeschrieben.

Die Löschung läuft über `ON DELETE CASCADE` der Datenbank, nicht über
Anwendungscode (services/user_deletion_service.py räumt nur RESTRICT-Blocker).
Genau deshalb braucht es diesen Test: niemand liest die Kaskade im Code, und
SQLite prüft Fremdschlüssel nur mit scharfgestelltem Pragma (conftest.py).

Was **bewusst nicht** gelöscht wird, sichern andere Tests als Invariante ab:
Team-, Server-Shared- und Panel-Memories überleben ihren Verfasser
(test_ai_memory_isolation.py, test_ai_memory_server_shared.py), und AiSkill
hat keinen Benutzer-Scope. Betreiber-Entscheidung vom 21.08.2026: teambasiertes
Wissen gehört dem Team, nicht dem gelöschten Konto.
"""

import uuid

from sqlalchemy.orm import Session

from models.ai_conversation import AiConversation, AiMessage
from models.ai_memory import AiMemoryEntry, AiMemoryPreference
from models import User
from services.auth_service import AuthService


def _ai_daten_anlegen(db: Session, user: User) -> tuple[str, str]:
    """Legt Konversation, Nachricht, Memory-Eintrag und -Präferenz an."""
    conv_id = str(uuid.uuid4())
    db.add(AiConversation(id=conv_id, user_id=user.id, title="Testgespräch", kind="primary"))
    msg_id = str(uuid.uuid4())
    db.add(AiMessage(id=msg_id, conversation_id=conv_id, role="user", content="hallo"))
    db.add(AiMemoryPreference(user_id=user.id, enabled=True))
    db.add(AiMemoryEntry(
        id=str(uuid.uuid4()),
        owner_user_id=user.id,
        scope="user",
        scope_identity=f"user:{user.id}",
        key="lieblingsserver",
        value_encrypted="test-enc-v1::74657374",
        origin="user",
    ))
    db.commit()
    return conv_id, msg_id


class TestKontoloeschungRaeumtKiDaten:
    def test_selbstloeschung_entfernt_persoenliche_ki_daten(self, db: Session, regular_user: User):
        conv_id, msg_id = _ai_daten_anlegen(db, regular_user)
        user_id = regular_user.id

        AuthService.delete_account_atomically(db, regular_user)
        db.expire_all()

        assert db.query(AiConversation).filter(AiConversation.user_id == user_id).count() == 0
        assert db.query(AiMessage).filter(AiMessage.conversation_id == conv_id).count() == 0
        assert db.query(AiMemoryEntry).filter(AiMemoryEntry.owner_user_id == user_id).count() == 0
        assert db.query(AiMemoryPreference).filter(AiMemoryPreference.user_id == user_id).count() == 0

    def test_fremde_ki_daten_bleiben_unberuehrt(self, db: Session, regular_user: User, owner_user: User):
        # Die Kaskade darf nur die Daten des gelöschten Kontos treffen.
        eigene_conv, _ = _ai_daten_anlegen(db, regular_user)
        fremde_conv, fremde_msg = _ai_daten_anlegen(db, owner_user)

        AuthService.delete_account_atomically(db, regular_user)
        db.expire_all()

        assert db.query(AiConversation).filter(AiConversation.id == fremde_conv).count() == 1
        assert db.query(AiMessage).filter(AiMessage.id == fremde_msg).count() == 1
        assert db.query(AiMemoryEntry).filter(AiMemoryEntry.owner_user_id == owner_user.id).count() == 1
        assert db.query(AiConversation).filter(AiConversation.id == eigene_conv).count() == 0
