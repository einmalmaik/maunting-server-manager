"""Der Export erfasst neue Tabellen von selbst, und wo nicht, faellt es auf.

Welche Zeilen ins Paket gehen, liest ``datenexport_service`` aus den
Fremdschluesseln. Dieser Test haelt die beiden Luecken zu, die das Schema
nicht verraet:

1. Eine Tabelle ohne Weg zu ``users`` (blind, per Hash verknuepft) waere
   stillschweigend nicht dabei. Sie muss in ``OHNE_KONTOBEZUG`` stehen, mit Grund.
2. Eine neue Spalte mit Geheimnis oder eigenem Chiffrat landete ungeprueft im
   Paket. Klingt ihr Name danach, muss sie eingeordnet sein.
"""

import re

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table, Text

from database import Base
from models.dis_text import DisText
from services import datenexport_service as export

VERDAECHTIG = re.compile(
    r"(^|_)(hash|secret|token|jti|verifier|blob|salt|signature|auth|family|endpoint)($|_)"
    r"|encrypted|_enc$|password|cipher|p256dh|public_key|credential_id|embedding|key_index"
)


def _nicht_eingeordnet(tabellen, bedingungen) -> list[str]:
    return sorted(
        t.name for t in tabellen
        if t.name not in bedingungen and t.name not in export.OHNE_KONTOBEZUG
    )


def _verdaechtige_spalten(tabellen, bedingungen) -> list[str]:
    offen = []
    for tabelle in tabellen:
        if tabelle.name not in bedingungen:
            continue
        for spalte in tabelle.columns:
            name = f"{tabelle.name}.{spalte.name}"
            if isinstance(spalte.type, DisText) or not VERDAECHTIG.search(spalte.name):
                continue
            if name in export.AUSGESCHLOSSEN or name in export.ZEILEN_AAD or name in export.UNBEDENKLICH:
                continue
            offen.append(name)
    return offen


def test_jede_tabelle_ist_exportiert_oder_begruendet_ausgelassen():
    bedingungen = export.zeilenwahl(1, "0" * 64)
    assert _nicht_eingeordnet(Base.metadata.sorted_tables, bedingungen) == []


def test_jede_verdaechtige_spalte_ist_eingeordnet():
    bedingungen = export.zeilenwahl(1, "0" * 64)
    assert _verdaechtige_spalten(Base.metadata.sorted_tables, bedingungen) == []


def test_listen_nennen_nur_was_es_gibt():
    tabellen = Base.metadata.tables
    for name in export.OHNE_KONTOBEZUG:
        assert name in tabellen, name
    for liste in (export.AUSGESCHLOSSEN, export.UNBEDENKLICH, export.ZEILEN_AAD):
        for eintrag in liste:
            tabelle, spalte = eintrag.split(".")
            assert spalte in tabellen[tabelle].c, eintrag
    assert export.GEHEIMNISSE <= set(export.ZEILEN_AAD)


@pytest.mark.parametrize("fall", ["blind", "geheim"])
def test_der_waechter_schlaegt_an(fall):
    """Eine neue Tabelle ohne Eintrag bzw. eine neue Geheimnis-Spalte macht rot."""
    probe = MetaData()
    users = Table("users", probe, Column("id", Integer, primary_key=True))
    if fall == "blind":
        neu = Table("probe_blind", probe, Column("id", Integer, primary_key=True), Column("wem", Text))
        assert _nicht_eingeordnet([users, neu], {"users": True}) == ["probe_blind"]
    else:
        neu = Table(
            "probe_geheim", probe,
            Column("id", Integer, primary_key=True),
            Column("user_id", Integer, ForeignKey("users.id")),
            Column("api_secret_encrypted", Text),
        )
        assert _verdaechtige_spalten([users, neu], {"users": True, "probe_geheim": True}) == [
            "probe_geheim.api_secret_encrypted"
        ]
