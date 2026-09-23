"""Autonom aus: alles fragt. Autonom an: nur das Löschen fragt.

Die Vorgabe des Betreibers vom 23.09.2026, wörtlich: „autonome Modus aus heißt
ALLES muss bestätigt werden, autonome Modus an bedeutet alles wird automatisch
bestätigt AUßer Löschvorgänge". Anlass war ein Beinahe-Verlust: im autonomen
Modus hätte das Modell dem Betreiber fast seinen Discord-Bot gelöscht, weil es
einen Löschvorgang aus Versehen anstieß.

Geprüft wird an den Stellen, an denen die Regel entschieden wird, mit echter
Datenbank und ohne Attrappe an der Entscheidung selbst:

* `autonomy_allows` verneint jedes Löschen, auch mit Freigabe.
* `create_proposal` legt ein Vergessen auch mit Freigabe als Karte an.
* Ein bestätigter Lesevorschlag kommt geschwärzt zurück, wie ein direkter
  Aufruf (`_ausfuehren_read_tool`).
* Die Stimme fragt ohne Freigabe vor jedem Werkzeug (`freigabe_einholen`), und
  ein gesprochenes Ja führt den Lesevorschlag aus und bringt sein Ergebnis mit.
* Ein Löschen bestätigt nur der Klick, nie die Stimme.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from models import AiActionProposal, AiConversation, Role, RolePermission, User
from services import (
    ai_action_service,
    ai_autonomy_service,
    ai_proposal_service,
    ai_tool_registry,
)
from services.ai_voice import interactions as voice_interactions
from services.openai_compatible_adapter import ProviderToolCall
from services.role_service import set_user_roles


LOESCHWERKZEUGE = sorted(
    name for name in ai_tool_registry.WERKZEUGE
    if name.endswith("_delete") or name.startswith("forget_")
)

#: Was `read_blueprint` wirklich liefert, wenn ein Blueprint ein Passwort in
#: seiner Umgebung trägt. Kein echtes Geheimnis, nur seine Form.
ROHERGEBNIS = {"runtime": {"env": {"RCON_PASSWORD": "hunter2"}}}


def _benutzer(db: Session, user: User, *, autonom: bool) -> AiConversation:
    """Rechte für Chat und Gedächtnis, auf Wunsch die Autonomie-Freigabe dazu."""
    rechte = ["ai.chat.use", "ai.memory.use"]
    if autonom:
        rechte.append("ai.autonomous.use")
    rolle = Role(name=f"loeschen-{uuid4().hex[:8]}", description=None, is_system=False)
    db.add(rolle)
    db.flush()
    for recht in rechte:
        db.add(RolePermission(role_id=rolle.id, permission_key=recht))
    db.commit()
    set_user_roles(db, user, [rolle.id])
    if autonom:
        ai_autonomy_service.set_grant(
            db, user=user, server_id=None, enabled=True,
            max_actions_per_hour=50, granted_by=user.id,
        )
    unterhaltung = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Löschen"
    )
    db.add(unterhaltung)
    db.commit()
    return unterhaltung


def _vorschlaege(db: Session, user: User) -> list[AiActionProposal]:
    db.expire_all()
    return db.query(AiActionProposal).filter(AiActionProposal.user_id == user.id).all()


# ── Autonom an: nur das Löschen fragt ─────────────────────────────────────


def test_mit_freigabe_fragt_jedes_loeschen(db: Session, regular_user: User) -> None:
    """Die Freigabe trägt alles außer dem Löschen.

    Die Gegenprobe steht dabei: ohne sie bewiese ein `False` nur, dass die
    Freigabe gar nicht greift.
    """
    _benutzer(db, regular_user, autonom=True)
    assert LOESCHWERKZEUGE  # sonst prüft die Schleife nichts

    for name in LOESCHWERKZEUGE:
        assert not ai_autonomy_service.autonomy_allows(
            db, user=regular_user, server_id=None, tool_name=name
        ), name

    for name in ("propose_note_create", "search_docs", "propose_task_set"):
        assert ai_autonomy_service.autonomy_allows(
            db, user=regular_user, server_id=None, tool_name=name
        ), name


def test_vergessen_steht_auch_mit_freigabe_auf_einer_karte(
    db: Session, regular_user: User
) -> None:
    """Vergessen ist Löschen, auch wenn es als Lesewerkzeug geführt wird.

    Die Karte nennt, was verschwindet, nicht nur den Werkzeugnamen: wer
    zustimmt, soll wissen, welche Einträge weg sind.
    """
    unterhaltung = _benutzer(db, regular_user, autonom=True)

    vorschlag = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=unterhaltung,
        tool_name="forget_memory",
        arguments={"scope": "user", "keys": ["lieblingsfarbe"], "server_id": None},
        correlation_id=str(uuid4()),
    )

    assert vorschlag.status == "proposed"
    assert vorschlag.requires_confirmation is True
    assert vorschlag.autonomous is False
    vorschau = json.loads(vorschlag.preview_json)
    assert vorschau["memory_scope"] == "user"
    assert vorschau["memory_keys"] == ["lieblingsfarbe"]


# ── Ein bestätigter Lesevorschlag ist so geschwärzt wie ein direkter ──────


def test_ein_bestaetigter_lesevorschlag_kommt_geschwaerzt_zurueck(
    db: Session, regular_user: User, monkeypatch
) -> None:
    """Ohne autonomen Modus läuft jedes Lesen über diesen Weg.

    Der direkte Weg schwärzt jedes Ergebnis, bevor es zum Modell geht. Der
    Weg über die Karte tat es bis zum 23.09.2026 nicht, und seit die Stimme
    ohne Freigabe vor jedem Werkzeug fragt, läuft dort fast alles entlang.
    """
    unterhaltung = _benutzer(db, regular_user, autonom=False)
    monkeypatch.setattr(ai_action_service, "execute_read_tool", lambda *a, **k: ROHERGEBNIS)

    vorschlag = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=unterhaltung,
        tool_name="search_docs",
        arguments={"query": "rcon"},
        correlation_id=str(uuid4()),
    )
    assert vorschlag.requires_confirmation is True
    _, token = ai_proposal_service.confirm_proposal(
        db, proposal_id=vorschlag.id, user=regular_user
    )
    _, ergebnis = ai_proposal_service.execute_proposal(
        db, proposal_id=vorschlag.id, user=regular_user, confirmation_token=token
    )

    assert ergebnis == {"runtime": {"env": {"RCON_PASSWORD": "[REDACTED]"}}}
    assert "hunter2" not in json.dumps(ergebnis)


# ── Die Stimme: autonom aus heißt, sie fragt vor jedem Werkzeug ──────────


def test_ohne_freigabe_fragt_die_stimme_vor_dem_lesen(
    db: Session, regular_user: User, monkeypatch
) -> None:
    """Auch vor der Doku-Suche, und das Werkzeug selbst läuft nicht."""
    unterhaltung = _benutzer(db, regular_user, autonom=False)
    gelaufen: list[str] = []
    monkeypatch.setattr(
        ai_action_service,
        "execute_read_tool",
        lambda *a, **k: gelaufen.append(k.get("tool_name")) or {},
    )

    karte = voice_interactions.freigabe_einholen(
        regular_user.id,
        ProviderToolCall(id="ruf-1", name="search_docs", arguments={"query": "backup"}),
        conversation_id=unterhaltung.id,
    )

    assert karte is not None
    wert, fehler, anzeige, vorschlaege = karte
    assert fehler is None
    assert wert["status"] == "needs_confirmation"
    assert wert["hinweis"] == voice_interactions.JA_NOETIG
    # Eine wartende Karte ist kein Suchergebnis: das Panel zeichnet nichts.
    assert "web_results" not in anzeige and "geo_analysis" not in anzeige
    assert [v["tool_name"] for v in vorschlaege] == ["search_docs"]
    assert gelaufen == []

    gespeichert = _vorschlaege(db, regular_user)
    assert [(v.tool_name, v.status, v.requires_confirmation) for v in gespeichert] == [
        ("search_docs", "proposed", True)
    ]


def test_mit_freigabe_liest_die_stimme_ohne_rueckfrage(
    db: Session, regular_user: User
) -> None:
    unterhaltung = _benutzer(db, regular_user, autonom=True)

    karte = voice_interactions.freigabe_einholen(
        regular_user.id,
        ProviderToolCall(id="ruf-1", name="search_docs", arguments={"query": "backup"}),
        conversation_id=unterhaltung.id,
    )

    assert karte is None
    assert _vorschlaege(db, regular_user) == []


def test_ohne_unterhaltung_laeuft_nichts(db: Session, regular_user: User) -> None:
    """Ohne Unterhaltung gäbe es keinen Ort für die Karte, also auch kein Ja."""
    _benutzer(db, regular_user, autonom=False)

    karte = voice_interactions.freigabe_einholen(
        regular_user.id,
        ProviderToolCall(id="ruf-1", name="search_docs", arguments={"query": "backup"}),
        conversation_id=None,
    )

    assert karte is not None
    _, fehler, anzeige, vorschlaege = karte
    assert fehler
    assert anzeige.get("failed") is True
    assert vorschlaege == []
    assert _vorschlaege(db, regular_user) == []


def test_ein_ja_fuehrt_den_lesevorschlag_aus_und_bringt_das_ergebnis_mit(
    db: Session, regular_user: User, monkeypatch
) -> None:
    """Das Ja ersetzt den Klick, und das Ergebnis kommt geschwärzt zurück.

    Ein Lesevorschlag der Stimme hängt an keinem Lauf, der das Ergebnis
    weitertrüge. Ohne `Ausgang.ergebnis` erführe das Modell nach dem Ja nur
    „bestätigt".
    """
    unterhaltung = _benutzer(db, regular_user, autonom=False)
    monkeypatch.setattr(ai_action_service, "execute_read_tool", lambda *a, **k: ROHERGEBNIS)
    wert, _, _, vorschlaege = voice_interactions.freigabe_einholen(
        regular_user.id,
        ProviderToolCall(id="ruf-1", name="search_docs", arguments={"query": "rcon"}),
        conversation_id=unterhaltung.id,
    )
    kennung = vorschlaege[0]["id"]

    assert voice_interactions.schon_entschieden(user_id=regular_user.id, kennung=kennung) is None
    ausgang = voice_interactions.vorschlag_ausfuehren(user_id=regular_user.id, kennung=kennung)

    assert ausgang.erledigt is True
    assert ausgang.werkzeug == "search_docs"
    assert ausgang.ergebnis == {"runtime": {"env": {"RCON_PASSWORD": "[REDACTED]"}}}
    assert voice_interactions.ergebnis_fuers_modell(ausgang) == {
        "status": "confirmed",
        "tool_name": "search_docs",
        "result": ausgang.ergebnis,
    }
    # Ein zweites Ja findet den Vorschlag erledigt und führt nichts mehr aus.
    assert voice_interactions.schon_entschieden(
        user_id=regular_user.id, kennung=kennung
    ) == "succeeded"


def test_die_stimme_schickt_zum_knopf_statt_nach_dem_ja_zu_fragen() -> None:
    """Der Prompt aller Sprachwege sagt dasselbe wie die Regel.

    Bis zum 23.09.2026 stand dort das Gegenteil: es gebe nichts, was auf eine
    Karte gehöre, Löschen eingeschlossen, und im Sprachmodus gebe es keinen
    Knopf. Ein Modell, das das liest, fragt nach einem Ja, das `braucht_klick`
    danach abweist.
    """
    from services import ai_prompt

    text = ai_prompt.ZUSTIMMUNG_GESPROCHEN
    assert "im Sprachmodus gibt es keinen" not in text
    assert "Knopf auf der\nKarte" in text or "Knopf auf der Karte" in text
    assert "nie mit einem gesprochenen Ja" in text


@pytest.mark.parametrize("autonom", [False, True])
def test_vergessen_bestaetigt_nur_der_klick(
    db: Session, regular_user: User, autonom: bool
) -> None:
    """Auch mit Freigabe eine Karte, und ein gesprochenes Ja führt sie nicht aus.

    Betreiberwahl vom 23.09.2026 („Klick auf die Karte"): ein Ja stellt das
    Modell fest, und das Modell kann es aus einer Webseite oder Mail „gehört"
    haben. Die Stimme sagt deshalb gleich, wo bestätigt wird.
    """
    unterhaltung = _benutzer(db, regular_user, autonom=autonom)

    karte = voice_interactions.freigabe_einholen(
        regular_user.id,
        ProviderToolCall(
            id="ruf-1",
            name="forget_memory",
            arguments={"scope": "user", "keys": ["lieblingsfarbe"], "server_id": None},
        ),
        conversation_id=unterhaltung.id,
    )

    assert karte is not None
    wert, _, _, vorschlaege = karte
    assert wert["hinweis"] == voice_interactions.KLICK_NOETIG
    kennung = vorschlaege[0]["id"]
    assert voice_interactions.braucht_klick(user_id=regular_user.id, kennung=kennung) is True

    ausgang = voice_interactions.vorschlag_ausfuehren(user_id=regular_user.id, kennung=kennung)

    assert ausgang.erledigt is False
    # Die Karte bleibt stehen: abgewiesen ist das Ja, nicht der Vorschlag.
    assert [v.status for v in _vorschlaege(db, regular_user)] == ["proposed"]
