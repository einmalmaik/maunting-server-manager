"""Die KI findet Erinnerungen nach Bedeutung; der Dienst verwaltet sie.

Der Fall aus der Beschreibung: *"was weisst du ueber meinen Hund?"*. Das setzt
voraus, die Eintraege zu **finden**, auch wenn das Wort "Hund" gar nicht darin
vorkommt — dafuer liegt neben jedem Eintrag ein Vektor.

Geloescht hat die KI frueher selbst, ueber `forget_memory` und benannte
Schluessel. Seit Stufe 2 des Gedaechtnisses (06.10.2026) vergisst der
Hintergrund, was der Mensch im Gespraech verlangt (`test_ai_gedaechtnis_schreiber`).
Hier bleiben die Suche und was der Dienst unter der Oberflaeche zusagt:
Uebersicht, Blaettern, Loeschen, die Grenzen der Bereiche.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import AiMemoryEntry, Role, RolePermission, Team, User
from services import ai_action_service, ai_embedding_service, ai_memory_service, team_service
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


# ── Finden ────────────────────────────────────────────────────────────


def test_search_finds_entries_the_user_can_see(db: Session, regular_user: User) -> None:
    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "hund.name", "Mein Hund heisst Bello")
    _remember(db, regular_user, "ram.bevorzugt", "8 GB fuer neue Server")
    ai_memory_service.erinnerung_anlegen(
        db, user=regular_user, scope="user",
        text="Der Hund des Benutzers mag Schnee.", titel="Hund im Winter",
    )

    result = ai_action_service.execute_read_tool(
        db, user=regular_user, tool_name="search_memory",
        arguments={"query": "Hund"},
    )

    # Der Klartext gehoert dazu: wer antworten soll, muss sehen was.
    nach_text = {item["text"]: item for item in result["results"]}
    altbestand = nach_text["Mein Hund heisst Bello"]
    satz = nach_text["Der Hund des Benutzers mag Schnee."]
    assert altbestand["origin"] == satz["origin"] == "user"
    # Altbestand traegt seinen Namen weiter mit: dort ist er Teil der Aussage.
    assert altbestand["key"] == "hund.name"
    # Ein Satz hat keinen, dafuer einen Titel.
    assert "key" not in satz and satz["titel"] == "Hund im Winter"
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
    assert "Golden Retriever" in result["results"][0]["text"]


def test_ein_team_treffer_sagt_aus_welchem_team_er_stammt(
    db: Session, regular_user: User
) -> None:
    """Zwei Teams namens "Alpha", zwei Wartungsfenster — welches gilt wo?

    Singra soll sagen, woher etwas kommt (`ai_prompt.GEDAECHTNIS`). Ein Treffer
    nur mit dem Bereich "team" liesse die beiden Wartungsfenster
    ununterscheidbar nebeneinander stehen, und die Antwort ordnete eines dem
    falschen Team zu. Die Nummer half dem Modell dabei nie: ein Werkzeug, das
    sie in einen Namen uebersetzt, gibt es nicht. Bis Stufe 2 des Gedaechtnisses
    trug der Treffer sie trotzdem, als Rueckweg fuer `forget_memory`; seitdem
    steht nur der Name da.

    Teamnamen sind nur je Gruender eindeutig (`_assert_name_is_free`). Mit zwei
    verschieden benannten Teams waere dieser Test gruen, ohne den eigentlichen
    Fall zu belegen — deshalb heissen hier beide gleich, und der Name muss den
    Gruender mitbringen (`team_service.ansprechbarer_name`).
    """
    zweiter = _user(db, "zweiter")
    kollege = _user(db, "kollege")
    _allow(db, regular_user, "teams.create", "ai.memory.use")
    _allow(db, zweiter, "teams.create", "ai.memory.use")
    _allow(db, kollege, "ai.memory.use")
    eins = team_service.create_team(db, user=regular_user, name="Alpha")
    zwei = team_service.create_team(db, user=zweiter, name="Alpha")
    for team, gruender, wert in (
        (eins, regular_user, "Das Wartungsfenster ist sonntags um 20 Uhr."),
        (zwei, zweiter, "Das Wartungsfenster ist mittwochs um 6 Uhr."),
    ):
        team_service.invite_member(
            db, team=team, user=gruender, new_user_id=kollege.id,
            can_manage_skills=False, can_manage_memory=False,
        )
        team_service.accept_invitation(db, user=kollege, team_id=team.id)
        ai_memory_service.erinnerung_anlegen(
            db, user=gruender, scope="team", team_id=team.id, text=wert,
        )

    result = ai_action_service.execute_read_tool(
        db, user=kollege, tool_name="search_memory",
        arguments={"query": "Wartungsfenster"},
    )

    # `.get` und nicht `[...]`: fehlt der Name, soll der Test das als fehlenden
    # Namen melden und nicht als KeyError.
    treffer = {
        item.get("team"): item["text"]
        for item in result["results"] if item["scope"] == "team"
    }
    assert treffer == {
        f"Alpha ({regular_user.username})": "Das Wartungsfenster ist sonntags um 20 Uhr.",
        "Alpha (zweiter)": "Das Wartungsfenster ist mittwochs um 6 Uhr.",
    }
    # Die Nummer geht nicht mehr mit hinaus: sie war nur fuer das Loeschen da.
    assert all("team_id" not in item for item in result["results"])


# ── Was der Dienst unter der Oberflaeche zusagt ───────────────────────


def test_eine_volle_absage_benennt_das_gemeinte_team_eindeutig(
    db: Session, regular_user: User
) -> None:
    """Der Bereich in der Absage muss genau ein Team benennen.

    Die volle Absage nennt den Bereich beim Namen; lesen tut sie ein Mensch,
    als Toast. Teamnamen sind aber nur je Gründer eindeutig — hiess der Bereich
    schlicht „Alpha“, benannte er zwei Teams auf einmal, und wer daraufhin
    aufräumte, räumte womöglich im falschen. Bis Stufe 2 des Gedächtnisses las
    die Absage auch das Modell und sprach das Team danach über `learning_team`
    an.

    Geprüft wird deshalb nicht der Wortlaut, sondern der Rückweg — der Name
    aus der Absage muss über `learning_team` genau das Team auswählen, über
    das sie sprach.
    """
    from services.ai_limit_service import LIMIT_FIELDS, set_role_limit

    zweiter = _user(db, "zweiter")
    kollege = _user(db, "kollege")
    _allow(db, regular_user, "teams.create")
    _allow(db, zweiter, "teams.create")
    _allow(db, kollege, "ai.memory.use")
    eins = team_service.create_team(db, user=regular_user, name="Alpha")
    zwei = team_service.create_team(db, user=zweiter, name="Alpha")
    for team, owner in ((eins, regular_user), (zwei, zweiter)):
        team_service.invite_member(
            db, team=team, user=owner, new_user_id=kollege.id,
            can_manage_skills=True, can_manage_memory=True,
        )
        team_service.accept_invitation(db, user=kollege, team_id=team.id)

    # Der Vorrat eines Teams hängt am Gründer — der hier keinen freigibt.
    rolle = db.query(Role).filter(Role.name == f"mgmt-{regular_user.username}").one()
    set_role_limit(db, rolle.id, {feld: 0 for feld in LIMIT_FIELDS})
    db.commit()

    with pytest.raises(ai_memory_service.MemoryScopeVoll) as exc:
        ai_memory_service.upsert_entry(
            db, user=kollege, scope="team", server_id=None, team_id=eins.id,
            key="wartungsfenster", value="Sonntags um 20 Uhr",
        )

    genannt = re.search("„(.+)“", exc.value.bereich)
    assert genannt is not None, exc.value.bereich
    ziel, frage = team_service.learning_team(
        db, kollege, schalter="memory", wunsch=genannt.group(1),
    )
    assert frage is None, f"„{genannt.group(1)}“ wählt kein Team aus"
    assert ziel is not None and ziel.id == eins.id
    # Und nicht bloss irgendeines: das andere trägt denselben Namen.
    assert ziel.id != zwei.id
    # Die Probe aufs Exempel — der blanke Name, der hier bis zuletzt stand,
    # benennt beide Teams und wählt deshalb keines aus.
    _, ohne_gruender = team_service.learning_team(
        db, kollege, schalter="memory", wunsch=eins.name,
    )
    assert ohne_gruender is not None


def test_servernotizen_stehen_im_persoenlichen_bereich(
    db: Session, regular_user: User
) -> None:
    """Serverbezogene Notizen sind persoenlich — und waren nirgends sichtbar.

    Die KI schreibt sie (seit Stufe 2 des Gedaechtnisses der Hintergrund, in
    den Bereich `server`), sie fliessen in jedes Gespraech und zaehlen gegen
    das Rollenlimit. `list_entries` fragt aber
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
    nach_scope = {row.scope: (row, wert) for row, wert in zeilen.eintraege}
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
    from tests._entschluesselung import unlesbar_machen

    _allow(db, regular_user, "ai.memory.use")
    kaputt, _ = ai_memory_service.upsert_entry(
        db, user=regular_user, scope="user", server_id=None,
        key="kaputt", value="Unlesbarer Wert",
    )
    _remember(db, regular_user, "heil", "Lesbarer Wert")
    unlesbar_machen(monkeypatch, kaputt.id)

    uebersicht = ai_memory_service.list_entries(db, regular_user, "user", None)
    assert [row.key for row, _wert in uebersicht] == ["heil"]

    persoenlich = ai_memory_service.personal_entries(db, regular_user)
    assert [row.key for row, _wert in persoenlich.eintraege] == ["heil"]
    # Die Gesamtzahl kommt aus der Datenbank, nicht aus der Liste: die kaputte
    # Zeile ist immer noch da und zaehlt gegen den Bereich. Wer sie unterschluege,
    # meldete "1 Eintrag" und liesse den Benutzer raten, wo sein zweiter blieb.
    assert persoenlich.gesamt == 2


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
    from services.dis_client import DisSidecarError
    from tests._entschluesselung import sidecar_tot

    _allow(db, regular_user, "ai.memory.use")
    _remember(db, regular_user, "heil", "Lesbarer Wert")
    sidecar_tot(monkeypatch)

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
    Der Gedaechtnisschreiber bietet das persoenliche Team gar nicht erst an
    (`_bereiche`) — die Regel gehoert trotzdem an den Dienst und nicht in die
    Aufrufer.
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
    geladen wird. Eine Nummer, die es gar nicht gibt, ergab eine Zeile, die
    erst der Fremdschluessel beim Commit zurueckwarf — als "Bitte erneut
    versuchen". Das Modell, das bis Stufe 2 des Gedaechtnisses selbst schrieb,
    wiederholte darauf denselben aussichtslosen Aufruf, statt mit
    `list_my_servers` nach der richtigen Nummer zu suchen. Die Pruefung sitzt
    in `scope_identity` und gilt damit fuer jeden Schreibweg.
    """
    from fastapi import HTTPException

    _allow(db, regular_user, "ai.memory.use", "server.view")

    with pytest.raises(HTTPException) as exc:
        ai_memory_service.erinnerung_anlegen(
            db, user=regular_user, scope="server", server_id=424242,
            text="Startet nur mit erhoehtem Timeout.",
        )
    # Die Diagnose entscheidet, was der Leser als naechstes tut.
    assert exc.value.status_code == 404
    assert "Server nicht gefunden" in str(exc.value.detail)
    assert "erneut versuchen" not in str(exc.value.detail)
    db.rollback()
    assert db.query(AiMemoryEntry).filter(
        AiMemoryEntry.server_id == 424242
    ).count() == 0


# ── Blättern ─────────────────────────────────────────────────────────


def _team_mit_wissen(db: Session, user: User, anzahl: int) -> Team:
    """Ein Team und `anzahl` Einträge darin, alle vom Gründer."""
    team = team_service.create_team(db, user=user, name="Betriebsteam")
    for nummer in range(anzahl):
        ai_memory_service.upsert_entry(
            db, user=user, scope="team", server_id=None, team_id=team.id,
            key=f"regel.{nummer:02d}", value=f"Betriebsregel Nummer {nummer}",
        )
    db.commit()
    return team


def test_die_teamansicht_blaettert_statt_alles_auf_einmal_zu_oeffnen(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dieselbe Zusage wie im Profil, für den zweiten Bereich, der wachsen darf.

    `panel` und `server_shared` hängen an der festen
    `MAX_SYSTEM_SCOPE_ENTRIES` und passen immer auf eine Seite. Ein Team hängt
    am Rollenlimit seines Gründers und darf seit dem 19.08.2026 bis zu 5.000
    Einträge fassen — und jeder davon kostet beim Öffnen einen eigenen
    HTTP-POST an den DIS-Sidecar. Gemessen sind das bei 5.000 Zeilen 10,3 s,
    also genau die Wartezeit, gegen die die Profilansicht längst geschützt
    ist. Über `list_entries` wäre die Teamansicht ungeschützt geblieben.

    Gezählt werden die Sidecar-Aufrufe und nicht nur die Zeilen der Antwort:
    eine kurze Liste bewiese sonst nur, dass wenig ankam — nicht, dass wenig
    geöffnet wurde.

    Die Seitengröße ist kleingesetzt. Die Zusage lautet "es wird nicht mehr
    entschlüsselt als gezeigt", und die gilt für jede Größe; mit den echten
    200 bräuchte der Test 205 Einträge und ein angehobenes Rollenlimit und
    prüfte dann zwei Dinge auf einmal.
    """
    from tests._entschluesselung import mitzaehlen

    _allow(db, regular_user, "ai.memory.use", "teams.create")
    team = _team_mit_wissen(db, regular_user, 5)
    monkeypatch.setattr(ai_memory_service, "PERSONAL_PAGE_SIZE", 3)
    zaehler = mitzaehlen(monkeypatch)

    erste = ai_memory_service.scope_entries(
        db, regular_user, "team", None, team.id
    )
    texte_erste, aufrufe_erste = zaehler.texte, zaehler.aufrufe
    zweite = ai_memory_service.scope_entries(
        db, regular_user, "team", None, team.id, offset=3
    )

    assert len(erste.eintraege) == 3
    assert erste.gesamt == 5
    # Genau ein Text je gezeigter Zeile — nicht je vorhandener —, in einem
    # Roundtrip.
    assert texte_erste == 3
    assert aufrufe_erste == 1

    # Zusammen genau die fünf, ohne Überlappung und ohne Lücke.
    assert len(zweite.eintraege) == 2
    schluessel = [row.key for row, _wert in erste.eintraege]
    schluessel += [row.key for row, _wert in zweite.eintraege]
    assert len(set(schluessel)) == 5


def test_eine_bereichsseite_meldet_alles_als_loeschbar(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Alle löschen" trifft hier wirklich alles — anders als im Profil.

    Die Bestätigungsfrage nennt diese Zahl, und sie darf nicht die Länge der
    angezeigten Seite sein: die Ansicht sieht drei von fünf. Im Profil ist
    `loeschbar` kleiner als `gesamt`, weil die Servernotizen in derselben Liste
    stehen und stehenbleiben; in einem Bereich räumt `delete_all_entries`
    genau die eine Kennung ab, die die Ansicht zeigt.
    """
    _allow(db, regular_user, "ai.memory.use", "teams.create")
    team = _team_mit_wissen(db, regular_user, 5)
    monkeypatch.setattr(ai_memory_service, "PERSONAL_PAGE_SIZE", 3)

    seite = ai_memory_service.scope_entries(db, regular_user, "team", None, team.id)

    assert len(seite.eintraege) == 3
    assert seite.loeschbar == seite.gesamt == 5
    assert ai_memory_service.delete_all_entries(
        db, regular_user, "team", None, team.id
    ) == 5


def test_die_bereichsseite_kommt_ueber_die_route_mit_ihren_drei_zahlen(
    client: TestClient,
    db: Session,
    regular_user: User,
    user_cookies: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der Weg, den die Oberfläche wirklich nimmt — einmal ganz durch.

    Die Zahlen daneben sind kein Beiwerk: aus `total` und `limit` rechnet die
    Ansicht ihre Seitenzahl und den nächsten Offset, und `clearable` steht in
    der Frage vor dem Leeren. Fehlt eine davon, blättert die Oberfläche ins
    Leere oder fragt nach der falschen Menge.

    Der negative Offset gehört dazu: er ist eine Abweisung und kein Rücklauf
    ins Nichts — dieselbe Zusage wie auf der Profilseite.
    """
    _allow(db, regular_user, "ai.memory.use", "teams.create")
    team = _team_mit_wissen(db, regular_user, 5)
    monkeypatch.setattr(ai_memory_service, "PERSONAL_PAGE_SIZE", 3)
    adresse = f"/api/ai/memory/page?scope=team&team_id={team.id}"

    erste = client.get(adresse, cookies=user_cookies)
    zweite = client.get(f"{adresse}&offset=3", cookies=user_cookies)
    negativ = client.get(f"{adresse}&offset=-1", cookies=user_cookies)

    assert erste.status_code == 200
    seite = erste.json()
    assert len(seite["entries"]) == 3
    assert seite["total"] == 5
    # Im Bereich räumt "Alle löschen" alles ab, anders als im Profil.
    assert seite["clearable"] == 5
    assert seite["limit"] == 3
    assert len(zweite.json()["entries"]) == 2
    assert negativ.status_code == 422


def test_eine_bereichsseite_bleibt_hinter_der_mitgliedschaft(
    db: Session, regular_user: User
) -> None:
    """Der neue Leseweg erbt die Grenze, er baut keine eigene.

    `scope_entries` fragt dieselbe `scope_identity` wie `list_entries`; ein
    Außenstehender bekommt deshalb dasselbe 404 wie dort — und zwar 404 und
    nicht 403, weil ihn die Existenz eines fremden Teams nichts angeht.
    """
    from fastapi import HTTPException

    fremder = _user(db, "fremder")
    _allow(db, fremder, "ai.memory.use")
    _allow(db, regular_user, "ai.memory.use", "teams.create")
    team = _team_mit_wissen(db, regular_user, 2)

    with pytest.raises(HTTPException) as fehler:
        ai_memory_service.scope_entries(db, fremder, "team", None, team.id)
    assert fehler.value.status_code == 404
