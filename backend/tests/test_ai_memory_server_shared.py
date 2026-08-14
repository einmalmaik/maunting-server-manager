"""Wissen, das der Anlage gehoert — und nicht dem, der es aufgeschrieben hat.

Der Anlass steht in einem Satz aus dem Betrieb: *"Bei diesem Server muss man
nach jedem Neustart die Whitelist neu laden, sonst kommt keiner rein."* Die KI
legte ihn im persoenlichen Gedaechtnis ab. Nicht falsch angewandt — eine
fehlende Schublade. Der Kollege, der morgen Dienst hat, findet ihn dort nie.

Was `server_shared` von den vier vorhandenen Bereichen unterscheidet, sind vier
Zusagen, und jede hat hier ihren Fall:

1. **Sehen darf, wer den Server sehen darf** (`server.view`) — aendern nur, wer
   an der Anlage etwas aendern darf (`server.config.write`).
2. **Es gehoert niemandem.** `owner_user_id` ist NULL; das Wissen ueberlebt das
   Konto seines Verfassers und verschwindet mit dem Server.
3. **Es haengt nicht am persoenlichen Einwilligungsschalter.** Der ist eine
   Entscheidung ueber das eigene Gedaechtnis, nicht ueber das der Kollegen.
4. **Es ist eine gemeinsame Kasse.** Die 100er-Grenze gilt je Server, nicht je
   Mensch.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import (
    AiMemoryEntry,
    Role,
    RolePermission,
    Server,
    ServerPermission,
    User,
)
from services import ai_memory_service
from services.auth_service import AuthService
from services.role_service import set_user_roles


def _user(db: Session, name: str) -> User:
    user = AuthService.create_user(db, name, f"{name}@test.de", "MemPass123!")
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return user


def _server(db: Session, name: str) -> Server:
    server = Server(
        name=name, game_type="dayz", install_dir=f"/tmp/{name}", status="stopped"
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


def _allow(
    db: Session, user: User, server: Server, *server_keys: str, memory: bool = True
) -> None:
    role = Role(name=f"rolle-{user.username}", description=None, is_system=False)
    db.add(role)
    db.flush()
    db.add(RolePermission(role_id=role.id, permission_key="ai.memory.use"))
    db.commit()
    set_user_roles(db, user, [role.id])
    for key in server_keys:
        db.add(ServerPermission(
            user_id=user.id, server_id=server.id, permission_key=key
        ))
    db.commit()
    ai_memory_service.set_preference(db, user, memory)


def _merken(db: Session, user: User, server: Server, key: str, value: str):
    return ai_memory_service.upsert_entry(
        db, user=user, scope="server_shared", server_id=server.id,
        key=key, value=value, origin="ai",
    )


def _kontext(
    db: Session, user: User, server: Server | None = None, query: str = ""
) -> str:
    """Der Kontext eines Laufs, der um genau diesen Server geht.

    Ohne Server ist es ein Lauf ohne Thema — dann kommt bewusst kein
    Anlagenwissen mit, und die Tests unten nutzen genau das als Gegenprobe.
    """
    block = ai_memory_service.provider_memory_context(
        db, user, query, server.id if server is not None else None
    )
    db.commit()
    return block or ""


# ── Die Schublade tut, wozu es sie gibt ───────────────────────────────


def test_the_note_reaches_the_colleague_who_never_wrote_it(
    db: Session, regular_user: User
) -> None:
    """Der eigentliche Zweck, in einem Fall.

    Genau das konnte das persoenliche Gedaechtnis nicht: der Satz stand da, aber
    nur fuer den, der ihn aufgeschrieben hatte. Der Kollege braucht ihn
    dringender — er war beim Vorfall nicht dabei.
    """
    server = _server(db, "whitelist")
    kollege = _user(db, "kollege")
    _allow(db, regular_user, server, "server.view", "server.config.write")
    _allow(db, kollege, server, "server.view")

    _merken(db, regular_user, server, "whitelist",
            "Nach jedem Neustart die Whitelist neu laden, sonst kommt keiner rein.")

    assert "Whitelist neu laden" in _kontext(
        db, kollege, server, "Warum kommt keiner rein?"
    )


def test_the_label_names_the_server(db: Session, regular_user: User) -> None:
    """Ohne Nummer wendet das Modell eine Eigenheit auf den falschen Server an.

    Und ohne die Unterscheidung zur persoenlichen Notiz waere nicht zu sehen, ob
    eine Zeile fuer alle gilt oder nur fuer einen selbst.
    """
    server = _server(db, "beschriftet")
    _allow(db, regular_user, server, "server.view", "server.config.write")

    _merken(db, regular_user, server, "port", "Aussen 30015, innen 27015.")

    assert f"[server:{server.id}:anlage/gemerkt]" in _kontext(db, regular_user, server)


def test_a_server_only_shows_its_own_knowledge(
    db: Session, regular_user: User
) -> None:
    """Zwei Anlagen, zwei Betriebsanleitungen — und keine Vermischung."""
    einer = _server(db, "eins")
    anderer = _server(db, "zwei")
    _allow(db, regular_user, einer, "server.view", "server.config.write")
    db.add_all([
        ServerPermission(user_id=regular_user.id, server_id=anderer.id,
                         permission_key=key)
        for key in ("server.view", "server.config.write")
    ])
    db.commit()

    _merken(db, regular_user, einer, "eigenheit", "Braucht zwei Minuten zum Start.")
    _merken(db, regular_user, anderer, "eigenheit", "Startet sofort.")

    eintraege = ai_memory_service.list_entries(
        db, regular_user, "server_shared", einer.id
    )

    assert [wert for _row, wert in eintraege] == ["Braucht zwei Minuten zum Start."]


def test_only_the_manual_of_the_server_in_question_enters_the_context(
    db: Session, regular_user: User
) -> None:
    """Der Bezug ist eine Verengung und keine Zierde.

    Ein Betreiber sieht leicht zwanzig Server. Kaeme das Wissen aller zwanzig
    gleichzeitig mit, waere das Budget von 6.000 Zeichen mit
    Betriebsanleitungen gefuellt, die mit der Frage nichts zu tun haben — die
    persoenlichen Vorlieben des Benutzers fielen als Erstes heraus, und das
    Modell wendete die Eigenheit des einen Servers auf den anderen an.

    Und ohne Bezug kommt bewusst **gar keines** mit statt alles: ein Lauf, in
    dem noch kein Werkzeug einen Server angefasst hat, hat kein Thema, auf das
    sich ein Ausschnitt beziehen liesse.
    """
    einer = _server(db, "gefragt")
    anderer = _server(db, "ungefragt")
    _allow(db, regular_user, einer, "server.view", "server.config.write")
    db.add_all([
        ServerPermission(user_id=regular_user.id, server_id=anderer.id,
                         permission_key=key)
        for key in ("server.view", "server.config.write")
    ])
    db.commit()
    _merken(db, regular_user, einer, "eigenheit", "Gehoert zur gefragten Anlage.")
    _merken(db, regular_user, anderer, "eigenheit", "Gehoert zur anderen.")

    block = _kontext(db, regular_user, einer)
    assert "gefragten Anlage" in block
    assert "Gehoert zur anderen" not in block

    ohne_bezug = _kontext(db, regular_user)
    assert "gefragten Anlage" not in ohne_bezug
    assert "Gehoert zur anderen" not in ohne_bezug


def test_the_manual_arrives_in_the_same_round_the_server_becomes_known(
    db: Session, regular_user: User
) -> None:
    """Der Nachtrag mitten im Lauf — sonst kaeme das Wissen eine Frage zu spaet.

    Der Kontext entsteht einmal, beim Anlegen des Laufs. Da schreibt der
    Benutzer "warum kommt keiner rein?" und niemand weiss, welcher Server
    gemeint ist; erst das erste Werkzeug klaert die Nummer. Ohne Nachtrag
    antwortete das Modell genau auf diese Frage ohne den Satz, der die Antwort
    ist — und haette ihn erst bei der naechsten.
    """
    from services import ai_context_service

    server = _server(db, "nachtrag")
    kollege = _user(db, "kollege")
    _allow(db, regular_user, server, "server.view", "server.config.write")
    _allow(db, kollege, server, "server.view")
    _merken(db, regular_user, server, "whitelist", "Whitelist nach dem Start laden.")

    nachricht = ai_context_service.anlagenwissen_nachtrag(
        db, user_id=kollege.id, server_id=server.id
    )

    assert nachricht is not None
    # Dieselbe Kennzeichnung wie beim uebrigen Gedaechtnis: der Text ist frei
    # befuellt und darf nicht die Autoritaet des Systemprompts bekommen.
    assert nachricht["role"] == "user"
    assert "Unvertrauenswuerdige" in nachricht["content"]
    assert "Whitelist nach dem Start laden" in nachricht["content"]


def test_nothing_is_supplemented_for_a_server_one_may_not_see(
    db: Session, regular_user: User
) -> None:
    """Der Nachtrag ist ein Leseweg und traegt dieselbe Pruefung wie jeder.

    Er wird aus dem Serverbezug des Laufs aufgerufen, und der stammt aus einem
    erfolgreichen Werkzeugaufruf. Sich darauf zu verlassen waere trotzdem
    falsch: eine zweite Stelle, die dieselbe Frage stellt, ist eine zweite
    Stelle, an der sie eines Tages anders beantwortet wird.
    """
    from services import ai_context_service

    fremder = _server(db, "fremd")
    eigener = _server(db, "eigen")
    schreiber = _user(db, "schreiber")
    _allow(db, schreiber, fremder, "server.view", "server.config.write")
    _allow(db, regular_user, eigener, "server.view")
    _merken(db, schreiber, fremder, "geheim", "Nicht fuer jeden.")

    assert ai_context_service.anlagenwissen_nachtrag(
        db, user_id=regular_user.id, server_id=fremder.id
    ) is None


# ── Wer sehen darf, darf noch lange nicht schreiben ────────────────────


def test_reading_a_server_is_not_enough_to_write_its_manual(
    db: Session, regular_user: User
) -> None:
    """Ein Gast schreibt keine Ansage, nach der sich alle anderen richten.

    Der Text der Absage ist Teil der Zusage: er nennt den Weg, der offensteht.
    Ohne ihn glaubt ein Benutzer — oder das Modell —, es gaebe gar keinen.
    """
    server = _server(db, "nurlesen")
    _allow(db, regular_user, server, "server.view")

    with pytest.raises(HTTPException) as fehler:
        _merken(db, regular_user, server, "eigenheit", "Wird nicht geschrieben.")

    assert fehler.value.status_code == 403
    assert "scope='server'" in str(fehler.value.detail)
    assert db.query(AiMemoryEntry).count() == 0


def test_clearing_the_area_needs_the_same_right_as_writing(
    db: Session, regular_user: User
) -> None:
    """Loeschen ist Schreiben.

    Diese Stelle hat der Vorlauf beinahe verloren: `delete_all_entries` reichte
    die **rohe** `team_id` an die Rechtepruefung weiter statt der aus
    `scope_identity`. Ein Bereich, dessen Kennung aus etwas anderem entsteht,
    waere damit ungeprueft durchgelaufen.
    """
    server = _server(db, "leeren")
    schreiber = _user(db, "schreiber")
    _allow(db, schreiber, server, "server.view", "server.config.write")
    _allow(db, regular_user, server, "server.view")
    _merken(db, schreiber, server, "bleibt", "Diese Zeile bleibt stehen.")

    with pytest.raises(HTTPException) as fehler:
        ai_memory_service.delete_all_entries(
            db, regular_user, "server_shared", server_id=server.id
        )

    assert fehler.value.status_code == 403
    assert db.query(AiMemoryEntry).count() == 1


def test_clearing_the_area_works_and_stops_at_the_server_border(
    db: Session, regular_user: User
) -> None:
    """Die Gegenprobe zum Test darueber — und die schaerfere von beiden.

    Eine Absage laesst sich versehentlich richtig hinbekommen: reicht der
    Aufrufer statt der aufgeloesten Servernummer gar keine weiter, weist die
    Rechtepruefung **jeden** ab, und ein Test, der nur 403 erwartet, bleibt
    gruen. Erst der Erfolgsweg zeigt, ob die richtige Nummer ankommt.

    Der Nachbarserver steht daneben, weil "alles geloescht" sonst genauso gruen
    waere wie "den richtigen Bereich geloescht".
    """
    server = _server(db, "geleert")
    nachbar = _server(db, "unberuehrt")
    _allow(db, regular_user, server, "server.view", "server.config.write")
    db.add_all([
        ServerPermission(user_id=regular_user.id, server_id=nachbar.id,
                         permission_key=key)
        for key in ("server.view", "server.config.write")
    ])
    db.commit()
    _merken(db, regular_user, server, "eins", "Verschwindet.")
    _merken(db, regular_user, server, "zwei", "Verschwindet auch.")
    _merken(db, regular_user, nachbar, "eins", "Bleibt stehen.")

    entfernt = ai_memory_service.delete_all_entries(
        db, regular_user, "server_shared", server_id=server.id
    )

    assert entfernt == 2
    uebrig = db.query(AiMemoryEntry).all()
    assert len(uebrig) == 1 and uebrig[0].server_id == nachbar.id


def test_the_row_check_holds_even_when_the_prefilter_lets_everything_through(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die zweite Verteidigungslinie, fuer sich allein gemessen.

    Der Vorfilter in der Abfrage ist eine Mengenbegrenzung; die Autoritaet ist
    die zeilenweise Nachpruefung darunter. Solange beide dieselbe Menge meinen,
    verdeckt der Vorfilter jeden Fehler in der Nachpruefung — sie bliebe
    ungeprueft, bis die beiden eines Tages auseinanderlaufen.

    `None` heisst fuer den Vorfilter "alle Server" und ist der Zustand eines
    Betreibers mit pauschalem `server.view`. Genau dort filtert die Abfrage gar
    nichts, und ab da entscheidet allein die Schleife.

    Das ist die Spiegelung des Tests in `test_ai_memory_recall.py`, der
    umgekehrt die Nachpruefung aushaengt, um den Vorfilter zu messen.
    """
    fremder = _server(db, "fremd")
    eigener = _server(db, "eigen")
    _allow(db, regular_user, eigener, "server.view", "server.config.write")
    schreiber = _user(db, "schreiber")
    _allow(db, schreiber, fremder, "server.view", "server.config.write")
    _merken(db, schreiber, fremder, "geheim", "Nicht fuer jeden.")
    _merken(db, regular_user, eigener, "offen", "Fuer mich schon.")

    monkeypatch.setattr(
        ai_memory_service.permission_service, "list_visible_server_ids",
        lambda *_args, **_kwargs: None,
    )
    rows = ai_memory_service._visible_scope_rows(db, regular_user)

    assert [row.server_id for row in rows if row.scope == "server_shared"] == [
        eigener.id
    ]


def test_a_shared_note_is_deletable_at_all(db: Session, regular_user: User) -> None:
    """Ohne eigenen Zweig waere sie fuer **niemanden** loeschbar.

    Der `else`-Zweig von `delete_entry` vergleicht `owner_user_id == user.id`.
    Anlagenwissen hat bewusst keinen Besitzer; gegen NULL ist der Vergleich
    immer False. Der Eintrag stuende fuer immer da, zaehlte gegen die Grenze des
    Servers und liesse sich ueber die Oberflaeche nicht mehr entfernen.
    """
    server = _server(db, "loeschbar")
    _allow(db, regular_user, server, "server.view", "server.config.write")
    row, _wert = _merken(db, regular_user, server, "weg", "Kommt wieder weg.")
    assert row.owner_user_id is None

    ai_memory_service.delete_entry(db, regular_user, row.id)

    assert db.query(AiMemoryEntry).count() == 0


def test_only_the_write_right_removes_a_single_shared_note(
    db: Session, regular_user: User
) -> None:
    """Und der eigene Zweig darf nicht zu weit sein.

    404 statt 403, wie ueberall beim einzelnen Loeschen: die Antwort soll nicht
    verraten, dass es die Zeile gibt.
    """
    server = _server(db, "fremdloeschen")
    schreiber = _user(db, "schreiber")
    _allow(db, schreiber, server, "server.view", "server.config.write")
    _allow(db, regular_user, server, "server.view")
    row, _wert = _merken(db, schreiber, server, "bleibt", "Bleibt stehen.")

    with pytest.raises(HTTPException) as fehler:
        ai_memory_service.delete_entry(db, regular_user, row.id)

    assert fehler.value.status_code == 404
    assert db.query(AiMemoryEntry).count() == 1


def test_an_invented_server_is_indistinguishable_from_a_hidden_one(
    db: Session, regular_user: User
) -> None:
    """Sonst waere die Fehlermeldung ein Existenzorakel ueber fremde Server."""
    fremder = _server(db, "fremd")
    eigener = _server(db, "eigen")
    _allow(db, regular_user, eigener, "server.view", "server.config.write")

    fehler = []
    for nummer in (fremder.id, 999_999):
        with pytest.raises(HTTPException) as ausnahme:
            ai_memory_service.upsert_entry(
                db, user=regular_user, scope="server_shared", server_id=nummer,
                key="k", value="v", origin="ai",
            )
        fehler.append((ausnahme.value.status_code, str(ausnahme.value.detail)))

    assert fehler[0] == fehler[1] == (404, "Server nicht gefunden")


# ── Das Wissen haengt am Server, nicht an Menschen ─────────────────────


def test_the_knowledge_outlives_the_colleague_who_wrote_it(
    db: Session, regular_user: User
) -> None:
    """Genau dafuer ist `owner_user_id` NULL.

    Mit einem Besitzer haette das `ondelete="CASCADE"` auf `users.id` die Zeile
    beim naechsten geloeschten Konto mitgenommen — und niemand haette gemerkt,
    dass die Betriebsanleitung fehlt, bis der Server nicht mehr hochkommt.
    """
    server = _server(db, "nachlass")
    schreiber = _user(db, "geht")
    _allow(db, schreiber, server, "server.view", "server.config.write")
    _allow(db, regular_user, server, "server.view")
    row, _wert = _merken(db, schreiber, server, "eigenheit",
                         "Ueberlebt seinen Verfasser.")
    assert row.owner_user_id is None

    # Ueber denselben Weg, den das Panel geht — nicht per `db.delete(user)`.
    # Ein Konto haengt an Zeilen, die bewusst kein `ondelete` tragen (Audit,
    # Sitzungen); die werden vorher aufgeloest. Ein Test, der anders loescht als
    # die Anwendung, sagt nichts ueber die Anwendung.
    AuthService.delete_account_atomically(db, db.get(User, schreiber.id))

    assert db.get(AiMemoryEntry, row.id) is not None
    assert "Ueberlebt seinen Verfasser" in _kontext(db, regular_user, server)


def test_the_knowledge_goes_when_the_server_goes(
    db: Session, regular_user: User
) -> None:
    """Und der Nachbar bleibt unberuehrt.

    Die Kaskade steht am Fremdschluessel; hier steht, was sie bedeutet. Ohne die
    zweite Anlage waere der Test auch mit einem Loeschen aller Zeilen gruen.
    """
    weg = _server(db, "weg")
    bleibt = _server(db, "bleibt")
    _allow(db, regular_user, weg, "server.view", "server.config.write")
    db.add_all([
        ServerPermission(user_id=regular_user.id, server_id=bleibt.id,
                         permission_key=key)
        for key in ("server.view", "server.config.write")
    ])
    db.commit()
    _merken(db, regular_user, weg, "eigenheit", "Verschwindet mit der Anlage.")
    _merken(db, regular_user, bleibt, "eigenheit", "Bleibt bestehen.")

    db.delete(db.get(Server, weg.id))
    db.commit()

    uebrig = db.query(AiMemoryEntry).all()
    assert len(uebrig) == 1
    assert uebrig[0].server_id == bleibt.id


def test_losing_sight_of_the_server_hides_its_knowledge(
    db: Session, regular_user: User
) -> None:
    """Der Entzug wirkt sofort und ohne Nachpflege.

    Geprueft wird beides: es faellt aus dem Kontext, und es laesst sich auch
    nicht mehr loeschen. Das zweite ist bewusst so — anders als bei der eigenen
    Notiz, die man behalten koennen soll. Fremdes Wissen wegzuraeumen, das man
    nicht mehr sehen darf, ist das Gegenteil davon.
    """
    server = _server(db, "entzogen")
    _allow(db, regular_user, server, "server.view", "server.config.write")
    row, _wert = _merken(db, regular_user, server, "eigenheit", "Nur mit Zugriff.")

    for recht in db.query(ServerPermission).filter(
        ServerPermission.user_id == regular_user.id
    ).all():
        db.delete(recht)
    db.commit()

    assert "Nur mit Zugriff" not in _kontext(db, regular_user, server)
    with pytest.raises(HTTPException) as fehler:
        ai_memory_service.delete_entry(db, regular_user, row.id)
    assert fehler.value.status_code == 404


# ── Der Weg der KI: merken, finden, vergessen ─────────────────────────


def _werkzeug(db: Session, user: User, name: str, **argumente):
    from services import ai_action_service

    return ai_action_service.execute_read_tool(
        db, user=user, tool_name=name, arguments=argumente
    )


def test_the_ai_can_fill_the_area_through_its_tool(
    db: Session, regular_user: User
) -> None:
    """Ohne diesen Weg waere der Bereich nur ueber die Oberflaeche erreichbar.

    Der Anlass war aber gerade, dass die KI den Satz selbst ablegt — sie hoert
    ihn im Gespraech, nicht der Betreiber in einem Formular.
    """
    server = _server(db, "werkzeug")
    _allow(db, regular_user, server, "server.view", "server.config.write")

    ergebnis = _werkzeug(
        db, regular_user, "remember", scope="server_shared", server_id=server.id,
        key="whitelist", value="Nach jedem Neustart die Whitelist neu laden.",
    )

    assert ergebnis["remembered"] is True
    assert ergebnis["scope"] == "server_shared"
    # Die Nummer geht zurueck ans Modell: sonst weiss es hinterher nicht, zu
    # welcher Anlage es gerade etwas abgelegt hat.
    assert ergebnis["server_id"] == server.id


def test_the_consent_switch_does_not_block_writing_the_manual_either(
    db: Session, regular_user: User
) -> None:
    """Die Schreibseite derselben Entscheidung.

    Beim persoenlichen Gedaechtnis ist der abgeschaltete Schalter ein Halt:
    dort wurde frueher im Hintergrund weitergeschrieben, waehrend die
    Oberflaeche "deaktiviert" meldete. Anlagenwissen ist kein persoenliches
    Gedaechtnis — es haengt an `server.config.write`, nicht an einem Schalter
    ueber die eigene Person.
    """
    server = _server(db, "schalteraus")
    _allow(db, regular_user, server, "server.view", "server.config.write",
           memory=False)

    ergebnis = _werkzeug(
        db, regular_user, "remember", scope="server_shared", server_id=server.id,
        key="eigenheit", value="Gilt weiterhin fuer alle.",
    )

    assert ergebnis["remembered"] is True

    # Zur Abgrenzung: die eigene Notiz zu derselben Anlage wird sehr wohl
    # angehalten — und zwar sichtbar, nicht still.
    persoenlich = _werkzeug(
        db, regular_user, "remember", scope="server", server_id=server.id,
        key="notiz", value="Nur fuer mich.",
    )
    assert persoenlich["remembered"] is False
    assert persoenlich["reason"] == "memory_disabled"


def test_the_ai_is_told_plainly_when_it_may_not_write(
    db: Session, regular_user: User
) -> None:
    """Klarer Fehlschlag statt stiller Herabstufung — der Unterschied zum Team.

    Beim Team ist "kein echtes Team vorhanden" ein Zustand des Panels, und
    persoenlich zu speichern ist enger als gewuenscht, also unbedenklich. Hier
    waere es umgekehrt gefaehrlich: der Benutzer glaubte, ein Kollege lese den
    Satz, und niemand tut es.
    """
    from services.ai_action_errors import AiActionValidationError

    server = _server(db, "nurlesend")
    _allow(db, regular_user, server, "server.view")

    with pytest.raises(AiActionValidationError) as fehler:
        _werkzeug(
            db, regular_user, "remember", scope="server_shared",
            server_id=server.id, key="k", value="Wird nicht geschrieben.",
        )

    assert "scope='server'" in str(fehler.value)
    assert db.query(AiMemoryEntry).count() == 0


def test_what_the_search_finds_the_ai_can_also_forget(
    db: Session, regular_user: User
) -> None:
    """Suchen und Loeschen muessen denselben Bereich meinen.

    `search_memory` hat serverbezogene Eintraege schon immer gefunden,
    `forget_memory` kannte sie nie: "vergiss die Notiz zu Server 62" endete in
    "Unbekannter Memory-Bereich". Fuer den Benutzer sah das aus wie eine
    Weigerung.

    Geprueft wird die Kette, nicht die Einzelteile: was die Suche liefert, geht
    unveraendert ins Loeschen. Deshalb muss die Suche die Servernummer
    mitgeben — ohne sie laesst sich der Bereich nicht ein zweites Mal
    aufloesen.
    """
    server = _server(db, "kette")
    _allow(db, regular_user, server, "server.view", "server.config.write")
    _merken(db, regular_user, server, "whitelist", "Whitelist nach dem Start laden.")

    treffer = _werkzeug(db, regular_user, "search_memory", query="Whitelist")
    gefunden = [
        eintrag for eintrag in treffer["results"]
        if eintrag["scope"] == "server_shared"
    ]
    assert len(gefunden) == 1
    assert gefunden[0]["server_id"] == server.id

    ergebnis = _werkzeug(
        db, regular_user, "forget_memory", scope=gefunden[0]["scope"],
        server_id=gefunden[0]["server_id"], keys=[gefunden[0]["key"]],
    )

    assert ergebnis["forgotten"] == ["whitelist"]
    assert db.query(AiMemoryEntry).count() == 0


def test_forgetting_needs_the_write_right_too(
    db: Session, regular_user: User
) -> None:
    """Ein Leserecht loescht nicht, was alle anderen brauchen."""
    from services.ai_action_errors import AiActionValidationError

    server = _server(db, "nichtvergessen")
    schreiber = _user(db, "schreiber")
    _allow(db, schreiber, server, "server.view", "server.config.write")
    _allow(db, regular_user, server, "server.view")
    _merken(db, schreiber, server, "bleibt", "Bleibt stehen.")

    with pytest.raises(AiActionValidationError):
        _werkzeug(
            db, regular_user, "forget_memory", scope="server_shared",
            server_id=server.id, keys=["bleibt"],
        )

    assert db.query(AiMemoryEntry).count() == 1


def test_the_shared_area_needs_a_server_number(
    db: Session, regular_user: User
) -> None:
    """Ohne Nummer gibt es keinen Bereich, in den geschrieben werden koennte.

    Das Modell soll den Fehlgriff als Auskunft bekommen und `list_my_servers`
    aufrufen — nicht einen Eintrag anlegen, der irgendwo landet.
    """
    from services.ai_action_errors import AiActionValidationError

    server = _server(db, "ohnenummer")
    _allow(db, regular_user, server, "server.view", "server.config.write")

    for argumente in (
        {"scope": "server_shared", "server_id": None},
        {"scope": "server_shared"},
    ):
        with pytest.raises(AiActionValidationError):
            _werkzeug(db, regular_user, "remember", key="k", value="v", **argumente)

    assert db.query(AiMemoryEntry).count() == 0


# ── Nicht am Einwilligungsschalter, und eine gemeinsame Kasse ──────────


def test_the_consent_switch_does_not_govern_the_machines_manual(
    db: Session, regular_user: User
) -> None:
    """Der Schalter ist eine Entscheidung ueber sich, nicht ueber die Kollegen.

    Dieselbe Ueberlegung wie bei `team` und `panel`. Wer sein persoenliches
    Gedaechtnis abschaltet, soll nicht nebenbei die Betriebsanleitung seines
    Servers verlieren: er hat sie nicht angelegt und kann sie nicht ersetzen.
    """
    server = _server(db, "ohneschalter")
    schreiber = _user(db, "schreiber")
    _allow(db, schreiber, server, "server.view", "server.config.write")
    _allow(db, regular_user, server, "server.view", memory=False)
    _merken(db, schreiber, server, "eigenheit", "Gilt fuer alle hier.")
    ai_memory_service.upsert_entry(
        db, user=schreiber, scope="user", server_id=None,
        key="privat", value="Nur fuer mich.", origin="ai",
    )

    block = _kontext(db, regular_user, server)

    assert "Gilt fuer alle hier" in block
    assert "Nur fuer mich" not in block


def test_the_shared_area_does_not_show_up_in_a_personal_profile(
    db: Session, regular_user: User
) -> None:
    """Die Profilseite zeigt, was einem gehoert. Das hier gehoert dem Server.

    Getragen wird die Trennung vom fehlenden Besitzer: `personal_entries`
    filtert auf `owner_user_id == user.id`. Bekaeme Anlagenwissen je einen
    Besitzer — etwa weil jemand "der Verfasser sollte doch dranstehen" fuer eine
    Verbesserung haelt —, stuende es unter "Meine Erinnerungen" und saehe aus
    wie etwas, das man selbst hinterlegt hat und allein aendern kann. Der Nachbar
    dieses Tests haelt dieselbe Eigenschaft von der anderen Seite fest: ohne
    Besitzer ueberlebt der Eintrag das Konto seines Verfassers.
    """
    server = _server(db, "nichtimprofil")
    _allow(db, regular_user, server, "server.view", "server.config.write")
    _merken(db, regular_user, server, "anlage", "Gehoert dem Server.")
    ai_memory_service.upsert_entry(
        db, user=regular_user, scope="server", server_id=server.id,
        key="notiz", value="Gehoert mir.", origin="ai",
    )

    eigene = ai_memory_service.personal_entries(db, regular_user)

    assert [wert for _row, wert in eigene] == ["Gehoert mir."]


def test_two_colleagues_share_one_key_instead_of_doubling_it(
    db: Session, regular_user: User
) -> None:
    """Eine gemeinsame Kasse heisst auch: ein gemeinsamer Schluesselraum.

    Der zweite Kollege korrigiert den Eintrag, statt einen zweiten mit demselben
    Namen danebenzustellen. Das ist der Sinn des Bereichs — es gibt genau eine
    Betriebsanleitung.
    """
    server = _server(db, "geteilt")
    zweiter = _user(db, "zweiter")
    _allow(db, regular_user, server, "server.view", "server.config.write")
    _allow(db, zweiter, server, "server.view", "server.config.write")

    _merken(db, regular_user, server, "port", "Aussen 30015.")
    _merken(db, zweiter, server, "port", "Aussen 30016, wurde umgestellt.")

    eintraege = ai_memory_service.list_entries(
        db, regular_user, "server_shared", server.id
    )
    assert [wert for _row, wert in eintraege] == ["Aussen 30016, wurde umgestellt."]


def test_the_area_fills_up_per_server_and_says_so(
    db: Session, regular_user: User
) -> None:
    """Die 100er-Grenze zaehlt je Anlage, nicht je Mensch.

    Das ist neu und bewusst angenommen: bisher fuellte jeder seine eigene Kasse.
    Hier kann ein Kollege sie fuer alle vollmachen. Ein Verdraengungsmechanismus
    existiert nicht — die Meldung ist deshalb das Einzige, was den naechsten
    davor bewahrt, den Fehler bei sich zu suchen.

    Und sie geht unverändert an das Modell weiter (`_execute_remember` reicht
    `str(exc.detail)` durch). Sie muss deshalb den offenen Weg nennen: ohne
    `search_memory` und `forget_memory` darin hört die KI für diesen Bereich
    schlicht auf zu lernen, obwohl beide Werkzeuge vor ihr liegen.
    """
    server = _server(db, "voll")
    nachbar = _server(db, "nachbar")
    _allow(db, regular_user, server, "server.view", "server.config.write")
    db.add_all([
        ServerPermission(user_id=regular_user.id, server_id=nachbar.id,
                         permission_key=key)
        for key in ("server.view", "server.config.write")
    ])
    db.commit()
    for nummer in range(ai_memory_service.MAX_ENTRIES_PER_SCOPE):
        _merken(db, regular_user, server, f"eigenheit{nummer}", f"Wert {nummer}")

    with pytest.raises(HTTPException) as fehler:
        _merken(db, regular_user, server, "einer_zuviel", "Passt nicht mehr.")
    assert fehler.value.status_code == 409
    assert "search_memory" in fehler.value.detail
    assert "forget_memory" in fehler.value.detail

    # Die Nachbaranlage hat ihre eigene Kasse und ist davon unberuehrt.
    _merken(db, regular_user, nachbar, "eigenheit", "Passt.")


# ── Einmal je Lauf, nicht einmal je Runde ─────────────────────────────


@pytest.mark.asyncio
async def test_the_manual_is_read_once_per_run_not_once_per_round(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Das Anlagenwissen gehört dem Lauf, nicht der Werkzeugrunde.

    Angehängt wurde es schon immer genau einmal — die Marke im Laufzustand
    entscheidet darüber. **Gelesen** wurde es aber in jeder Runde: der Nachtrag
    lief unbedingt, sobald ein Serverbezug feststand, und ab Runde zwei wanderte
    das Ergebnis in den Papierkorb. Ein Lauf mit acht Werkzeugrunden las damit
    achtmal das komplette sichtbare Gedächtnis, prüfte jede Zeile einzeln gegen
    die Rechte und entschlüsselte sie über den Sidecar.

    Der Nutzungszähler ist der bleibende Schaden davon, nicht die Rechenzeit:
    `server_shared_context` zählt bei jedem Aufruf hoch, und der Zähler
    entscheidet beim nächsten Engpass mit, was im Kontext bleibt. Achtmal
    gezählt für einmal gezeigt verschiebt das Gewicht zwischen Anlagenwissen,
    Teamwissen und persönlichen Vorlieben ohne jeden Grund.
    """
    from uuid import uuid4

    from models import AiConversation
    from services import ai_stream_service
    from services.openai_compatible_adapter import ProviderToolCall

    user = _user(db, "rundenzaehler")
    server = _server(db, "runden")
    _allow(db, user, server, "server.view", "server.config.write")
    eintrag_id = _merken(
        db, user, server, "whitelist", "Whitelist nach dem Start laden."
    )[0].id
    conversation = AiConversation(
        id=str(uuid4()), user_id=user.id, server_id=None, title="Runden"
    )
    db.add(conversation)
    db.commit()

    monkeypatch.setattr(
        ai_stream_service,
        "_werkzeug_ausfuehren",
        lambda _user_id, call: ({"lines": []}, None),
    )
    aufruf = ProviderToolCall(
        id="c1", name="read_server_logs", arguments={"server_id": server.id}
    )

    async def _runde(noetig: bool):
        _, _, nachtrag = await ai_stream_service._tool_followup_messages(
            user_id=user.id,
            conversation_id=conversation.id,
            tool_calls=[aufruf],
            anlagenwissen_noetig=noetig,
        )
        return nachtrag

    assert await _runde(True) is not None
    assert await _runde(False) is None

    db.expire_all()
    assert db.get(AiMemoryEntry, eintrag_id).use_count == 1


def test_the_permission_is_asked_once_per_server_not_once_per_row(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zehn Notizen zu **einem** Server sind eine Frage, nicht zehn.

    `has_server_permission` ist für ein Teammitglied kein billiger Aufruf:
    `direct_server_permission` fragt Rollen, Rollenrechte und Serverrechte ab,
    und schlägt das fehl, folgt ein Dreifach-Join über die Teams samt erneuter
    Prüfung je Gründer. Gecacht wird nichts. Die Antwort kann sich innerhalb
    eines Aufrufs aber nicht ändern — `db`, `user` und `key` sind konstant.

    Der Zwischenspeicher lebt ausdrücklich nur im Funktionsrumpf. Eine
    Rechteantwort, die eine Anfrage überlebt, wäre keine Optimierung mehr,
    sondern ein entzogenes Recht, das noch eine Weile weiterwirkt.
    """
    from services import permission_service

    user = _user(db, "vielenotizen")
    server = _server(db, "vielzeilen")
    _allow(db, user, server, "server.view", "server.config.write")
    for nummer in range(10):
        _merken(db, user, server, f"eigenheit{nummer}", f"Wert {nummer}")

    echt = permission_service.has_server_permission
    gefragt: list[int] = []

    def _zaehlen(**felder):
        gefragt.append(int(felder["server_id"]))
        return echt(**felder)

    monkeypatch.setattr(permission_service, "has_server_permission", _zaehlen)
    zeilen = ai_memory_service._visible_scope_rows(db, user)

    assert len([zeile for zeile in zeilen if zeile.scope == "server_shared"]) == 10
    assert gefragt == [server.id]
