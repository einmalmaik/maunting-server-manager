"""Eine Wahrheit darüber, welcher Zugang einen Chatlauf tragen kann.

Die Regel „spricht ``/chat/completions`` **und** hat ein Standardmodell" stand
an sechs Stellen ausgeschrieben — in der Providerauswahl, im Chat-Router, im
Sprach-Router und zweimal im Service selbst. Sie war dabei schon
auseinandergelaufen. Seit `ai_provider_service.fuer_chat()` gibt es eine
Antwort; diese Tests halten fest, dass alle drei Wege dieselbe geben.

Ehrlich dazu gesagt: kein Test kann erzwingen, dass es bei **einer** Kopie
bleibt. Was er kann, ist die Folge einer Drift sichtbar machen — ein Zugang,
den die Auswahl anbietet und das Senden mit 404 abweist, oder umgekehrt.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiProvider, Role, RolePermission, User
from routers import ai_chat, ai_voice
from services import ai_provider_service
from services.ai_limit_service import LIMIT_FIELDS, set_role_limit
from services.role_service import set_user_roles


def _enable_chat(db: Session, user: User) -> None:
    role = Role(name=f"ai-chat-{user.id}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.chat.use"))
    set_role_limit(db, role.id, {field: None for field in LIMIT_FIELDS})
    db.commit()
    set_user_roles(db, user, [role.id])


def _zugang(db: Session, **felder) -> AiProvider:
    """Ein Zugang mit Vorgaben, die jeder Test einzeln überschreiben kann."""
    vorgabe = dict(
        name=f"Zugang {uuid4().hex[:6]}",
        provider_kind="openrouter",
        default_model="test-modell",
        enabled=True,
        requires_api_key=True,
        operator_api_key="sk-or-v1-operator-secret",
    )
    vorgabe.update(felder)
    provider = ai_provider_service.create_provider(db, **vorgabe)
    db.commit()
    db.refresh(provider)
    return provider


# ── Die Regel selbst ──────────────────────────────────────────────────────


def test_fuer_chat_verlangt_protokoll_und_modell(db: Session) -> None:
    """Zwei Bedingungen und keine dritte."""
    voll = _zugang(db)
    assert ai_provider_service.fuer_chat(voll) is True

    # Ein halb eingerichteter Chatzugang: er hört, aber er antwortet nicht.
    # `create_provider` lässt ihn zu, weil das Transkriptmodell für sich eine
    # Funktion ist — für den Chat taugt er trotzdem nicht.
    nur_gehoer = _zugang(db, default_model=None, transcription_model="whisper-1")
    assert nur_gehoer.default_model is None
    assert ai_provider_service.fuer_chat(nur_gehoer) is False

    # Leerzeichen sind kein Modell. Über `create_provider` kommt so etwas nicht
    # herein (es trimmt), über eine von Hand gepflegte Zeile schon.
    voll.default_model = "   "
    assert ai_provider_service.fuer_chat(voll) is False

    # Und ein Stimmzugang fällt schon am Protokoll — ElevenLabs kennt kein
    # `/chat/completions`, ein Modellname davor änderte daran nichts.
    stimme = _zugang(
        db,
        provider_kind="elevenlabs",
        default_model=None,
        default_voice="21m00Tcm4TlvDq8ikWAM",
        operator_api_key=None,
        requires_api_key=False,
    )
    stimme.default_model = "eleven_flash_v2_5"
    assert ai_provider_service.fuer_chat(stimme) is False


# ── Der Gleichlauf über die Wege ──────────────────────────────────────────


def test_ein_zugang_ohne_modell_steht_in_keiner_auswahl_und_wird_nicht_angenommen(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    user_csrf_token: str,
) -> None:
    """Was die Auswahl zeigt, muss das Senden auch annehmen — und umgekehrt.

    Driftet eine der beiden Fassungen, entsteht genau einer dieser beiden
    Fälle: eine Zeile in der Auswahl, die beim Absenden mit 404 antwortet, oder
    ein Zugang, der annimmt, aber nirgends wählbar ist. Beides sieht der
    Benutzer, und beides erklärt ihm niemand.
    """
    _enable_chat(db, regular_user)
    voll = _zugang(db)
    ohne_modell = _zugang(db, default_model=None, transcription_model="whisper-1")

    auswahl = client.get("/api/ai/providers", cookies=user_cookies)
    assert auswahl.status_code == 200
    gezeigt = {eintrag["id"] for eintrag in auswahl.json()}
    assert voll.id in gezeigt
    assert ohne_modell.id not in gezeigt

    abgewiesen = client.post(
        "/api/ai/conversation/messages/stream",
        json={
            "content": "Hallo",
            "provider_id": ohne_modell.id,
            "request_id": str(uuid4()),
        },
        cookies=user_cookies,
        headers={"X-CSRF-Token": user_csrf_token},
    )
    assert abgewiesen.status_code == 404


def test_alle_drei_wege_beantworten_dieselbe_frage_gleich(db: Session) -> None:
    """Chat-Router, Sprach-Router und Service dürfen nicht auseinanderlaufen.

    Der Sprach-Router stellt zusätzlich eine **zweite** Frage — ist der Zugang
    betriebsbereit, also mit Schlüssel versehen? Diese Trennung ist Absicht und
    wird hier mitgeprüft: derselbe Zugang ohne Schlüssel bleibt für den Chat
    tauglich (die Providerliste zeigt ihn als ``available: false``), aber der
    Sprachmodus greift ihn nicht.
    """
    ohne_modell = _zugang(db, default_model=None, transcription_model="whisper-1")
    voll = _zugang(db)

    assert ai_chat._fuer_chat(ohne_modell) is ai_provider_service.fuer_chat(ohne_modell)
    assert ai_chat._fuer_chat(voll) is ai_provider_service.fuer_chat(voll)
    assert ai_voice._denkender_zugang(db, ohne_modell.id) is not ohne_modell
    assert ai_voice._denkender_zugang(db, voll.id) is voll

    voll.operator_api_key_encrypted = None
    db.commit()
    assert ai_provider_service.fuer_chat(voll) is True
    assert ai_voice._denkender_zugang(db, voll.id) is None
