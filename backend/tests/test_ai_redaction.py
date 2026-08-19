"""Was die Redaktion entfernen muss — und was sie stehen lassen muss.

Dieses Modul hatte neun Aufrufstellen und keine eigene Testdatei. Geprüft wurde
es nur nebenbei, über ein ``assert "[REDACTED]" in text`` in Tests, die eigentlich
etwas anderes zusichern. Genau so kann ein Loch entstehen, das niemandem
auffällt: die Zusicherung „irgendwo wurde etwas ersetzt“ ist erfüllt, während
das Passwort daneben unverändert weitergeht.

Eine Reviewrunde am 2026-08-11 hat das nachgewiesen. ``RCON_PASSWORD=hunter2``
ging im Klartext an den KI-Anbieter, weil ``\\b`` am Unterstrich scheitert — und
das ist nicht irgendeine Schreibweise, sondern die übliche für Umgebungs-
variablen von Spieleservern. Ebenso ``MYSQL_ROOT_PASSWORD``, ``OPENAI_API_KEY``
und jeder JSON-Schlüssel, weil zwischen Name und Doppelpunkt ein Anführungs-
zeichen steht.

Die Tests stehen deshalb in zwei Gruppen, und die zweite ist genauso wichtig wie
die erste: eine Redaktion, die zu viel entfernt, macht Logs unlesbar und
verleitet dazu, sie ganz abzuschalten.
"""

from __future__ import annotations

import pytest

from services.ai_redaction import (
    enthaelt_zugangsdaten,
    redact_and_count,
    redact_freetext,
    redact_sensitive_text,
)


# ── Was verschwinden muss ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("eingabe", "geheimnis"),
    [
        # Umgebungsvariablen — die Form, an der das alte Muster scheiterte.
        ("RCON_PASSWORD=hunter2", "hunter2"),
        ("MYSQL_ROOT_PASSWORD=geheim", "geheim"),
        ("OPENAI_API_KEY=xyzabc123", "xyzabc123"),
        ("DB_SECRET=abc", "abc"),
        ("X_AUTH_TOKEN=abc123", "abc123"),
        # JSON — das zweite Loch: das Anführungszeichen vor dem Doppelpunkt.
        ('{"password": "hunter2"}', "hunter2"),
        ('{"api_key":"sk-test-value"}', "sk-test-value"),
        # Ein Wert in Anführungszeichen darf Leerzeichen haben. Ohne diese
        # Regel bliebe von „a b c“ das „b c“ stehen.
        ('X_AUTH_TOKEN = "a b c"', "a b c"),
        # Punktgetrennt und nackt — das konnte das alte Muster schon.
        ("rcon.password=geheim", "geheim"),
        ("password=hunter2", "hunter2"),
        ("API-KEY: abcdef", "abcdef"),
        ("credential=xyz", "xyz"),
        # Zusammengeschrieben — das dritte Loch. Der Präfix verlangte hinter
        # jedem Wortteil ein Trennzeichen, und die Grenze davor verbot die
        # Wortmitte; zusammen ließen sie genau die Schreibweise durch, die in
        # den INI-Dateien von ARK, Palworld, DayZ und SCUM steht. Das sind die
        # Dateien, die `read_config` liest und an den Anbieter weiterreicht.
        ("ServerAdminPassword=geheim", "geheim"),
        ("AdminPassword=hunter2", "hunter2"),
        ("SpectatorPassword=xyzabc", "xyzabc"),
        ("rconPassword: swordfish", "swordfish"),
        ("MyApiKey=sk-test-value", "sk-test-value"),
        ('{"ServerAdminPassword": "hunter2"}', "hunter2"),
        # Vier Löcher aus der Review vom 19.08.2026: `secret` musste am Ende
        # stehen, `pass`/`pwd` fehlten ganz, `…_KEY` zählte nur nach `api`,
        # und die deutschen Wörter fehlten. Jede Zeile hier ging vorher im
        # Klartext an den Anbieter.
        ("SECRET_KEY=abc123xyz", "abc123xyz"),
        ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI", "wJalrXUtnFEMI"),
        ("SECRET_KEY_BASE=deadbeefcafe", "deadbeefcafe"),
        ("LICENSE_KEY=XXXX-YYYY-ZZZZ", "XXXX-YYYY-ZZZZ"),
        ("db_pass=hunter2", "hunter2"),
        ("db_pwd=hunter2", "hunter2"),
        ("EncryptionKey=abcdef", "abcdef"),
        ("SessionKey: qwertz", "qwertz"),
        ('{"encryption_key": "s3cr3tval"}', "s3cr3tval"),
        ("credentials=user:passwort123", "user:passwort123"),
        ("passwort: hunter2", "hunter2"),
        ("Kennwort=geheim42", "geheim42"),
    ],
)
def test_a_secret_assignment_never_survives(eingabe: str, geheimnis: str) -> None:
    ergebnis = redact_sensitive_text(eingabe)
    assert geheimnis not in ergebnis, f"{eingabe!r} gab das Geheimnis preis: {ergebnis!r}"
    assert "[REDACTED]" in ergebnis


def test_the_surrounding_json_stays_readable() -> None:
    """Der Wert geht, die Form bleibt.

    Vorher wurde jede Zuweisung auf ``schluessel=[REDACTED]`` normalisiert; aus
    gültigem JSON wurde damit Bruch. Der Text geht als Kontext an ein Modell —
    ein zerlegtes Dokument ist dort teurer als ein erhaltenes.
    """
    assert redact_sensitive_text('{"password": "hunter2"}') == '{"password": "[REDACTED]"}'
    assert redact_sensitive_text("RCON_PASSWORD=hunter2") == "RCON_PASSWORD=[REDACTED]"


def test_known_token_shapes_go_even_without_an_assignment() -> None:
    """Ein Schlüssel bleibt ein Schlüssel, auch ohne ``name=`` davor."""
    for roh in ("sk-abcdefghijklmnopqrst", "ghp_" + "a" * 20, "AKIA" + "B" * 16):
        assert roh not in redact_sensitive_text(f"Im Log stand {roh} mittendrin")


def test_a_private_key_block_goes_as_a_whole() -> None:
    text = "vorher\n-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----\nnachher"
    ergebnis = redact_sensitive_text(text)
    assert "MIIE" not in ergebnis
    assert ergebnis.startswith("vorher")
    assert ergebnis.endswith("nachher")


def test_the_count_reports_every_replacement_not_every_secret() -> None:
    """Zehnmal dasselbe Passwort sind zehn Ersetzungen.

    Die Zahl dient dem Satz „an drei Stellen unkenntlich gemacht, sieh es dir
    an“ — dafür ist die Zahl der Fundstellen die brauchbarere.
    """
    text, anzahl = redact_and_count("password=a\npassword=a\npassword=a")
    assert anzahl == 3
    assert text.count("[REDACTED]") == 3


# ── Was stehen bleiben muss ───────────────────────────────────────────
#
# Eine Redaktion, die zu viel greift, ist kein sicherer Fehlschlag: sie macht
# Logs unbrauchbar, und unbrauchbare Logs schaltet irgendwann jemand ab.


@pytest.mark.parametrize(
    "harmlos",
    [
        "tokens_used=42",
        "max_tokens: 4096",
        "tokenizer=fast",
        "Der Server hat 5 Tokens verbraucht",
        "Dein Passwort steht in der Anleitung",
        "secretary=anna",
        "Die Konfiguration liegt in /etc/server.properties",
        # Seit der Präfix auch Wortteile ohne Trennzeichen frisst, ist die
        # Gegenprobe wichtiger geworden: das Schlüsselwort muss weiterhin
        # unmittelbar vor dem Trennzeichen stehen, sonst greift die Schwärzung
        # in jede Zeile, in der irgendwo „token“ vorkommt.
        "tokenCount=42",
        "MaxTokens=100",
        "PasswordPolicy_MinLength=8",
        # Gegenproben zu den vier Nachträgen vom 19.08.2026: `pass`/`pwd` und
        # `…key` zählen nur als eigenes Wortteil mit Geheimnis-Bedeutung.
        "Compass=enabled",
        "bypass=true",
        "hotkey=F5",
        "MapKey=tab",
        "monkey: banana",
    ],
)
def test_ordinary_text_is_left_alone(harmlos: str) -> None:
    assert redact_sensitive_text(harmlos) == harmlos


# ── Die Abweisung beim Merken: enger als die Schwärzung ───────────────
#
# `enthaelt_zugangsdaten` entscheidet, ob ein Memory-/Skill-Text abgewiesen
# wird. Die Schwärzung darf großzügig sein (ihr Ergebnis bleibt lesbar); eine
# Abweisung macht den Eintrag unspeicherbar und behauptet einen Grund. Zwei
# reproduzierte Fehlabweisungen vom 19.08.2026 stehen unten als Zusicherung.


@pytest.mark.parametrize(
    "text",
    [
        "RCON_PASSWORD=hunter2",
        "das passwort: hunter2 bitte merken",
        "api_key=sk-test-1234567890",
        "Bearer eyJhbGciOiJIUzI1NiJ9",
        "AKIA" + "B" * 16,
        "db_pass=hunter2",
        "SECRET_KEY=abc123xyz",
    ],
)
def test_real_credentials_are_rejected(text: str) -> None:
    assert enthaelt_zugangsdaten(text), f"{text!r} hätte als Zugangsdatum gelten müssen"


@pytest.mark.parametrize(
    "text",
    [
        # Die zwei gemessenen Fehlabweisungen: E-Mail-Adresse und
        # Zahlenkontingent. Beide scheiterten mit „Memory darf keine
        # Zugangsdaten enthalten“, und die KI erklärte dem Benutzer dann,
        # er habe ein Passwort geschickt.
        "Rechnungen gehen an billing@firma.de",
        "Serverwechsel-Token: 2 pro Monat",
        "Token-Budget: 100000",
        "Der Benutzer mag kurze Antworten",
        "Das Passwort wird über den Passwortmanager verwaltet",
    ],
)
def test_memorable_facts_are_not_rejected(text: str) -> None:
    assert not enthaelt_zugangsdaten(text), f"{text!r} ist kein Zugangsdatum"


def test_the_authorization_header_keeps_its_established_shape() -> None:
    """Diese Form ist anderswo zugesichert und darf sich nicht verschieben."""
    assert redact_sensitive_text("Authorization: Bearer abc.def.ghi") == "Authorization=[REDACTED]"


def test_a_long_word_does_not_bring_the_redaction_to_a_standstill() -> None:
    """Ein einziges langes Wort darf keine spürbare Zeit kosten.

    Der Präfix vor dem Schlüsselwort (``[A-Za-z0-9._-]*``) darf ohne eine Grenze
    davor an **jeder** Stelle eines Wortes neu ansetzen und jedes Mal bis zum
    Ende laufen — quadratischer Aufwand. Gemessen an 50.000 Wortzeichen waren
    das 101 Sekunden statt 0,002; und solche Zeichenketten stehen in echten
    Logzeilen und Konfigurationsdateien, die die KI liest. Die Schwärzung läuft
    dabei im Anfragepfad, ein Hänger dort hält den Arbeiter fest.

    Die Grenze ``(?<![A-Za-z0-9._-])`` lässt je Wort genau einen Ansatzpunkt zu.
    Fällt sie wieder weg, schlägt dieser Test zu — und zwar deutlich.
    """
    import time

    text = "A" * 200_000
    beginn = time.perf_counter()
    assert redact_sensitive_text(text) == text
    assert time.perf_counter() - beginn < 2.0


# ── Freitext: zusätzlich fremde IP-Adressen ───────────────────────────
#
# `redact_freetext` hatte bis zur Reviewrunde vom 14.08.2026 überhaupt keine
# Abdeckung — kein einziger Treffer für `redact_freetext`, `freetext` oder
# `REDACTED_IP` über backend/tests/. Das ist die Funktion, die entscheidet, ob
# die öffentliche Adresse eines Spielers aus einer Join-Zeile an einen externen
# KI-Anbieter geht.
#
# Die öffentlichen Adressen hier stammen bewusst **nicht** aus den
# Dokumentationsnetzen (`203.0.113.0/24` und Geschwister): Python zählt die seit
# 3.13 zu `is_private`, und ein Test damit wäre aus dem falschen Grund rot.


@pytest.mark.parametrize(
    "zeile",
    [
        "[12:34:56] Spieler Anna joined from 93.184.216.34",
        "Connection from 8.8.8.8 refused",
        "player disconnect ip=1.1.1.1 reason=timeout",
        "IPv6-Verbindung von 2606:4700:4700::1111 angenommen",
    ],
)
def test_a_public_address_in_freetext_is_redacted(zeile: str) -> None:
    """In einer Logzeile bezeichnet eine öffentliche Adresse eine Person.

    Sie hat für die Diagnose keinen Wert und ist ein personenbezogenes Datum,
    das kein Modellanbieter zu sehen braucht.
    """
    assert "[REDACTED_IP]" in redact_freetext(zeile)


@pytest.mark.parametrize(
    "bleibt",
    [
        # Die Bindeadresse — genau die Zeile, an der man "läuft, aber niemand
        # kommt drauf" erkennt. Sie zu schwärzen hieße, die Diagnose
        # abzuschaffen, um ein Datum zu schützen, das keine Person bezeichnet.
        "server-ip=0.0.0.0",
        "bind 127.0.0.1:2302",
        "listening on 192.168.1.50",
        "gateway 10.0.0.1",
        "docker bridge 172.17.0.2",
        "link-local 169.254.0.5",
        "IPv6 loopback ::1",
        "ULA fd00::1",
        # Keine Adressen: `ipaddress` entscheidet, und was durchfällt, bleibt.
        "Minecraft 1.20.4 gestartet",
        "Fehlercode 999.1.2.3",
        "[12:34:56] Serverstart",
    ],
)
def test_freetext_leaves_what_names_no_person(bleibt: str) -> None:
    assert redact_freetext(bleibt) == bleibt


def test_freetext_still_removes_credentials() -> None:
    """Die IP-Schwärzung kommt **zusätzlich**, nicht an Stelle der übrigen.

    Wäre `redact_freetext` ein eigener Weg statt eine Erweiterung, würde die
    nächste Ergänzung an `redact_sensitive_text` genau die Werkzeuge nicht
    erreichen, die den meisten Fremdtext liefern.
    """
    text = redact_freetext("RCON_PASSWORD=hunter2 von 93.184.216.34")

    assert "hunter2" not in text
    assert "93.184.216.34" not in text


def test_the_plain_redaction_keeps_addresses() -> None:
    """Die Gegenprobe zur Trennung der beiden Funktionen.

    `read_server_network` liefert die Bind-Adresse als Betriebsangabe und läuft
    über `redact_sensitive_text`. Wanderte die IP-Schwärzung ins allgemeine
    Muster, wäre die Netzwerkdiagnose ohne jede Fehlermeldung tot.
    """
    assert redact_sensitive_text("bind_address=93.184.216.34") == (
        "bind_address=93.184.216.34"
    )
