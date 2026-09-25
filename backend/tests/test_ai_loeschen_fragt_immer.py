"""Autonom aus: alles fragt. Autonom an: nur, was Server oder Rechte trifft.

Die Vorgabe des Betreibers vom 23.09.2026, wörtlich: „autonome Modus aus heißt
ALLES muss bestätigt werden, autonome Modus an bedeutet alles wird automatisch
bestätigt AUßer Löschvorgänge". Anlass war ein Beinahe-Verlust: im autonomen
Modus hätte das Modell dem Betreiber fast seinen Discord-Bot gelöscht.

Am 25.09.2026 hat er die Ausnahme enger gezogen („autonomer Modus bedeutet ja,
dass er autonom arbeiten soll"): seine eigenen Notizen, Termine, Aufgaben,
Erinnerungen, Skills und DNS-Einträge löscht die KI mit Freigabe ohne
Rückfrage (`EIGENE_DATEN_LOESCHEN`). Server, Dateien, Backups, Blueprints,
Rollen und fremde Rechte fragen weiter. Und bestätigt wird jede Karte nur noch
per Klick, auch in der Sprachansicht.

Geprüft wird an den Stellen, an denen die Regel entschieden wird, mit echter
Datenbank und ohne Attrappe an der Entscheidung selbst:

* `autonomy_allows` verneint jedes gesperrte Löschen, auch mit Freigabe, und
  bejaht die eigenen Daten.
* `create_proposal` legt ein Vergessen mit Freigabe autonom an, nicht als Karte.
* Ein bestätigter Lesevorschlag kommt geschwärzt zurück, wie ein direkter
  Aufruf (`_ausfuehren_read_tool`).
* Die Stimme fragt ohne Freigabe vor jedem Werkzeug (`freigabe_einholen`) und
  schickt zum Knopf; ein gesprochenes Ja führt nichts aus, ein Nein lehnt ab.
"""

from __future__ import annotations

import json
from uuid import uuid4

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


# ── Autonom an: nur, was Server oder Rechte trifft, fragt ────────────────


def test_mit_freigabe_fragt_nur_das_gesperrte_loeschen(
    db: Session, regular_user: User
) -> None:
    """Die Freigabe trägt die eigenen Daten, nicht Server, Dateien und Rollen.

    Die Gegenprobe steht dabei: ohne sie bewiese ein `False` nur, dass die
    Freigabe gar nicht greift.
    """
    _benutzer(db, regular_user, autonom=True)
    gesperrt = [n for n in LOESCHWERKZEUGE if n in ai_tool_registry.ALWAYS_CONFIRM_TOOLS]
    eigene = [n for n in LOESCHWERKZEUGE if n in ai_tool_registry.EIGENE_DATEN_LOESCHEN]
    # Sonst prüfen die Schleifen nichts.
    assert {"propose_file_delete", "propose_role_delete"} <= set(gesperrt)
    assert {"forget_memory", "propose_note_delete"} <= set(eigene)

    for name in gesperrt:
        assert not ai_autonomy_service.autonomy_allows(
            db, user=regular_user, server_id=None, tool_name=name
        ), name

    for name in [*eigene, "propose_note_create", "search_docs", "propose_task_set"]:
        assert ai_autonomy_service.autonomy_allows(
            db, user=regular_user, server_id=None, tool_name=name
        ), name


def test_vergessen_laeuft_mit_freigabe_ohne_karte(
    db: Session, regular_user: User
) -> None:
    """Betreiber, 25.09.2026: im autonomen Modus fragt Vergessen nicht mehr."""
    unterhaltung = _benutzer(db, regular_user, autonom=True)

    vorschlag = ai_proposal_service.create_proposal(
        db,
        user=regular_user,
        conversation=unterhaltung,
        tool_name="forget_memory",
        arguments={"scope": "user", "keys": ["lieblingsfarbe"], "server_id": None},
        correlation_id=str(uuid4()),
    )

    assert vorschlag.requires_confirmation is False
    assert vorschlag.autonomous is True


def test_ohne_freigabe_nennt_die_vergessen_karte_was_verschwindet(
    db: Session, regular_user: User
) -> None:
    """Die Karte nennt, was verschwindet, nicht nur den Werkzeugnamen: wer
    zustimmt, soll wissen, welche Einträge weg sind.
    """
    unterhaltung = _benutzer(db, regular_user, autonom=False)

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
    assert wert["hinweis"] == voice_interactions.KLICK_NOETIG
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


def test_ein_ja_fuehrt_auch_den_lesevorschlag_nicht_aus(
    db: Session, regular_user: User, monkeypatch
) -> None:
    """Seit dem 25.09.2026 ersetzt kein gesprochenes Ja den Klick, auch beim Lesen.

    Vorher führte es einen Lesevorschlag aus. Ein falsch erkanntes Geräusch
    war damit eine Zustimmung (Betreiber: „alles wird mit Karte bestätigt,
    sowohl Chat als auch Echtzeit").
    """
    unterhaltung = _benutzer(db, regular_user, autonom=False)
    gelaufen: list[str] = []
    monkeypatch.setattr(
        ai_action_service,
        "execute_read_tool",
        lambda *a, **k: gelaufen.append("lesen") or ROHERGEBNIS,
    )
    _, _, _, vorschlaege = voice_interactions.freigabe_einholen(
        regular_user.id,
        ProviderToolCall(id="ruf-1", name="search_docs", arguments={"query": "rcon"}),
        conversation_id=unterhaltung.id,
    )
    kennung = vorschlaege[0]["id"]

    assert voice_interactions.braucht_klick(user_id=regular_user.id, kennung=kennung) is True
    ausgang = voice_interactions.vorschlag_ausfuehren(user_id=regular_user.id, kennung=kennung)

    assert ausgang.erledigt is False
    assert gelaufen == []
    assert [v.status for v in _vorschlaege(db, regular_user)] == ["proposed"]


def test_ein_gesprochenes_ja_bekommt_den_knopf_ein_nein_lehnt_ab(
    db: Session, regular_user: User
) -> None:
    """Am Stapel der Sprachsitzung: Ja zeigt auf die Karte, Nein gilt."""
    unterhaltung = _benutzer(db, regular_user, autonom=False)
    stapel = voice_interactions.OffeneVorschlaege()
    for ruf in ("ruf-1", "ruf-2"):
        _, _, _, vorschlaege = voice_interactions.freigabe_einholen(
            regular_user.id,
            ProviderToolCall(id=ruf, name="search_docs", arguments={"query": ruf}),
            conversation_id=unterhaltung.id,
        )
        stapel.merken(vorschlaege[0])

    wert, fehler = stapel.entscheiden(user_id=regular_user.id, entscheidung="confirm")
    assert fehler is None
    assert wert == {
        "status": "needs_panel_confirmation",
        "hinweis": voice_interactions.KLICK_NOETIG,
    }
    assert [v.status for v in _vorschlaege(db, regular_user)] == ["proposed", "proposed"]

    wert, fehler = stapel.entscheiden(user_id=regular_user.id, entscheidung="reject")
    assert fehler is None
    assert wert["status"] == "rejected_by_user"
    assert wert["hinweis_verworfen"] == voice_interactions.VERWORFEN
    # Abgelehnt wie per Knopf: sonst stünde die Karte in der Sprachansicht
    # nach dem Nein weiter mit „Ausführen" da. Die ältere wartet weiter.
    stand = sorted(
        (v.created_at, v.status, v.error_code) for v in _vorschlaege(db, regular_user)
    )
    assert [(s, f) for _, s, f in stand] == [
        ("proposed", None),
        ("expired", "AI_ACTION_REJECTED"),
    ]


def test_die_stimme_schickt_zum_knopf_statt_nach_dem_ja_zu_fragen() -> None:
    """Der Prompt aller Sprachwege sagt dasselbe wie die Regel.

    Bis zum 23.09.2026 stand dort das Gegenteil: es gebe nichts, was auf eine
    Karte gehöre, Löschen eingeschlossen, und im Sprachmodus gebe es keinen
    Knopf. Ein Modell, das das liest, fragt nach einem Ja, das `braucht_klick`
    danach abweist.
    """
    from services import ai_prompt

    text = " ".join(ai_prompt.ZUSTIMMUNG_GESPROCHEN.split())
    assert "im Sprachmodus gibt es keinen" not in text
    assert "Karte auf seinen Klick wartet" in text
    assert "nie mit einem gesprochenen Ja" in text
    # Kein Rest der alten Regel, die nach einem Ja fragen liess.
    assert 'klares "Ja" fuehrt' not in text


def test_vergessen_bestaetigt_nur_der_klick(db: Session, regular_user: User) -> None:
    """Ohne Freigabe eine Karte, und ein gesprochenes Ja führt sie nicht aus.

    Betreiberwahl vom 23.09.2026 („Klick auf die Karte"): ein Ja stellt das
    Modell fest, und das Modell kann es aus einer Webseite oder Mail „gehört"
    haben. Die Stimme sagt deshalb gleich, wo bestätigt wird.
    """
    unterhaltung = _benutzer(db, regular_user, autonom=False)

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
