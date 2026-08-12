"""Geloescht wird nur, was sich zurueckholen laesst.

`propose_file_delete` traegt in der Werkzeugtabelle **keine**
Bestaetigungspflicht, und begruendet wird das mit genau einem Satz: der
Versionsschnappschuss aus `file_history_service` holt die Datei zurueck. Damit
ist der Schnappschuss die **Vorbedingung** des Loeschens und nicht sein Beiwerk.
Eine Vorbedingung, die nur behauptet wird, ist aber keine — und behauptet war
sie:

* `file_history_service.snapshot` gibt oberhalb von `MAX_HISTORY_EDIT_SIZE`
  (512 KiB) **stillschweigend `False`** zurueck, statt zu werfen, und
  `delete_server_text` verwarf den Rueckgabewert. Eine zwei Megabyte grosse
  Regionsdatei lief also durch `read_server_text` (das bis 5 MiB durchlaesst),
  erzeugte keinen Schnappschuss und wurde geloescht. Kein Fehler, keine Spur,
  kein Weg zurueck.
* Binaere Dateien nahmen denselben Weg mit einem schlimmeren Ende: der
  Dateizugriff dekodiert mit ``errors="replace"``, ein Schnappschuss davon
  besteht aus U+FFFD statt aus den Originalbytes. Er sieht in der Oberflaeche
  aus wie ein Rueckweg, und wer ihn benutzt, schreibt eine zerstoerte Datei
  zurueck. Gar kein Rueckweg ist ehrlicher als ein kaputter: ohne ihn bleibt die
  Datei liegen, mit ihm ist das Original endgueltig weg.

Deshalb steht `_rueckweg_sichern` in **beiden** Pfaden von
`delete_server_text` — lokal wie ueber den Node-Agenten. Ein Server auf einer
entfernten Node ist derselbe Server; eine Zusage, die nur fuer lokale Nodes
gilt, ist keine.

Ein zweites Tor sitzt schon im Vorschlag (`_file_delete_payload`). Es ist keine
doppelte Pruefung um ihrer selbst willen: das Modell soll eine Antwort bekommen,
mit der es weiterarbeiten kann, statt einen Vorschlag, der im Chat steht und
beim Klick scheitert — und in einer unbeaufsichtigten Heilung klickt niemand,
dort waere der Vorschlag schlicht das Ende des Weges.

Zwei Gewohnheiten ziehen sich durch diese Datei:

1. Nach **jedem** abgewiesenen Loeschen wird nachgesehen, ob die Datei noch da
   ist. Ein Test, der nur den Statuscode liest, bliebe gruen, wenn die Datei
   zusaetzlich verschwaende — und genau das war der Fehler.
2. Nach **jedem** gelungenen Loeschen wird die Version wirklich abgerufen. Dass
   `snapshot` gerufen wurde, sagt nichts; der Rueckweg ist erst einer, wenn der
   Inhalt zurueckkommt.

Geprueft wird durchgehend der `code` im Fehlerrumpf, nie der Meldungstext: der
Code ist Teil der Schnittstelle zu Oberflaeche und Modell, der Text ist es
nicht.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from models import Server, User
from services import ai_proposal_service, file_history_service, server_file_access_service
from services.ai_action_errors import AiActionValidationError
from services.file_edit_service import content_revision
from services.file_history_service import MAX_HISTORY_EDIT_SIZE


#: Eine Textdatei, wie sie in jedem Serververzeichnis liegt. Klein genug fuer
#: den Schnappschuss, gross genug, dass ihr Verlust wehtut.
KONFIG = "port=2302\nmaxPlayers=40\npassword=synthetic\n"

#: Bytes, die keine Textdatei sein koennen. Das Nullbyte reicht `is_binary_text`
#: allein aus — es braucht die U+FFFD-Quote gar nicht erst.
BINAER = b"PK\x03\x04\x14\x00\x00\x00\x08\x00" + bytes(range(256)) * 4


@pytest.fixture
def versionsspeicher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Der Versionsspeicher liegt im Test unter `tmp_path`, nicht unter /opt/msm.

    Ohne diese Umlenkung schriebe die Testsuite in das Konfigurationsverzeichnis
    der Anlage — und die Zusicherungen dieser Datei haengen daran, dass sie
    danach **nachsehen** kann, was dort steht.
    """
    panel = tmp_path / "panel"
    monkeypatch.setattr(file_history_service.settings, "panel_config_dir", str(panel))
    return panel / ".msm-file-history"


@pytest.fixture
def datei_server(db: Session, tmp_path: Path) -> Server:
    """Ein Server mit echtem Verzeichnis — ohne Node, also lokaler Pfad.

    `resolve_server_node` gibt ohne `node_id` `None` zurueck, `_agent` damit
    ebenfalls; `delete_server_text` nimmt den lokalen Zweig. Der Agentenzweig
    wird in den Tests weiter unten ausdruecklich hergestellt.
    """
    verzeichnis = tmp_path / "srv"
    verzeichnis.mkdir()
    server = Server(
        name="Loeschserver",
        game_type="dayz",
        install_dir=str(verzeichnis),
        container_name=f"msm-del-{uuid4().hex[:8]}",
        status="stopped",
    )
    db.add(server)
    db.commit()
    db.refresh(server)
    return server


class _Agent:
    """Ein Node-Agent, der nur das kann, was `delete_server_text` von ihm ruft.

    Er zaehlt mit, ob `files_delete` an die Reihe kam. Das ist die eigentliche
    Frage im Agentenzweig: dort liegt die Datei auf einer fremden Maschine, und
    ob sie noch existiert, kann der Test nur ueber den ausgebliebenen Aufruf
    feststellen.
    """

    def __init__(self, inhalt: str, *, revision: str | None = None) -> None:
        self.inhalt = inhalt
        self.revision = revision or content_revision(inhalt.encode("utf-8"))
        self.gelesen: list[tuple[str, str]] = []
        self.geloescht: list[tuple[str, str]] = []

    def files_read_info(self, key: str, pfad: str) -> dict:
        self.gelesen.append((str(key), pfad))
        return {"content": self.inhalt, "revision": self.revision, "size": len(self.inhalt)}

    def files_delete(self, key: str, pfad: str) -> dict:
        self.geloescht.append((str(key), pfad))
        return {"deleted": True}


def _agent_einsetzen(
    monkeypatch: pytest.MonkeyPatch, agent: _Agent
) -> _Agent:
    """Stellt den entfernten Pfad her, ohne eine Node-Zeile zu erfinden.

    `_agent` ist die eine Weiche zwischen lokalem Verzeichnis und Node-Agent.
    Sie hier zu ersetzen ist genauer als eine Node in die Datenbank zu legen:
    der Test sagt damit "dieser Server liegt entfernt" und nichts sonst.
    """
    monkeypatch.setattr(server_file_access_service, "_agent", lambda server, db: agent)
    return agent


def _schreiben(ziel: Path, inhalt: str) -> None:
    """Legt genau die Bytes ab, die dastehen sollen.

    Nicht `write_text`: das uebersetzt Zeilenenden nach Plattformgewohnheit, und
    die Revision ist ein Hash ueber die Bytes der Datei. Ein Test, der seine
    Erwartung aus dem String bildet und die Datei ueber `write_text` anlegt,
    waere je nach Betriebssystem gruen oder rot — und die Zusage, um die es
    geht, haette damit nichts zu tun.
    """
    ziel.write_bytes(inhalt.encode("utf-8"))


def _lesen(ziel: Path) -> str:
    return ziel.read_bytes().decode("utf-8")


def _loeschen(
    db: Session, user: User, server: Server, pfad: str, revision: str | None = None
) -> dict:
    return server_file_access_service.delete_server_text(
        db, user=user, server_id=server.id, relative_path=pfad, expected_revision=revision
    )


def _versionen(server: Server, pfad: str) -> list[dict]:
    return file_history_service.list_versions(server.id, pfad)


# ── Der gelungene Fall: die Datei geht, der Rueckweg bleibt ───────────────


def test_textdatei_wird_geloescht_und_die_version_ist_danach_abrufbar(
    db: Session, regular_user: User, datei_server: Server, versionsspeicher: Path
) -> None:
    """Der Rueckweg wird abgerufen, nicht nur behauptet.

    Ein Test, der bloss nachweist, dass `snapshot` gerufen wurde, belegt genau
    das, was hier kaputt war: gerufen wurde es vorher auch, es gab nur `False`
    zurueck. Deshalb geht dieser Test den Weg zu Ende und liest den Inhalt aus
    dem Versionsspeicher zurueck — Zeichen fuer Zeichen derselbe.
    """
    ziel = Path(datei_server.install_dir) / "server.cfg"
    _schreiben(ziel, KONFIG)

    ergebnis = _loeschen(db, regular_user, datei_server, "server.cfg")

    assert ergebnis == {"path": "server.cfg", "deleted": True}
    assert not ziel.exists()
    versionen = _versionen(datei_server, "server.cfg")
    assert len(versionen) == 1
    zurueck = file_history_service.read_version(
        datei_server.id, "server.cfg", versionen[0]["id"]
    )
    assert zurueck["content"] == KONFIG
    # Wer geloescht hat, steht im Schnappschuss. Ohne den Akteur waere die
    # Version ein Fundstueck ohne Herkunft.
    assert versionen[0]["actor_id"] == regular_user.id


# ── Zu gross: der Kern des Befundes ──────────────────────────────────────


def test_datei_ueber_der_schnappschussgrenze_bleibt_liegen(
    db: Session, regular_user: User, datei_server: Server, versionsspeicher: Path
) -> None:
    """Der eigentliche Fehler: `snapshot` sagte `False`, und die Datei war weg.

    `read_server_text` laesst bis 5 MiB durch, der Versionsspeicher nimmt 512 KiB
    — dazwischen liegt jede Regions-, Welt- und Spielstandsdatei, also genau die
    Dateien, deren Verlust nicht zu reparieren ist. Vorher verschwand so eine
    Datei ohne Fehlermeldung und ohne Schnappschuss.

    Zwei Zusicherungen, und beide sind noetig: der Statuscode allein wuerde auch
    dann stimmen, wenn die Datei zusaetzlich geloescht worden waere.
    """
    ziel = Path(datei_server.install_dir) / "welt.map"
    inhalt = "A" * (MAX_HISTORY_EDIT_SIZE + 1)
    _schreiben(ziel, inhalt)

    with pytest.raises(HTTPException) as fehler:
        _loeschen(db, regular_user, datei_server, "welt.map")

    assert fehler.value.status_code == 413
    assert fehler.value.detail["code"] == "FILE_TOO_LARGE_FOR_HISTORY"
    assert ziel.is_file()
    assert _lesen(ziel) == inhalt
    # Kein halber Schnappschuss: was nicht ganz gesichert werden kann, wird gar
    # nicht gesichert, sonst stuende im Verlauf eine Version, die nicht die
    # Datei ist.
    assert _versionen(datei_server, "welt.map") == []


def test_genau_auf_der_grenze_wird_noch_geloescht(
    db: Session, regular_user: User, datei_server: Server, versionsspeicher: Path
) -> None:
    """Die Gegenprobe zur Grenze — sonst waere auch eine Totalsperre gruen.

    `snapshot` vergleicht mit ``>``, nicht mit ``>=``. Eine Datei von exakt
    `MAX_HISTORY_EDIT_SIZE` Bytes ist damit noch sicherbar und muss geloescht
    werden koennen. Ohne diesen Test wuerde ein zu strenges `_rueckweg_sichern`
    — eines, das jede Loeschung verweigert — von der Testsuite gelobt.
    """
    ziel = Path(datei_server.install_dir) / "rand.cfg"
    inhalt = "B" * MAX_HISTORY_EDIT_SIZE
    _schreiben(ziel, inhalt)

    ergebnis = _loeschen(db, regular_user, datei_server, "rand.cfg")

    assert ergebnis["deleted"] is True
    assert not ziel.exists()
    versionen = _versionen(datei_server, "rand.cfg")
    assert len(versionen) == 1
    assert (
        file_history_service.read_version(
            datei_server.id, "rand.cfg", versionen[0]["id"]
        )["content"]
        == inhalt
    )


# ── Binaer: ein kaputter Rueckweg ist schlimmer als keiner ───────────────


def test_binaerdatei_bleibt_liegen(
    db: Session, regular_user: User, datei_server: Server, versionsspeicher: Path
) -> None:
    """Ein mit U+FFFD durchsetzter Schnappschuss ist kein Rueckweg, sondern eine Falle.

    `file_edit_service.read_text` dekodiert mit ``errors="replace"``. Was der
    Versionsspeicher von einer `.zip`, einer `.jar` oder einem Spielstand
    bekaeme, waeren nicht die Originalbytes, sondern deren Ersatzzeichen — jede
    nicht dekodierbare Stelle unwiederbringlich durch U+FFFD ersetzt.

    Der Unterschied zu "kein Schnappschuss" ist der ganze Punkt: ohne
    Schnappschuss bleibt die Datei liegen und ist noch da. Mit einem solchen
    Schnappschuss ist die Datei geloescht, im Verlauf steht eine Version, die
    aussieht wie ein Rueckweg, und wer sie wiederherstellt, schreibt eine
    kaputte Datei an die Stelle der echten. Aus dem Ersatzzeichen kommt kein
    Byte zurueck.

    Der Schreibpfad weist binaeren Inhalt aus genau diesem Grund seit jeher ab;
    das Loeschen tut es jetzt auch.
    """
    ziel = Path(datei_server.install_dir) / "mods" / "plugin.jar"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_bytes(BINAER)

    with pytest.raises(HTTPException) as fehler:
        _loeschen(db, regular_user, datei_server, "mods/plugin.jar")

    assert fehler.value.status_code == 422
    assert fehler.value.detail["code"] == "FILE_BINARY_NO_HISTORY"
    assert ziel.read_bytes() == BINAER
    assert _versionen(datei_server, "mods/plugin.jar") == []


def test_ohne_versionsspeicher_wird_nicht_geloescht(
    db: Session,
    regular_user: User,
    datei_server: Server,
    versionsspeicher: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der dritte Weg in dieselbe Luecke: der Speicher selbst faellt aus.

    `snapshot` verschluesselt ueber DIS und faellt bewusst nie auf Klartext
    zurueck — ist der Dienst weg, fliegt ein `RuntimeError` beziehungsweise ein
    `DisSidecarError`. Auch dann gilt die Vorbedingung: erst der Rueckweg, dann
    die Loeschung. 503 sagt "spaeter noch einmal", und die Datei ist noch da,
    wenn es soweit ist.
    """
    ziel = Path(datei_server.install_dir) / "server.cfg"
    _schreiben(ziel, KONFIG)

    def kaputt(*_args, **_kwargs) -> bool:
        raise RuntimeError("Versionsspeicher synthetisch ausgefallen")

    monkeypatch.setattr(file_history_service, "snapshot", kaputt)

    with pytest.raises(HTTPException) as fehler:
        _loeschen(db, regular_user, datei_server, "server.cfg")

    assert fehler.value.status_code == 503
    assert _lesen(ziel) == KONFIG


# ── Derselbe Server, nur entfernt ────────────────────────────────────────


def test_agentenpfad_loescht_und_legt_denselben_schnappschuss_an(
    db: Session,
    regular_user: User,
    datei_server: Server,
    versionsspeicher: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der Rueckweg entsteht im Panel, nicht auf der Node.

    Der Versionsspeicher liegt beim Panel; der Agent liefert nur den Inhalt. Ob
    ein Server lokal oder entfernt laeuft, darf am Rueckweg deshalb nichts
    aendern — und das ist hier die Gegenprobe zu den beiden Abweisungen
    darunter.
    """
    agent = _agent_einsetzen(monkeypatch, _Agent(KONFIG))

    ergebnis = _loeschen(db, regular_user, datei_server, "server.cfg")

    assert ergebnis == {"path": "server.cfg", "deleted": True}
    assert agent.geloescht == [(server_file_access_service._agent_key(datei_server), "server.cfg")]
    versionen = _versionen(datei_server, "server.cfg")
    assert len(versionen) == 1
    assert (
        file_history_service.read_version(
            datei_server.id, "server.cfg", versionen[0]["id"]
        )["content"]
        == KONFIG
    )


def test_agentenpfad_loescht_keine_zu_grosse_datei(
    db: Session,
    regular_user: User,
    datei_server: Server,
    versionsspeicher: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auf der entfernten Node kann der Test nur den ausgebliebenen Aufruf pruefen.

    Die Datei liegt auf einer fremden Maschine; es gibt kein `ziel.exists()`.
    `files_delete` **nicht** gerufen zu haben ist hier die vollstaendige
    Aussage — der Agent loescht nur, wenn er gefragt wird.
    """
    agent = _agent_einsetzen(
        monkeypatch, _Agent("A" * (MAX_HISTORY_EDIT_SIZE + 1))
    )

    with pytest.raises(HTTPException) as fehler:
        _loeschen(db, regular_user, datei_server, "welt.map")

    assert fehler.value.status_code == 413
    assert fehler.value.detail["code"] == "FILE_TOO_LARGE_FOR_HISTORY"
    assert agent.geloescht == []
    assert _versionen(datei_server, "welt.map") == []


def test_agentenpfad_loescht_keine_binaerdatei(
    db: Session,
    regular_user: User,
    datei_server: Server,
    versionsspeicher: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der Agent liest mit derselben Ersetzung — also gilt dieselbe Abweisung.

    Was ueber die Leitung kommt, ist bereits dekodierter Text; die Originalbytes
    sind zu diesem Zeitpunkt schon nicht mehr zu haben. Der Schnappschuss waere
    hier genauso wertlos wie lokal, und die Datei genauso weg.
    """
    agent = _agent_einsetzen(
        monkeypatch, _Agent(BINAER.decode("utf-8", errors="replace"))
    )

    with pytest.raises(HTTPException) as fehler:
        _loeschen(db, regular_user, datei_server, "mods/plugin.jar")

    assert fehler.value.status_code == 422
    assert fehler.value.detail["code"] == "FILE_BINARY_NO_HISTORY"
    assert agent.geloescht == []
    assert _versionen(datei_server, "mods/plugin.jar") == []


# ── Die Revision: geloescht wird die Datei, die gemeint war ──────────────


def test_veraltete_revision_verhindert_das_loeschen_lokal(
    db: Session, regular_user: User, datei_server: Server, versionsspeicher: Path
) -> None:
    """Hat sich die Datei seit dem Vorschlag geaendert, ist es nicht mehr dieselbe.

    Die Begruendung, mit der jemand — oder die KI — das Loeschen angestossen
    hat, galt einem Inhalt. Steht dort inzwischen etwas anderes, gilt sie nicht
    mehr, und der Vorgang gehoert abgebrochen statt ausgefuehrt.

    Der Abbruch passiert **vor** allem anderen: die Datei ist danach unveraendert
    da, und im Versionsspeicher steht nichts. Ein Schnappschuss waere hier kein
    Schaden, aber sein Fehlen belegt die Reihenfolge.
    """
    ziel = Path(datei_server.install_dir) / "server.cfg"
    _schreiben(ziel, KONFIG)
    veraltet = content_revision(b"port=2302\n")

    with pytest.raises(HTTPException) as fehler:
        _loeschen(db, regular_user, datei_server, "server.cfg", revision=veraltet)

    assert fehler.value.status_code == 409
    assert fehler.value.detail["code"] == "FILE_REVISION_CONFLICT"
    assert _lesen(ziel) == KONFIG
    assert _versionen(datei_server, "server.cfg") == []


def test_veraltete_revision_verhindert_das_loeschen_beim_agenten(
    db: Session,
    regular_user: User,
    datei_server: Server,
    versionsspeicher: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dieselbe Reihenfolge entfernt: erst vergleichen, dann anfassen.

    Der Agent hat die Datei gelesen — das muss er, sonst gaebe es nichts zu
    vergleichen. Geloescht hat er nichts.
    """
    agent = _agent_einsetzen(monkeypatch, _Agent(KONFIG))

    with pytest.raises(HTTPException) as fehler:
        _loeschen(
            db, regular_user, datei_server, "server.cfg",
            revision=content_revision(b"port=2302\n"),
        )

    assert fehler.value.status_code == 409
    assert fehler.value.detail["code"] == "FILE_REVISION_CONFLICT"
    assert agent.gelesen == [(server_file_access_service._agent_key(datei_server), "server.cfg")]
    assert agent.geloescht == []
    assert _versionen(datei_server, "server.cfg") == []


def test_passende_revision_laesst_das_loeschen_zu(
    db: Session, regular_user: User, datei_server: Server, versionsspeicher: Path
) -> None:
    """Gegenprobe zur Revisionspruefung: mit der richtigen Kennung geht es.

    Ohne sie waere die Zusage auch dann erfuellt, wenn `delete_server_text`
    grundsaetzlich 409 antwortete.
    """
    ziel = Path(datei_server.install_dir) / "server.cfg"
    _schreiben(ziel, KONFIG)

    ergebnis = _loeschen(
        db, regular_user, datei_server, "server.cfg",
        revision=content_revision(KONFIG.encode("utf-8")),
    )

    assert ergebnis["deleted"] is True
    assert not ziel.exists()


# ── Das zweite Tor: schon der Vorschlag weist ab ─────────────────────────


def test_vorschlag_weist_binaerdatei_ab(
    db: Session, datei_server: Server
) -> None:
    """Das Modell soll eine Antwort bekommen, keinen Vorschlag, der beim Klick scheitert.

    Ein `propose_file_delete` auf eine `.jar` waere ohne dieses Tor eine Karte im
    Chat, die der Mensch bestaetigt und die dann mit 422 endet — und in einer
    unbeaufsichtigten Heilung bestaetigt niemand: dort waere der Vorschlag
    schlicht das Ende des Weges, ohne dass das Modell je erfaehrt, warum.

    `AiActionValidationError` und kein Zustandsfehler: es ist eine Aussage
    ueber die genannten Argumente, und darauf kann das Modell in derselben Runde
    reagieren.
    """
    ziel = Path(datei_server.install_dir) / "plugin.jar"
    ziel.write_bytes(BINAER)

    with pytest.raises(AiActionValidationError):
        ai_proposal_service._file_delete_payload(db, datei_server, {"path": "plugin.jar"})

    assert ziel.read_bytes() == BINAER


def test_vorschlag_weist_zu_grosse_datei_ab(
    db: Session, datei_server: Server
) -> None:
    """Dieselbe fruehe Absage fuer alles, was der Versionsspeicher nicht nimmt."""
    ziel = Path(datei_server.install_dir) / "welt.map"
    _schreiben(ziel, "A" * (MAX_HISTORY_EDIT_SIZE + 1))

    with pytest.raises(AiActionValidationError):
        ai_proposal_service._file_delete_payload(db, datei_server, {"path": "welt.map"})

    assert ziel.is_file()


def test_vorschlag_laesst_eine_gewoehnliche_konfiguration_durch(
    db: Session, datei_server: Server
) -> None:
    """Die Gegenprobe zum zweiten Tor — und die Revision, die es mitfuehrt.

    Der Vorschlag merkt sich die Revision des Augenblicks. Aendert sich die Datei
    zwischen Vorschlag und Bestaetigung, faellt das beim Ausfuehren als 409 auf;
    ohne mitgefuehrte Revision gaebe es diesen Vergleich nicht.

    `lines` steht in der Vorschau, weil der Unterschied zwischen einer leeren
    Sperrdatei und einer Weltkonfiguration mit hunderten Zeilen darueber
    entscheidet, ob jemand das ohne Nachsehen bestaetigt.
    """
    ziel = Path(datei_server.install_dir) / "server.cfg"
    _schreiben(ziel, KONFIG)

    payload, preview, revision = ai_proposal_service._file_delete_payload(
        db, datei_server, {"path": "server.cfg"}
    )

    assert payload == {"path": "server.cfg"}
    assert preview["operation"] == "file_delete"
    assert preview["binary"] is False
    assert preview["lines"] == len(KONFIG.splitlines())
    assert revision == content_revision(KONFIG.encode("utf-8"))
    assert ziel.is_file()


def test_beide_tore_kennen_dieselbe_grenze(
    db: Session, regular_user: User, datei_server: Server, versionsspeicher: Path
) -> None:
    """Eine Grenze, ein Ort — importiert statt abgeschrieben.

    `ai_proposal_service` holt `MAX_HISTORY_EDIT_SIZE` aus `file_history_service`
    und traegt die Zahl nicht noch einmal. Der Unterschied ist nicht kosmetisch:
    eine abgeschriebene Zahl haelt genau so lange, bis jemand die eine von beiden
    aendert, und dann wandert die Absage vom Vorschlag zum Klick — der
    Vorschlag stuende im Chat, das Ausfuehren scheiterte. Deshalb steht hier die
    **Identitaet** der Konstante und nicht nur ihr Wert: zwei getrennt
    uebersetzte Literale mit demselben Wert waeren verschiedene Objekte.

    Darunter dieselbe Aussage als Verhalten: bei exakt der Grenze sagen beide
    Tore ja, ein Byte darueber sagen beide nein.
    """
    assert (
        ai_proposal_service.MAX_HISTORY_EDIT_SIZE
        is file_history_service.MAX_HISTORY_EDIT_SIZE
    )

    passt = Path(datei_server.install_dir) / "passt.cfg"
    _schreiben(passt, "C" * MAX_HISTORY_EDIT_SIZE)
    zu_gross = Path(datei_server.install_dir) / "zu_gross.cfg"
    _schreiben(zu_gross, "C" * (MAX_HISTORY_EDIT_SIZE + 1))

    payload, _preview, _revision = ai_proposal_service._file_delete_payload(
        db, datei_server, {"path": "passt.cfg"}
    )
    assert payload == {"path": "passt.cfg"}
    assert _loeschen(db, regular_user, datei_server, "passt.cfg")["deleted"] is True

    with pytest.raises(AiActionValidationError):
        ai_proposal_service._file_delete_payload(db, datei_server, {"path": "zu_gross.cfg"})
    with pytest.raises(HTTPException) as fehler:
        _loeschen(db, regular_user, datei_server, "zu_gross.cfg")
    assert fehler.value.status_code == 413
    assert zu_gross.is_file()


# ── Der vorhandene Rueckweg ──────────────────────────────────────────────


def test_bereits_gesicherter_inhalt_verhindert_das_loeschen_nicht(
    db: Session, regular_user: User, datei_server: Server, versionsspeicher: Path
) -> None:
    """Ein vorhandener Rueckweg darf das Loeschen nicht verhindern.

    `snapshot` hat zwei Gruende, `False` zu sagen, und `delete_server_text` kennt
    nur einen davon: zu gross **oder** die juengste Version traegt bereits
    dieselbe Revision. Der zweite Fall entsteht bei ganz gewoehnlicher Bedienung
    — wer eine Datei im Editor oeffnet und ohne Aenderung speichert, legt genau
    diesen Schnappschuss an (`write_server_text` sichert den **alten** Inhalt,
    der hier der aktuelle ist).

    Solange `delete_server_text` jedes `False` als "kein Rueckweg" las, war die
    Datei danach dauerhaft unloeschbar — mit falscher Begruendung obendrein: 413
    "zu gross" fuer eine Datei von 50 Bytes. Im Heilungslauf uebersetzt
    `_execute_file_delete` das in ein pauschales AI_ACTION_EXECUTION_FAILED; das
    Modell kann daraus nichts ableiten und versucht es wieder.

    Die Zusage lautet "geloescht wird nur, was sich zurueckholen laesst". Hier
    laesst es sich zurueckholen: derselbe Inhalt liegt bereits im
    Versionsspeicher. Also wird geloescht. Geprueft wird deshalb nicht mehr der
    Rueckgabewert von `snapshot`, sondern ob hinterher eine Version dasteht.
    """
    ziel = Path(datei_server.install_dir) / "server.cfg"
    _schreiben(ziel, KONFIG)

    # Speichern ohne Aenderung — der Weg, auf dem der Zustand im Betrieb entsteht.
    server_file_access_service.write_server_text(
        db,
        user=regular_user,
        server_id=datei_server.id,
        relative_path="server.cfg",
        content=KONFIG,
        expected_revision=content_revision(KONFIG.encode("utf-8")),
    )
    assert len(_versionen(datei_server, "server.cfg")) == 1

    ergebnis = _loeschen(db, regular_user, datei_server, "server.cfg")

    assert ergebnis["deleted"] is True
    assert not ziel.exists()
    versionen = _versionen(datei_server, "server.cfg")
    assert (
        file_history_service.read_version(
            datei_server.id, "server.cfg", versionen[0]["id"]
        )["content"]
        == KONFIG
    )
