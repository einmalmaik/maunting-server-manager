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

from services.ai_redaction import redact_and_count, redact_sensitive_text


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
        # Wortmitte; zusammen liessen sie genau die Schreibweise durch, die in
        # den INI-Dateien von ARK, Palworld, DayZ und SCUM steht. Das sind die
        # Dateien, die `read_config` liest und an den Anbieter weiterreicht.
        ("ServerAdminPassword=geheim", "geheim"),
        ("AdminPassword=hunter2", "hunter2"),
        ("SpectatorPassword=xyzabc", "xyzabc"),
        ("rconPassword: swordfish", "swordfish"),
        ("MyApiKey=sk-test-value", "sk-test-value"),
        ('{"ServerAdminPassword": "hunter2"}', "hunter2"),
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
    ],
)
def test_ordinary_text_is_left_alone(harmlos: str) -> None:
    assert redact_sensitive_text(harmlos) == harmlos


def test_the_authorization_header_keeps_its_established_shape() -> None:
    """Diese Form ist anderswo zugesichert und darf sich nicht verschieben."""
    assert redact_sensitive_text("Authorization: Bearer abc.def.ghi") == "Authorization=[REDACTED]"
