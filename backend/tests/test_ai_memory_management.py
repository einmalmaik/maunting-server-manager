"""Die KI findet Erinnerungen nach Bedeutung und loescht sie auf Zuruf.

Der Fall aus der Beschreibung: *"loesch alles was ich ueber meinen Hund gesagt
habe"*. Das setzt zweierlei voraus — die Eintraege zu **finden**, auch wenn das
Wort "Hund" gar nicht darin vorkommt, und sie danach gezielt zu **loeschen**.

Beides ist bewusst getrennt. Eine Vektoraehnlichkeit von 0,4 ist eine
brauchbare Grundlage dafuer, jemandem etwas anzuzeigen, und eine schlechte
dafuer, es zu vernichten. Deshalb sucht das Modell zuerst, nennt was es
gefunden hat, und loescht danach benannte Schluessel.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from models import AiMemoryEntry, Role, RolePermission, Team, User
from services import ai_action_service, ai_embedding_service, ai_memory_service, team_service
from services.ai_action_errors import AiActionValidationError
from services.auth_service import AuthService
from services.role_service import set_user_roles


def _user(db: Session, name: str) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "MgmtPass123!")
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


def _allow(db: Session, user: User, *keys: str) -> None:
    role = Role(name=f"mgmt-{user.username}", description=None, is_system=False)
    db.add(role)
    db.flush()
    for key in keys:
        db.add(RolePermission(role_id=role.id, permission_key=key))
    db.commit()
    set_user_roles(db, user, [role.id])
    db.commit()
    if "ai.memory.use" in keys:
        ai_memory_service.set_preference(db, user, True)


def _remember(db: Session, user: User, key: str, value: str) -> None:
    ai_memory_service.upsert_entry(
        db, user=user, scope="user", server_id=None, key=key, value=value,
    )


def _keys_of(result: dict) -> set[str]:
    return {item["key"] for item in result["results"]}


# ── Finden ────────────────────────────────────────────────────────────


def test_search_finds_entries_the_user_can_see(db: Session, regular_user: User) -> None:
    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "hund.name", "Mein Hund heisst Bello")
    _remember(db, regular_user, "ram.bevorzugt", "8 GB fuer neue Server")

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="search_memory",
        arguments={"query": "Hund"},
    )

    assert "hund.name" in _keys_of(result)
    # Der Klartext gehoert dazu: wer loeschen soll, muss sehen was.
    treffer = next(item for item in result["results"] if item["key"] == "hund.name")
    assert "Bello" in treffer["value"]
    # Fremdtext bleibt als solcher gekennzeichnet.
    assert result["untrusted"] is True


def test_search_never_reaches_another_users_memory(
    db: Session, regular_user: User
) -> None:
    """Die Suche nutzt denselben Sichtbarkeitsfilter wie der Abruf.

    Sonst waere sie ein Weg, an Eintraege zu kommen, die im Kontext nie
    auftauchen wuerden — eine Hintertuer um die Trennung herum.
    """
    other = _user(db, "andere")
    _allow(db, regular_user, "ai.memory.use")
    _allow(db, other, "ai.memory.use")
    _remember(db, other, "gehalt", "Verdient 4200 Euro im Monat")

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="search_memory",
        arguments={"query": "Gehalt Einkommen Verdienst"},
    )

    assert result["results"] == []


@pytest.mark.skipif(
    not ai_embedding_service.is_available(),
    reason="Lokales Embeddingmodell nicht installiert",
)
def test_search_finds_what_is_worded_differently(
    db: Session, regular_user: User
) -> None:
    """Der eigentliche Zweck: "mein Hund" findet den Eintrag ueber Bello.

    Ein reiner Wortabgleich fiele hier durch — im Eintrag steht "Hund" gar
    nicht. Genau dafuer liegt neben jedem Eintrag ein Vektor.
    """
    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "bello", "Bello ist ein Golden Retriever und drei Jahre alt")
    _remember(db, regular_user, "backup.zeit", "Backups laufen nachts um drei")

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="search_memory",
        arguments={"query": "alles ueber meinen Hund"},
    )

    # Der Hundeeintrag muss vor dem Backupeintrag stehen.
    assert result["results"][0]["key"] == "bello"


def test_ein_team_treffer_traegt_den_namen_und_nicht_nur_die_nummer(
    db: Session, regular_user: User
) -> None:
    """Ein Treffer muss in dem Bereich ansprechbar sein, aus dem er stammt.

    Die volle Absage aus `ai_memory_service` nennt den Bereich beim **Namen**
    und macht daraus eine Auflage: „nur Eintraege aus genau diesem Bereich“.
    `remember` und `forget_memory` erreichen ein Team aber ausschliesslich ueber
    `team="<Name>"`; ein Werkzeug, das eine Nummer in einen Namen uebersetzt,
    gibt es nicht. Trug ein Treffer nur `team_id`, war die Auflage fuer das
    Modell schlicht nicht befolgbar.

    Folgenlos bliebe das nicht. Schluessel sind bewusst stabil und wiederholen
    sich ueber Teams hinweg — deshalb steht derselbe Schluessel hier in beiden
    Teams. Ohne den Namen stehen die zwei Treffer ununterscheidbar nebeneinander,
    das Modell greift den falschen, und `forget_memory` loescht den noch
    gueltigen Eintrag, waehrend der veraltete bleibt. Mit nur einem Team waere
    dieser Test gruen, ohne davon irgendetwas zu belegen.
    """
    _allow(db, regular_user, "ai.memory.use", "teams.create")
    alpha = team_service.create_team(db, user=regular_user, name="Alpha")
    beta = team_service.create_team(db, user=regular_user, name="Beta")
    for team, wert in (
        (alpha, "Wartungsfenster ist sonntags um 20 Uhr"),
        (beta, "Wartungsfenster ist mittwochs um 6 Uhr"),
    ):
        ai_memory_service.upsert_entry(
            db, user=regular_user, scope="team", server_id=None, team_id=team.id,
            key="wartungsfenster", value=wert,
        )

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="search_memory",
        arguments={"query": "Wartungsfenster"},
    )

    # Genau der Fall, um den es geht: ein Schluessel, zwei Bereiche.
    assert _keys_of(result) == {"wartungsfenster"}
    treffer = [item for item in result["results"] if item["scope"] == "team"]
    assert len(treffer) == 2
    # `.get` und nicht `[...]`: fehlt der Name, soll der Test das als fehlenden
    # Namen melden und nicht als KeyError.
    nach_namen = {item.get("team"): item for item in treffer}
    assert set(nach_namen) == {"Alpha", "Beta"}
    # Der Name muss zum Bereich gehoeren und nicht bloss vorhanden sein — sonst
    # bliebe der Test auch gruen, wenn beide Treffer denselben Namen truegen.
    assert nach_namen["Alpha"]["team_id"] == alpha.id
    assert nach_namen["Beta"]["team_id"] == beta.id
    # Die Nummer bleibt daneben stehen: sie ist der Fremdschluessel, ueber den
    # die Oberflaeche denselben Eintrag findet.
    assert {item["team_id"] for item in treffer} == {alpha.id, beta.id}


# ── Loeschen ──────────────────────────────────────────────────────────


def test_deletion_removes_exactly_the_named_keys(
    db: Session, regular_user: User
) -> None:
    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "hund.name", "Mein Hund heisst Bello")
    _remember(db, regular_user, "hund.rasse", "Golden Retriever")
    _remember(db, regular_user, "ram.bevorzugt", "8 GB fuer neue Server")

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="forget_memory",
        arguments={"scope": "user", "keys": ["hund.name", "hund.rasse"]},
    )

    assert result["forgotten"] == ["hund.name", "hund.rasse"]
    verbleibend = {
        row.key for row in
        db.query(AiMemoryEntry).filter(
            AiMemoryEntry.scope_identity == f"user:{regular_user.id}"
        ).all()
    }
    assert verbleibend == {"ram.bevorzugt"}


def test_a_key_that_does_not_exist_is_reported(db: Session, regular_user: User) -> None:
    """Sonst meldet das Modell ein Loeschen, das nie stattgefunden hat."""
    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "hund.name", "Mein Hund heisst Bello")

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="forget_memory",
        arguments={"scope": "user", "keys": ["hund.name", "gibt.es.nicht"]},
    )

    assert result["forgotten"] == ["hund.name"]
    assert result["not_found"] == ["gibt.es.nicht"]


def test_deletion_cannot_reach_another_users_memory(
    db: Session, regular_user: User
) -> None:
    """Derselbe Schluessel bei zwei Benutzern sind zwei verschiedene Zeilen."""
    other = _user(db, "andere")
    _allow(db, regular_user, "ai.memory.use")
    _allow(db, other, "ai.memory.use")
    _remember(db, regular_user, "zeitzone", "Europe/Berlin")
    _remember(db, other, "zeitzone", "America/New_York")

    ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="forget_memory",
        arguments={"scope": "user", "keys": ["zeitzone"]},
    )

    uebrig = db.query(AiMemoryEntry).filter(AiMemoryEntry.key == "zeitzone").all()
    assert len(uebrig) == 1
    assert uebrig[0].scope_identity == f"user:{other.id}"


def test_panel_memory_is_out_of_reach(db: Session, regular_user: User) -> None:
    """Was fuer alle gilt, loescht die KI nicht auf Zuruf eines Einzelnen."""
    _allow(db, regular_user, "ai.memory.use")

    with pytest.raises(AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="forget_memory",
            arguments={"scope": "panel", "keys": ["irgendwas"]},
        )


def test_team_deletion_requires_the_switch(db: Session, regular_user: User) -> None:
    colleague = _user(db, "kollege")
    _allow(db, regular_user, "ai.memory.use", "teams.create")
    _allow(db, colleague, "ai.memory.use")
    team = team_service.create_team(db, user=regular_user, name="Betrieb")
    team_service.add_member(
        db, team=team, user=regular_user, new_user_id=colleague.id,
        can_manage_skills=False, can_manage_memory=False,
    )
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="team", server_id=None, team_id=team.id,
        key="valheim.ram", value="Valheim braucht mindestens 6 GB",
    )

    # Der Kollege darf das Teamwissen nicht pflegen — sein Loeschversuch
    # landet deshalb im persoenlichen Bereich und laesst das Team unberuehrt.
    ai_action_service.execute_read_tool(
        db, user=colleague, tool_name="forget_memory",
        arguments={"scope": "team", "keys": ["valheim.ram"]},
    )

    assert db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == f"team:{team.id}"
    ).count() == 1


def test_deletion_without_the_permission_is_refused(
    db: Session, regular_user: User
) -> None:
    with pytest.raises(AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="forget_memory",
            arguments={"scope": "user", "keys": ["egal"]},
        )


def test_an_empty_key_list_is_refused(db: Session, regular_user: User) -> None:
    """Ohne Schluessel gibt es nichts zu loeschen — und kein "alles"."""
    _allow(db, regular_user, "ai.memory.use")

    with pytest.raises(AiActionValidationError):
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="forget_memory",
            arguments={"scope": "user", "keys": []},
        )


# ── Korrigieren ───────────────────────────────────────────────────────


def test_the_ai_does_not_silently_overwrite_what_the_user_said(
    db: Session, regular_user: User
) -> None:
    """Der Schutz gilt gegen die *stillschweigende* Korrektur.

    Die KI leitet nebenbei etwas ab und ueberschreibt damit, was der Benutzer
    selbst gesagt hat — das soll nicht passieren.
    """
    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "hund.name", "Mein Hund heisst Bello")

    with pytest.raises(AiActionValidationError) as exc:
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="remember",
            arguments={
                "scope": "user", "key": "hund.name", "value": "Mein Hund heisst Rex",
            },
        )
    # Die Meldung muss den richtigen Weg nennen. Frueher stand dort "verwende
    # einen anderen Schluessel" — genau das erzeugt die Dubletten, die wir
    # vermeiden wollen.
    assert "replace_user_entry" in str(exc.value)


def test_an_explicit_correction_overwrites_instead_of_duplicating(
    db: Session, regular_user: User
) -> None:
    """"Nein, er heisst Rex" soll nicht zu zwei Hunden fuehren.

    Verlangt der Benutzer die Korrektur ausdruecklich, ist das Ueberschreiben
    genau das Gewuenschte — der Schutz oben zielt auf etwas anderes.
    """
    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "hund.name", "Mein Hund heisst Bello")

    ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="remember",
        arguments={
            "scope": "user", "key": "hund.name", "value": "Mein Hund heisst Rex",
            "replace_user_entry": True,
        },
    )

    rows = db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == f"user:{regular_user.id}"
    ).all()
    assert len(rows) == 1
    _row, value = ai_memory_service.list_entries(db, regular_user, "user", None)[0]
    assert "Rex" in value


def test_ein_zweites_team_macht_das_merken_nicht_unmoeglich(
    db: Session, regular_user: User
) -> None:
    """Der Fall, in dem Teamwissen bisher gar nicht entstehen konnte.

    Bei zwei verwaltbaren Teams gab `learning_team` nur den Rueckfragetext
    zurueck — und `remember` hatte kein Argument, mit dem sich die Antwort
    haette einloesen lassen. Das Modell fragte, bekam eine Antwort, fragte
    wieder. Teamwissen war ab dem zweiten Team unerreichbar.
    """
    _allow(db, regular_user, "ai.memory.use", "teams.create")
    eins = team_service.create_team(db, user=regular_user, name="Ops")
    team_service.create_team(db, user=regular_user, name="Support")

    ohne = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="remember",
        arguments={"scope": "team", "key": "ram.minimum", "value": "Mindestens 6 GB"},
    )
    assert ohne["remembered"] is False
    assert "Ops" in ohne["ask_user"] and "Support" in ohne["ask_user"]

    mit = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="remember",
        arguments={
            "scope": "team", "key": "ram.minimum",
            "value": "Mindestens 6 GB", "team": "Ops",
        },
    )
    assert mit["remembered"] is True
    assert mit["scope"] == "team" and mit["team_id"] == eins.id
    assert db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == f"team:{eins.id}"
    ).count() == 1


def test_ein_erfundenes_team_landet_nirgends(db: Session, regular_user: User) -> None:
    """Der Name waehlt aus, er berechtigt nicht.

    Trifft er keinen Kandidaten, gibt es dieselbe Rueckfrage wie ohne ihn — und
    vor allem keinen Eintrag irgendwo. Ein stiller Rueckfall ins persoenliche
    Gedaechtnis waere hier das Schlimmste: der Benutzer glaubt, es steht im
    Team, und niemand ausser ihm sieht es.
    """
    _allow(db, regular_user, "ai.memory.use", "teams.create")
    team_service.create_team(db, user=regular_user, name="Ops")
    team_service.create_team(db, user=regular_user, name="Support")

    ergebnis = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="remember",
        arguments={
            "scope": "team", "key": "ram.minimum",
            "value": "Mindestens 6 GB", "team": "Gibt-Es-Nicht",
        },
    )
    assert ergebnis["remembered"] is False
    assert "Gibt-Es-Nicht" not in ergebnis["ask_user"]
    assert db.query(AiMemoryEntry).count() == 0


def test_wer_wissen_pflegen_darf_schreibt_ins_team_und_nicht_zu_sich(
    db: Session, regular_user: User
) -> None:
    """Der Schalter am Mitglied entscheidet — und zwar der richtige.

    Der Weg dorthin fragte fest `can_manage_skills` ab, obwohl fuer
    Erinnerungen `can_manage_memory` gilt. Ein Mitglied mit
    `memory=True, skills=False` bekam sein „merk dir fuers Team" still ins
    persoenliche Gedaechtnis geschrieben: kein Fehler, keine Meldung, nur der
    falsche Ort — und niemand im Team sah es je.
    """
    colleague = _user(db, "kollege")
    _allow(db, regular_user, "ai.memory.use", "teams.create")
    _allow(db, colleague, "ai.memory.use")
    team = team_service.create_team(db, user=regular_user, name="Betrieb")
    team_service.add_member(
        db, team=team, user=regular_user, new_user_id=colleague.id,
        can_manage_skills=False, can_manage_memory=True,
    )

    ergebnis = ai_action_service.execute_read_tool(
        db, user=colleague, tool_name="remember",
        arguments={
            "scope": "team", "key": "valheim.ram",
            "value": "Valheim braucht mindestens 6 GB",
        },
    )

    assert ergebnis["remembered"] is True
    assert ergebnis["scope"] == "team", "Der Eintrag darf nicht persoenlich werden"
    assert db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == f"team:{team.id}"
    ).count() == 1
    assert db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == f"user:{colleague.id}"
    ).count() == 0


def test_servernotizen_stehen_im_persoenlichen_bereich(
    db: Session, regular_user: User
) -> None:
    """Serverbezogene Notizen sind persoenlich — und waren nirgends sichtbar.

    Die KI schreibt sie (`remember` mit scope='server'), sie fliessen in jedes
    Gespraech und zaehlen gegen die 100er-Grenze. `list_entries` fragt aber
    genau eine Scope-Kennung ab und braucht dafuer eine konkrete `server_id` —
    wer alle seine Notizen sehen wollte, haette die Server raten muessen. In der
    Oberflaeche gab es sie deshalb nicht.
    """
    from models import Server, ServerPermission

    _allow(db, regular_user, "ai.memory.use")
    server = Server(
        name="Notizserver", game_type="dayz",
        install_dir="/tmp/notizserver", status="stopped",
    )
    db.add(server)
    db.commit()
    db.add(ServerPermission(
        user_id=regular_user.id, server_id=server.id, permission_key="server.view",
    ))
    db.commit()

    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None,
        key="ram.bevorzugt", value="Ich nehme immer 8 GB",
    )
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="server", server_id=server.id,
        key="startzeit", value="Startet nur mit erhoehtem Timeout",
    )
    db.commit()

    zeilen = ai_memory_service.personal_entries(db, regular_user)
    nach_scope = {row.scope: (row, wert) for row, wert in zeilen}
    assert set(nach_scope) == {"user", "server"}
    assert nach_scope["server"][0].server_id == server.id
    assert "Timeout" in nach_scope["server"][1]


def test_ein_unlesbarer_eintrag_nimmt_nicht_die_ganze_uebersicht_mit(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Entsprechung zur Härtung des Chatwegs, für die Verwaltungsansicht.

    Der Chat überspringt eine Zeile, die sich nicht mehr öffnen lässt, seit
    `test_one_unreadable_entry_does_not_take_the_whole_chat_down`. Die beiden
    Lesewege der Oberfläche (`GET /api/ai/memory` und
    `GET /api/ai/memory/personal`) hatten diese Härtung nicht: der Router
    übersetzt `DisSidecarError` zu 503, und `DisDecryptionError` ist dessen
    Unterklasse. Eine einzige beschädigte Zeile — verdrehte AAD, halb
    eingespielte Sicherung — ließ unter Profil > Memory dauerhaft "Memory ist
    nicht verfügbar" stehen, auch für die intakten Einträge daneben. Löschen
    konnte man den Störenfried auch nicht, weil man keine Kennung zu sehen
    bekam.
    """
    from services.dis_client import DisClient, DisDecryptionError

    _allow(db, regular_user, "ai.memory.use")
    kaputt, _ = ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None,
        key="kaputt", value="Unlesbarer Wert",
    )
    _remember(db, regular_user, "heil", "Lesbarer Wert")
    kaputte_id = kaputt.id

    echt = DisClient.decrypt

    def stolpert(payload, *, aad):
        if aad.endswith(kaputte_id):
            raise DisDecryptionError("AAD passt nicht mehr")
        return echt(payload, aad=aad)

    monkeypatch.setattr(DisClient, "decrypt", staticmethod(stolpert))

    uebersicht = ai_memory_service.list_entries(db, regular_user, "user", None)
    assert [row.key for row, _wert in uebersicht] == ["heil"]

    persoenlich = ai_memory_service.personal_entries(db, regular_user)
    assert [row.key for row, _wert in persoenlich] == ["heil"]


def test_ein_toter_sidecar_bleibt_ein_ehrlicher_fehler(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die andere Hälfte derselben Entscheidung.

    Antwortet der Sidecar gar nicht, scheitert **jede** Zeile. Würde die
    Verwaltungsansicht auch das still überspringen, sähe der Benutzer eine leere
    Liste und hielte sein Gedächtnis für gelöscht — während jeder Schreibversuch
    weiterhin mit 503 abbricht. Der Fehler muss also bis zum Router durchkommen.
    Genau darin unterscheidet sich der Helfer der Oberfläche vom Helfer des
    Chats, und nur darin.
    """
    from services.dis_client import DisClient, DisSidecarError

    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "heil", "Lesbarer Wert")

    def tot(payload, *, aad):
        raise DisSidecarError("Sidecar nicht erreichbar")

    monkeypatch.setattr(DisClient, "decrypt", staticmethod(tot))

    with pytest.raises(DisSidecarError):
        ai_memory_service.list_entries(db, regular_user, "user", None)
    with pytest.raises(DisSidecarError):
        ai_memory_service.personal_entries(db, regular_user)


def test_ein_entzogener_server_sperrt_die_eigene_notiz_nicht_ein(
    db: Session, regular_user: User
) -> None:
    """Wer den Zugriff verliert, muss seine eigene Notiz trotzdem loeschen koennen.

    Vorher verlangte `delete_entry` auch bei der eigenen Servernotiz weiterhin
    `server.view`. Die Zeile blieb damit in der Datenbank, zaehlte gegen das
    Kontingent des Benutzers und war fuer ihn unerreichbar — eigene Daten, die
    man nicht loeschen kann.

    Was das Modell zu sehen bekommt, ist davon unberuehrt: der Kontextaufbau
    prueft `server.view` weiterhin bei jedem Abruf.
    """
    from models import Server, ServerPermission

    _allow(db, regular_user, "ai.memory.use")
    server = Server(
        name="Entzogen", game_type="dayz",
        install_dir="/tmp/entzogen-mgmt", status="stopped",
    )
    db.add(server)
    db.commit()
    recht = ServerPermission(
        user_id=regular_user.id, server_id=server.id, permission_key="server.view",
    )
    db.add(recht)
    db.commit()
    eintrag, _ = ai_memory_service.upsert_entry(
        db, user=regular_user, scope="server", server_id=server.id,
        key="startzeit", value="Startet nur mit erhoehtem Timeout",
    )
    eintrag_id = eintrag.id
    db.commit()

    db.delete(recht)
    db.commit()

    assert "Timeout" not in (
        ai_memory_service.provider_memory_context(db, regular_user) or ""
    )
    ai_memory_service.delete_entry(db, regular_user, eintrag_id)
    assert db.get(AiMemoryEntry, eintrag_id) is None


def test_ein_persoenliches_team_nimmt_kein_teamwissen(
    db: Session, regular_user: User
) -> None:
    """Sonst entstuende ein Eintrag, den niemand je zu sehen bekaeme.

    Er laege unter `team:{persoenlich}`: die persoenliche Ansicht zeigt
    `scope='user'`, und eine Teamansicht gibt es fuer das Ein-Mann-Team nicht.
    Der KI-Weg stuft laengst auf `scope='user'` herunter — die Regel gehoert
    deshalb an den Dienst und nicht in die Aufrufer.
    """
    _allow(db, regular_user, "ai.memory.use")
    persoenlich = team_service.personal_team(db, regular_user)
    db.commit()

    with pytest.raises(Exception) as exc:
        ai_memory_service.upsert_entry(
            db, user=regular_user, scope="team", server_id=None,
            team_id=persoenlich.id, key="irgendwas", value="Gehoert hier nicht hin",
        )
    assert getattr(exc.value, "status_code", None) == 422
    db.rollback()
    assert db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == f"team:{persoenlich.id}"
    ).count() == 0


def test_eine_erfundene_server_id_heisst_server_nicht_gefunden(
    db: Session, regular_user: User
) -> None:
    """Recht ohne Existenz reicht nicht — sonst luegt die Fehlermeldung.

    Ein Benutzer mit pauschalem `server.view` (hier ueber die Rolle, beim Owner
    genauso) kommt an `has_server_permission` vorbei, ohne dass der Server je
    geladen wird. Nannte das Modell eine Nummer, die es gar nicht gibt, wurde
    die Zeile angelegt und erst der Fremdschluessel beim Commit warf sie
    zurueck — als "Bitte erneut versuchen". Das Modell befolgte die
    Aufforderung und wiederholte denselben aussichtslosen Aufruf, statt mit
    `list_my_servers` nach der richtigen Nummer zu suchen.
    """
    _allow(db, regular_user, "ai.memory.use", "server.view")

    with pytest.raises(AiActionValidationError) as exc:
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="remember",
            arguments={
                "scope": "server", "server_id": 424242,
                "key": "startzeit", "value": "Startet nur mit erhoehtem Timeout",
            },
        )
    # Die Diagnose entscheidet, was das Modell als naechstes tut.
    assert "Server nicht gefunden" in str(exc.value)
    assert "erneut versuchen" not in str(exc.value)
    db.rollback()
    assert db.query(AiMemoryEntry).filter(
        AiMemoryEntry.server_id == 424242
    ).count() == 0


def test_eine_erlaubte_korrektur_nimmt_dem_eintrag_nicht_dauerhaft_den_schutz(
    db: Session, regular_user: User
) -> None:
    """Nach "nein, korrigier das" bleibt es trotzdem eine Ansage des Benutzers.

    Vorher stufte die ausdruecklich erlaubte Korrektur den Eintrag auf
    `origin='ai'` herunter. Der Schutz gegen das stillschweigende Ueberschreiben
    haengt aber genau an diesem Feld — er galt danach fuer immer nicht mehr, und
    die naechste beilaeufige Ableitung der KI durfte den Wert ohne
    `replace_user_entry` ersetzen.
    """
    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "ram.bevorzugt", "Ich nehme immer 16 GB")

    ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="remember",
        arguments={
            "scope": "user", "key": "ram.bevorzugt",
            "value": "Ich nehme immer 8 GB", "replace_user_entry": True,
        },
    )

    zeile = db.query(AiMemoryEntry).filter(
        AiMemoryEntry.scope_identity == f"user:{regular_user.id}",
        AiMemoryEntry.key == "ram.bevorzugt",
    ).one()
    assert zeile.origin == "user"

    # Und deshalb greift der Schutz beim naechsten Mal wieder.
    with pytest.raises(AiActionValidationError) as exc:
        ai_action_service.execute_read_tool(
            db, user=regular_user, tool_name="remember",
            arguments={
                "scope": "user", "key": "ram.bevorzugt",
                "value": "Ich nehme immer 4 GB",
            },
        )
    assert "replace_user_entry" in str(exc.value)
    _row, wert = ai_memory_service.list_entries(db, regular_user, "user", None)[0]
    assert "8 GB" in wert
