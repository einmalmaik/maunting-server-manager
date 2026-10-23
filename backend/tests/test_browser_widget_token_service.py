"""Negativfälle der Wetter-Widget-Tokens.

Das Token gilt nur für den Wetterpunkt, nur mit `pw_`-Präfix, nur im Scope
`browser.wetter`, nicht nach Ablauf oder Widerruf und höchstens fünfmal
gleichzeitig. Gespeichert wird ausschließlich der Hash.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from models.browser_widget_token import BrowserWidgetToken
from models.user import User
from services import browser_widget_token_service as dienst


def _jetzt() -> datetime:
    return datetime.now(timezone.utc)


def _anlegen(
    db: Session,
    user_id: int,
    *,
    klar: str | None = None,
    scope: str = dienst.SCOPE,
    ablauf: datetime | None = None,
    widerrufen: datetime | None = None,
) -> tuple[str, BrowserWidgetToken]:
    klar = klar or f"pw_{secrets.token_urlsafe(32)}"
    zeile = BrowserWidgetToken(
        user_id=user_id,
        token_hash=dienst._hash(klar),
        scope=scope,
        expires_at=ablauf or (_jetzt() + timedelta(days=1)),
        revoked_at=widerrufen,
    )
    db.add(zeile)
    db.commit()
    db.refresh(zeile)
    return klar, zeile


def _aktive(db: Session, user_id: int) -> list[BrowserWidgetToken]:
    db.expire_all()
    return (
        db.query(BrowserWidgetToken)
        .filter(
            BrowserWidgetToken.user_id == user_id,
            BrowserWidgetToken.scope == dienst.SCOPE,
            BrowserWidgetToken.revoked_at.is_(None),
            BrowserWidgetToken.expires_at > _jetzt(),
        )
        .all()
    )


def test_pruefen_lehnt_token_ohne_pw_praefix_ab(db: Session, regular_user: User) -> None:
    _anlegen(db, regular_user.id, klar="sitzungstoken")

    assert dienst.pruefen(db, "sitzungstoken") is None


def test_pruefen_lehnt_unbekanntes_token_ab(db: Session, regular_user: User) -> None:
    assert dienst.pruefen(db, f"pw_{secrets.token_urlsafe(32)}") is None


def test_pruefen_lehnt_abgelaufenes_token_ab(db: Session, regular_user: User) -> None:
    klar, _ = _anlegen(db, regular_user.id, ablauf=_jetzt() - timedelta(seconds=1))

    assert dienst.pruefen(db, klar) is None


def test_pruefen_lehnt_widerrufenes_token_ab(db: Session, regular_user: User) -> None:
    klar, _ = _anlegen(db, regular_user.id, widerrufen=_jetzt())

    assert dienst.pruefen(db, klar) is None


def test_pruefen_lehnt_fremden_scope_ab(db: Session, regular_user: User) -> None:
    klar, _ = _anlegen(db, regular_user.id, scope="browser.andere")

    assert dienst.pruefen(db, klar) is None


def test_pruefen_setzt_last_used_nur_bei_gueltigem_token(db: Session, regular_user: User) -> None:
    klar, zeile = _anlegen(db, regular_user.id)

    assert dienst.pruefen(db, klar) == regular_user.id
    db.refresh(zeile)
    assert zeile.last_used_at is not None


def test_ausstellen_haelt_hoechstens_fuenf_tokens_aktiv(db: Session, regular_user: User) -> None:
    klartexte = []
    for _ in range(dienst.MAX_AKTIVE + 1):
        klar, _ = dienst.ausstellen(db, regular_user.id)
        assert klar.startswith("pw_")
        klartexte.append(klar)

    aktive = _aktive(db, regular_user.id)
    assert len(aktive) == dienst.MAX_AKTIVE
    # Das älteste Token wurde widerrufen, das neueste gilt.
    assert dienst.pruefen(db, klartexte[0]) is None
    assert dienst.pruefen(db, klartexte[-1]) == regular_user.id


def test_ausstellen_speichert_nur_den_hash(db: Session, regular_user: User) -> None:
    klar, _ = dienst.ausstellen(db, regular_user.id)

    zeile = db.query(BrowserWidgetToken).one()
    assert zeile.token_hash == dienst._hash(klar)
    assert zeile.token_hash != klar
    assert len(zeile.token_hash) == 64


def test_ausstellen_zaehlt_abgelaufene_und_widerrufene_nicht_mit(db: Session, regular_user: User) -> None:
    for _ in range(dienst.MAX_AKTIVE):
        _anlegen(db, regular_user.id, ablauf=_jetzt() - timedelta(days=1))
    _anlegen(db, regular_user.id, widerrufen=_jetzt())

    dienst.ausstellen(db, regular_user.id)

    assert len(_aktive(db, regular_user.id)) == 1
    db.expire_all()
    assert db.query(BrowserWidgetToken).filter(BrowserWidgetToken.revoked_at.isnot(None)).count() == 1


def test_widerrufen_trifft_nur_eigene_tokens_im_wetter_scope(db: Session, owner_user: User, regular_user: User) -> None:
    eigen, _ = _anlegen(db, regular_user.id)
    fremd, _ = _anlegen(db, owner_user.id)
    anderer_scope, _ = _anlegen(db, regular_user.id, scope="browser.andere")

    assert dienst.widerrufen(db, regular_user.id) == 1

    assert dienst.pruefen(db, eigen) is None
    assert dienst.pruefen(db, fremd) == owner_user.id
    db.expire_all()
    zeile = db.query(BrowserWidgetToken).filter(BrowserWidgetToken.token_hash == dienst._hash(anderer_scope)).one()
    assert zeile.revoked_at is None


def test_widerrufen_meldet_null_ohne_tokens(db: Session, regular_user: User) -> None:
    assert dienst.widerrufen(db, regular_user.id) == 0
