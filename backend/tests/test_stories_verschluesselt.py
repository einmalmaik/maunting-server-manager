"""Stories stehen in der Datenbank nur verschluesselt.

Bis 29.09.2026 lagen Text und Bild einer Story im Klartext in
``chat_stories``, waehrend der Editor "Ende-zu-Ende verschluesselt" anzeigte.
Ende-zu-Ende sind sie weiterhin nicht (das Backend zeigt sie Freunden bzw.
allen Konten bei oeffentlichem Profil), aber ein Blick ins PostgreSQL-Studio
oder in einen Dump zeigt nur noch Chiffrat. Geprueft wird der Rohwert am ORM
vorbei.
"""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from models import User
from services.chat_media_validator import (
    MAX_STORY_IMAGE_BYTES,
    StorageLimitExceededError,
    validate_story_media_url,
)
from services.dis_client import DisClient
from services.social_service import SocialService

GEHEIM = "Bin gerade beim Arzt, Befund morgen"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
BILD = "data:image/png;base64," + base64.b64encode(_PNG).decode("ascii")


def _roh(db: Session, spalte: str, story_id: int) -> str:
    return db.execute(
        text(f"SELECT {spalte} FROM chat_stories WHERE id = :id"), {"id": story_id}
    ).scalar_one()


@pytest.mark.parametrize(("spalte", "wert"), [("content", GEHEIM), ("media_url", BILD)])
def test_rohwert_ist_chiffrat(db: Session, owner_user: User, spalte: str, wert: str) -> None:
    story = SocialService.create_story(db, owner_user, GEHEIM, media_url=BILD)

    roh = _roh(db, spalte, story.id)
    assert wert not in roh
    assert DisClient.ist_verschluesselt(roh)
    assert DisClient.decrypt(roh, aad=f"msm:social:chat_stories.{spalte}") == wert

    db.expire_all()
    stories = SocialService.list_active_stories(db, owner_user.id)
    assert next(s for s in stories if s["id"] == story.id)[spalte] == wert


def test_liste_entschluesselt_gebuendelt(db: Session, owner_user: User, monkeypatch) -> None:
    for i in range(3):
        SocialService.create_story(db, owner_user, f"Status {i}", media_url=BILD)
    db.expire_all()
    einzeln = []
    echt = DisClient.decrypt

    def zaehle(ciphertext, aad=None):
        einzeln.append(ciphertext)
        return echt(ciphertext, aad)

    monkeypatch.setattr(DisClient, "decrypt", staticmethod(zaehle))

    stories = SocialService.list_active_stories(db, owner_user.id)

    assert sorted(s["content"] for s in stories) == [f"Status {i}" for i in range(3)]
    assert all(s["media_url"] == BILD for s in stories)
    assert einzeln == []


def test_story_bild_passt_verschluesselt_unter_die_sidecar_grenze() -> None:
    """Ein 8-MB-Bild waere als Chiffrat ueber den 8 MiB des Sidecars gelandet."""
    zu_gross = "data:image/png;base64," + base64.b64encode(_PNG + b"\x00" * MAX_STORY_IMAGE_BYTES).decode("ascii")
    with pytest.raises(StorageLimitExceededError):
        validate_story_media_url(zu_gross)

    # Groesstes erlaubtes Bild: Base64 als Klartext, verschluesselt noch einmal
    # Base64 plus IV und Tag. Das muss unter MAX_JSON_BODY (8 MiB) bleiben und
    # in einen Stapel von vorab_entschluesselt (6 MiB) passen.
    groesste_url = len("data:image/png;base64,") + (MAX_STORY_IMAGE_BYTES + 2) // 3 * 4
    chiffrat = (groesste_url + 28 + 2) // 3 * 4 + len("msm-dis-v1:")
    assert chiffrat < 6 * 1024 * 1024
