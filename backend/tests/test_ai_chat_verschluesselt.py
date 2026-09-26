"""Der KI-Chat steht in der Datenbank nur verschluesselt.

Bis 26.09.2026 lagen Nachrichten, Titel, Zusammenfassungen, Werkzeugergebnisse
und Laufzustand im Klartext. Wer die Tabellen im PostgreSQL-Studio oder in
einem Dump ansah, las jedes Gespraech mit. Geprueft wird deshalb der Rohwert
in der Datenbank, am ORM vorbei, und nicht nur, dass der Code dasselbe
zurueckbekommt, was er hineingab.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from models import AiConversation, AiMessage, AiRun, AiToolResult, User
from services.dis_client import DisClient

GEHEIM = "Passwort vom Schwager: Sonnenblume42"


def _roh(db: Session, tabelle: str, spalte: str, zeilen_id: str) -> str:
    return db.execute(
        text(f"SELECT {spalte} FROM {tabelle} WHERE id = :id"), {"id": zeilen_id}
    ).scalar_one()


def _gespraech(db: Session, user: User) -> AiConversation:
    gespraech = AiConversation(id=str(uuid4()), user_id=user.id, title=GEHEIM, summary=GEHEIM)
    db.add(gespraech)
    db.commit()
    return gespraech


SPALTEN = [
    ("ai_conversations", "title"),
    ("ai_conversations", "summary"),
    ("ai_messages", "content"),
    ("ai_messages", "reasoning"),
    ("ai_messages", "question_json"),
    ("ai_messages", "sections_json"),
    ("ai_tool_results", "result_json"),
    ("ai_runs", "state_json"),
]


@pytest.mark.parametrize(("tabelle", "spalte"), SPALTEN)
def test_rohwert_ist_chiffrat(db: Session, regular_user: User, tabelle: str, spalte: str) -> None:
    gespraech = _gespraech(db, regular_user)
    zeilen = {
        "ai_conversations": gespraech,
        "ai_messages": AiMessage(
            id=str(uuid4()), conversation_id=gespraech.id, role="assistant",
            content=GEHEIM, reasoning=GEHEIM, question_json=GEHEIM, sections_json=GEHEIM,
        ),
        "ai_tool_results": AiToolResult(
            id=str(uuid4()), conversation_id=gespraech.id, tool_name="list_servers", result_json=GEHEIM,
        ),
        "ai_runs": AiRun(
            id=str(uuid4()), conversation_id=gespraech.id, user_id=regular_user.id, state_json=GEHEIM,
        ),
    }
    zeile = zeilen[tabelle]
    db.add(zeile)
    db.commit()

    roh = _roh(db, tabelle, spalte, zeile.id)
    assert GEHEIM not in roh
    assert DisClient.ist_verschluesselt(roh)
    # Und die AAD gehoert zur Spalte: ein Wert laesst sich nicht in eine
    # andere Spalte umhaengen.
    assert DisClient.decrypt(roh, aad=f"msm:ai:{tabelle}.{spalte}") == GEHEIM

    db.expire_all()
    assert getattr(db.get(type(zeile), zeile.id), spalte) == GEHEIM


def test_altbestand_bleibt_lesbar_und_wird_nachgezogen(db: Session, regular_user: User) -> None:
    from services.dis_altbestand import nachziehen

    gespraech = _gespraech(db, regular_user)
    nachricht = AiMessage(id=str(uuid4()), conversation_id=gespraech.id, role="user", content="neu")
    db.add(nachricht)
    db.commit()
    # So sieht eine Zeile von vor der Umstellung aus.
    db.execute(text("UPDATE ai_messages SET content = :k WHERE id = :id"), {"k": GEHEIM, "id": nachricht.id})
    db.commit()
    db.expire_all()
    assert db.get(AiMessage, nachricht.id).content == GEHEIM

    assert nachziehen(db) == {"ai_messages": 1}

    roh = _roh(db, "ai_messages", "content", nachricht.id)
    assert DisClient.ist_verschluesselt(roh)
    assert GEHEIM not in roh
    db.expire_all()
    assert db.get(AiMessage, nachricht.id).content == GEHEIM
    # Ein zweiter Lauf findet nichts mehr.
    assert nachziehen(db) == {}


def test_ein_zu_grosser_wert_haelt_den_rest_nicht_auf(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bis 27.09.2026 brach der ganze Nachzug am ersten Wert ab, den der
    Sidecar als zu gross ablehnte. Alles dahinter blieb Klartext, bei jedem
    Start aufs Neue."""
    from services.dis_altbestand import nachziehen
    from services.dis_client import DisZuGross

    riesig = "RIESIG " + GEHEIM
    gespraech = _gespraech(db, regular_user)
    zeilen = [
        AiMessage(id=str(uuid4()), conversation_id=gespraech.id, role="user", content="neu")
        for _ in range(3)
    ]
    db.add_all(zeilen)
    db.commit()
    db.execute(text("UPDATE ai_messages SET content = :k WHERE id = :id"), {"k": riesig, "id": zeilen[0].id})
    db.execute(text("UPDATE ai_messages SET content = :k WHERE id = :id"), {"k": GEHEIM, "id": zeilen[1].id})
    db.execute(text("UPDATE ai_messages SET reasoning = :k WHERE id = :id"), {"k": GEHEIM, "id": zeilen[2].id})
    db.commit()

    echt = DisClient.encrypt

    def encrypt(klartext, aad=None):
        if klartext == riesig:
            raise DisZuGross("zu gross")
        return echt(klartext, aad=aad)

    monkeypatch.setattr(DisClient, "encrypt", staticmethod(encrypt))

    assert nachziehen(db) == {"ai_messages": 2}
    assert DisClient.ist_verschluesselt(_roh(db, "ai_messages", "content", zeilen[1].id))
    assert DisClient.ist_verschluesselt(_roh(db, "ai_messages", "reasoning", zeilen[2].id))
    assert _roh(db, "ai_messages", "content", zeilen[0].id) == riesig
    # Der zweite Lauf kreist nicht um den einen Wert.
    assert nachziehen(db) == {}


def test_nachzug_ueberschreibt_keine_gleichzeitige_aenderung(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Laufzustand eines Laufs aendert sich staendig. Schreibt das Panel
    ihn, waehrend der Nachzug ihn gerade verschluesselt, gewinnt das Panel."""
    from services import dis_altbestand

    gespraech = _gespraech(db, regular_user)
    lauf = AiRun(id=str(uuid4()), conversation_id=gespraech.id, user_id=regular_user.id, state_json="{}")
    db.add(lauf)
    db.commit()
    db.execute(text("UPDATE ai_runs SET state_json = 'alt' WHERE id = :id"), {"id": lauf.id})
    db.commit()

    echt = DisClient.encrypt

    def mit_zwischenschritt(klartext: str, aad: str | None = None) -> str:
        if klartext == "alt":
            db.execute(
                text("UPDATE ai_runs SET state_json = :neu WHERE id = :id"),
                {"neu": echt('{"schritt": 2}', aad="msm:ai:ai_runs.state_json"), "id": lauf.id},
            )
        return echt(klartext, aad=aad)

    monkeypatch.setattr(DisClient, "encrypt", staticmethod(mit_zwischenschritt))
    dis_altbestand.nachziehen(db)
    monkeypatch.setattr(DisClient, "encrypt", staticmethod(echt))

    db.expire_all()
    assert db.get(AiRun, lauf.id).state_json == '{"schritt": 2}'


def test_vergleich_verschluesselt_den_vergleichswert_nicht(db: Session, regular_user: User) -> None:
    """Ohne ``coerce_compared_value`` wuerde SQLAlchemy das Muster eines
    Filters mitverschluesseln, und kein Filter auf eine DisText-Spalte traefe."""
    gespraech = _gespraech(db, regular_user)
    treffer = (
        db.query(AiConversation.id)
        .filter(AiConversation.title.like(DisClient.PRAEFIX + "%"), AiConversation.id == gespraech.id)
        .all()
    )
    assert treffer == [(gespraech.id,)]


# ── Stufe 3: was die KI ausserhalb des Chats aufschreibt ────────────────────

#: Jede Spalte, in der Text eines Menschen oder des Modells steht. Neben den
#: Chatspalten oben: Mail-Korb (auch ein Guardian-Bericht nennt Server und
#: Stoerungen), Aufgaben, Meldungen der Worker, Guardian-Erkenntnisse,
#: Begruendung und Vorschau eines Vorschlags und der Dateiname eines Anhangs.
INHALTSSPALTEN = SPALTEN + [
    ("ai_mail_outbox", "betreff"),
    ("ai_mail_outbox", "text_body"),
    ("ai_mail_outbox", "html_body"),
    ("ai_mail_outbox", "fakten"),
    ("ai_mail_outbox", "rahmen_json"),
    ("ai_mail_outbox", "letzter_fehler"),
    ("ai_tasks", "title"),
    ("ai_tasks", "instruction"),
    ("ai_meldungen", "text"),
    ("ai_meldungen", "question_json"),
    ("ai_guardian_repairs", "erkenntnisse"),
    ("ai_action_proposals", "preview_json"),
    ("ai_action_proposals", "reason"),
    ("ai_action_proposals", "expected_effect"),
    ("ai_attachments", "original_name"),
]


@pytest.mark.parametrize(("tabelle", "spalte"), INHALTSSPALTEN)
def test_jede_inhaltsspalte_ist_dis_text(tabelle: str, spalte: str) -> None:
    from database import Base
    from models.dis_text import DisText

    typ = Base.metadata.tables[tabelle].c[spalte].type
    assert isinstance(typ, DisText)
    assert typ.aad == f"msm:ai:{tabelle}.{spalte}"


def test_mail_im_korb_ist_chiffrat(db: Session, regular_user: User) -> None:
    from services import ai_mail

    kennung = ai_mail.einreihen(
        db, user_id=regular_user.id, anlass="ai-guardian-report",
        betreff=GEHEIM, text=GEHEIM, html=f"<p>{GEHEIM}</p>", fakten=GEHEIM,
        rahmen={"titel": GEHEIM},
    )
    assert kennung is not None
    for spalte in ("betreff", "text_body", "html_body", "fakten", "rahmen_json"):
        roh = _roh(db, "ai_mail_outbox", spalte, kennung)
        assert GEHEIM not in roh, spalte
        assert DisClient.ist_verschluesselt(roh), spalte


def test_aufgabe_ist_chiffrat(db: Session) -> None:
    from services import ai_task_service
    from tests.test_ai_task_bericht import _benutzer

    aufgabe = ai_task_service.anlegen(
        db, user=_benutzer(db, "aufgabenschreiber"),
        felder={
            "title": "Schwager", "instruction": GEHEIM, "kind": "report",
            "plan_kind": "daily", "time_of_day": "08:00",
            "timezone": "Europe/Berlin", "channel": "chat",
        },
    )
    db.commit()
    for spalte in ("title", "instruction"):
        roh = _roh(db, "ai_tasks", spalte, aufgabe.id)
        assert "Schwager" not in roh and GEHEIM not in roh, spalte
        assert DisClient.ist_verschluesselt(roh), spalte


# ── VACUUM FULL nach dem Nachzug ────────────────────────────────────────────


def _vacuum_mitschreiben(monkeypatch: pytest.MonkeyPatch, *, gelingt: bool = True) -> list[set[str]]:
    from services import dis_altbestand

    aufrufe: list[set[str]] = []

    def mitschreiben(engine, tabellen):
        aufrufe.append(set(tabellen))
        return gelingt

    monkeypatch.setattr(dis_altbestand, "klartextreste_entfernen", mitschreiben)
    return aufrufe


def test_der_erste_start_raeumt_jede_betroffene_tabelle(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auch Tabellen ohne neuen Nachzug: dort liegen geloeschte Mails und
    entfernte Spalten noch in den Dateien."""
    from services import dis_altbestand

    aufrufe = _vacuum_mitschreiben(monkeypatch)
    dis_altbestand.beim_start(db)

    assert len(aufrufe) == 1
    assert {"ai_messages", "ai_mail_outbox", "ai_memory_entries", "ai_tasks"} <= aufrufe[0]

    # Danach nur noch, wo der Nachzug wieder Klartext gefunden hat.
    dis_altbestand.beim_start(db)
    assert aufrufe[1] == set()


def test_ein_gescheitertes_vacuum_wird_beim_naechsten_start_wiederholt(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services import dis_altbestand

    aufrufe = _vacuum_mitschreiben(monkeypatch, gelingt=False)
    dis_altbestand.beim_start(db)
    dis_altbestand.beim_start(db)

    assert "ai_messages" in aufrufe[1]


def test_neuer_klartext_nach_dem_ersten_start_wird_wieder_geraeumt(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Etwa nach dem Einspielen eines alten Backups."""
    from services import dis_altbestand

    aufrufe = _vacuum_mitschreiben(monkeypatch)
    dis_altbestand.beim_start(db)
    gespraech = _gespraech(db, regular_user)
    db.execute(text("UPDATE ai_conversations SET summary = 'alt' WHERE id = :id"), {"id": gespraech.id})
    db.commit()

    dis_altbestand.beim_start(db)

    assert aufrufe[1] == {"ai_conversations"}


def test_vacuum_nur_auf_postgres() -> None:
    from sqlalchemy import create_engine

    from services.dis_altbestand import klartextreste_entfernen

    assert klartextreste_entfernen(create_engine("sqlite://"), {"ai_messages"}) is True


def test_der_client_erkennt_die_grenze_des_sidecars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Der Sidecar meldete Zu-gross bis 27.09.2026 als "invalid json"."""
    import httpx

    from services import dis_client as dis_client_modul
    from services.dis_client import DisZuGross

    class Antwort413:
        def post(self, url, json=None, headers=None):
            return httpx.Response(413, json={"error": "PayloadTooLarge"})

    monkeypatch.setattr(dis_client_modul, "_client", Antwort413())
    with pytest.raises(DisZuGross):
        dis_client_modul.DisClient._post("/encrypt", {"plaintext": "x"})


def test_ein_alter_sidecar_ohne_praefix_scheitert_laut(monkeypatch: pytest.MonkeyPatch) -> None:
    """Am 27.09.2026 lief das neue Panel kurz gegen den alten Sidecar. Der
    lieferte Chiffrat ohne Praefix, der Nachzug hielt es fuer Klartext und
    verschluesselte es erneut, rund 40 Schichten tief, bis ein Titel 9 MB
    hatte."""
    import httpx

    from services import dis_client as dis_client_modul
    from services.dis_client import DisSidecarError
    from tests.conftest import ECHTES_ENCRYPT

    class AlterSidecar:
        def post(self, url, json=None, headers=None):
            return httpx.Response(200, json={"ciphertext": "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB"})

    monkeypatch.setattr(dis_client_modul, "_client", AlterSidecar())
    with pytest.raises(DisSidecarError, match="msm-dis-v1"):
        ECHTES_ENCRYPT("Titel", aad="msm:ai:ai_conversations.title")


def test_nackter_dis_wert_wird_ausgepackt_statt_eingepackt(
    db: Session, regular_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Was das Panel mit dem alten Sidecar schrieb, ist DIS-Chiffrat ohne
    Praefix. Nochmals verschluesselt stuende im Chat danach Base64."""
    import base64
    import os

    from services.dis_altbestand import nachziehen
    from services.dis_client import DisDecryptionError

    nackt = base64.b64encode(os.urandom(48)).decode()
    gespraech = _gespraech(db, regular_user)
    nachricht = AiMessage(id=str(uuid4()), conversation_id=gespraech.id, role="user", content="neu")
    db.add(nachricht)
    db.commit()
    db.execute(text("UPDATE ai_messages SET content = :k WHERE id = :id"), {"k": nackt, "id": nachricht.id})
    db.commit()

    echt = DisClient.decrypt

    def decrypt(chiffrat, aad=None):
        if chiffrat == nackt:
            assert aad == "msm:ai:ai_messages.content"
            return GEHEIM
        if not DisClient.ist_verschluesselt(chiffrat):
            raise DisDecryptionError("kein Chiffrat")
        return echt(chiffrat, aad=aad)

    monkeypatch.setattr(DisClient, "decrypt", staticmethod(decrypt))

    assert nachziehen(db) == {"ai_messages": 1}
    db.expire_all()
    assert db.get(AiMessage, nachricht.id).content == GEHEIM
