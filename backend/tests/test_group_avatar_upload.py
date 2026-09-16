"""Gruppenlogo: hochladen, ausliefern, loeschen.

Dasselbe Bildpruefwerk wie beim Benutzeravatar (`services/bild_upload.py`),
deshalb liegt der Schwerpunkt hier auf dem, was die Gruppe eigenmacht: nur
Besitzer und Admins duerfen aendern, aber jeder darf das Logo sehen — eine
Einladungskarte zeigt es, bevor jemand beigetreten ist.
"""

from __future__ import annotations

import io

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User
from services.social_service import SocialService

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _header(kekse: dict) -> dict[str, str]:
    return {"X-CSRF-Token": kekse.get("__Secure-csrf_token", "")}


def _bild(name: str = "logo.png", inhalt: bytes = PNG, typ: str = "image/png") -> dict:
    return {"file": (name, io.BytesIO(inhalt), typ)}


def test_logo_hochladen_ausliefern_und_loeschen(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    gruppe = SocialService.create_group(db, owner_user, "Logo-Gruppe")

    hoch = client.post(
        f"/api/social/groups/{gruppe.id}/avatar",
        files=_bild(),
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert hoch.status_code == 200
    pfad = hoch.json()["avatar_url"]
    assert pfad.startswith(f"/api/social/groups/avatar/group_{gruppe.id}_")

    # Ohne Anmeldung sichtbar: die Einladungskarte braucht das Logo vorher.
    geladen = client.get(pfad)
    assert geladen.status_code == 200
    assert geladen.content == PNG

    geloescht = client.delete(
        f"/api/social/groups/{gruppe.id}/avatar",
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert geloescht.status_code == 200
    assert geloescht.json()["avatar_url"] is None
    assert client.get(pfad).status_code == 404


def test_zweiter_upload_raeumt_den_ersten_weg(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    gruppe = SocialService.create_group(db, owner_user, "Zwei Logos")

    erst = client.post(
        f"/api/social/groups/{gruppe.id}/avatar",
        files=_bild(),
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    ).json()["avatar_url"]
    zweit = client.post(
        f"/api/social/groups/{gruppe.id}/avatar",
        files=_bild(),
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    ).json()["avatar_url"]

    assert erst != zweit
    assert client.get(erst).status_code == 404
    assert client.get(zweit).status_code == 200


def test_nur_besitzer_und_admins_aendern_das_logo(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    user_cookies: dict,
) -> None:
    gruppe = SocialService.create_group(db, owner_user, "Fremdes Logo")
    SocialService.join_group_by_invite_code(db, regular_user, gruppe.invite_code)

    abgelehnt = client.post(
        f"/api/social/groups/{gruppe.id}/avatar",
        files=_bild(),
        cookies=user_cookies,
        headers=_header(user_cookies),
    )
    assert abgelehnt.status_code == 403

    entfernen = client.delete(
        f"/api/social/groups/{gruppe.id}/avatar",
        cookies=user_cookies,
        headers=_header(user_cookies),
    )
    assert entfernen.status_code == 403


def test_nichtmitglied_kommt_gar_nicht_heran(
    client: TestClient,
    db: Session,
    owner_user: User,
    regular_user: User,
    user_cookies: dict,
) -> None:
    gruppe = SocialService.create_group(db, owner_user, "Geschlossen")

    antwort = client.post(
        f"/api/social/groups/{gruppe.id}/avatar",
        files=_bild(),
        cookies=user_cookies,
        headers=_header(user_cookies),
    )
    assert antwort.status_code == 403


def test_falsches_format_wird_abgewiesen(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    gruppe = SocialService.create_group(db, owner_user, "Kein Bild")

    antwort = client.post(
        f"/api/social/groups/{gruppe.id}/avatar",
        files=_bild("boese.exe", b"MZ\x90\x00keinbild", "application/octet-stream"),
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 400


def test_falsche_magic_bytes_trotz_richtigem_mime(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    """Der behauptete Typ allein reicht nicht, der Inhalt muss passen."""
    gruppe = SocialService.create_group(db, owner_user, "Getarnt")

    antwort = client.post(
        f"/api/social/groups/{gruppe.id}/avatar",
        files=_bild("getarnt.png", b"<?php system($_GET[0]); ?>", "image/png"),
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 400


def test_zu_grosses_bild_wird_abgewiesen(
    client: TestClient, db: Session, owner_user: User, owner_cookies: dict
) -> None:
    gruppe = SocialService.create_group(db, owner_user, "Zu gross")
    zu_gross = PNG + b"\x00" * (5 * 1024 * 1024)

    antwort = client.post(
        f"/api/social/groups/{gruppe.id}/avatar",
        files=_bild("riesig.png", zu_gross),
        cookies=owner_cookies,
        headers=_header(owner_cookies),
    )
    assert antwort.status_code == 400


def test_dateiname_ausserhalb_des_musters_liefert_nichts(client: TestClient) -> None:
    """Kein Pfaddurchstieg ueber den oeffentlichen Ausliefer-Endpunkt."""
    for name in (
        "..%2F..%2Fetc%2Fpasswd",
        "group_1_abc.php",
        "avatar_1_abc.png",
        "group_1_abc.png.exe",
    ):
        assert client.get(f"/api/social/groups/avatar/{name}").status_code == 404
