"""Entfernt Zugangsdaten aus Text, bevor er gespeichert oder verschickt wird.

Eigenes Modul, weil die Funktion an neun Stellen gebraucht wird und mit
Kontextaufbau nichts zu tun hat. Vorher lag sie in `ai_context_service` — und
weil `ai_memory_service` sie auf Modulebene importierte, waehrend
`ai_context_service` seinerseits `ai_memory_service` brauchte, bestand ein
Importzyklus. Er krachte nur deshalb nicht, weil eine der beiden Richtungen
verzoegert in einer Funktion stand. Wer diesen verzoegerten Import fuer
Unordnung hielt und ihn nach oben zog, brachte das Panel zum Stillstand.

Die Muster sind bewusst konservativ: lieber ein `[REDACTED]` zu viel als ein
Token in einem Log, einer Zusammenfassung oder einer Anfrage an einen externen
KI-Anbieter.
"""

from __future__ import annotations

import re


#: Zuweisungen der Form ``SCHLUESSEL = wert``, in den Schreibweisen, die in
#: Spieleserver-Konfigurationen tatsächlich vorkommen.
#:
#: Hier stand einmal ``\b(password|…)\b\s*[:=]``. Das sah vollständig aus und
#: hatte zwei Löcher, die genau die häufigsten Fälle trafen:
#:
#: 1. ``\b`` scheitert am Unterstrich, weil der ein Wortzeichen ist. Damit ging
#:    ``RCON_PASSWORD=hunter2`` unverändert an den KI-Anbieter — und das ist
#:    nicht irgendeine Schreibweise, sondern die übliche für Umgebungsvariablen.
#:    Ebenso ``MYSQL_ROOT_PASSWORD``, ``OPENAI_API_KEY``, ``DB_SECRET``.
#: 2. In JSON steht zwischen Schlüssel und Doppelpunkt ein Anführungszeichen,
#:    das ``\s*[:=]`` nicht zuließ: ``{"password": "hunter2"}`` blieb stehen.
#:
#: Deshalb jetzt: ein optionaler Präfix aus Wortteilen vor dem Schlüsselwort,
#: und optionale Anführungszeichen um Trennzeichen und Wert. Die Anführungs-
#: zeichen werden mitgeschrieben, damit aus gültigem JSON wieder gültiges JSON
#: wird — der Text geht als Kontext an ein Modell und soll lesbar bleiben.
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)"
    # Kein Buchstabe und keine Ziffer davor: der Präfix unten fängt das ab, was
    # dazugehört, und hier soll keine Wortmitte anfangen.
    r"(?<![A-Za-z0-9])"
    r"(?P<key>"
    r"(?:[A-Za-z0-9]+[._-])*"
    r"(?:password|passwd|secret|token|api[_-]?key|authorization|credential)"
    r")"
    # Trennzeichen, davor optional das schliessende Anführungszeichen des
    # Schlüssels. Es wird mitgenommen und unverändert wieder ausgegeben.
    r"(?P<sep>[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"'])?"
    # Mit Anführungszeichen läuft der Wert bis zum nächsten; ohne bis zum
    # nächsten Trennzeichen. Ein Wert in Anführungszeichen darf Leerzeichen
    # enthalten — ein Passwort mit Leerzeichen wäre sonst nur halb entfernt.
    r"(?(quote)[^\"'\n]*[\"']|[^\s,;]+)"
)
_AUTHORIZATION_BEARER_RE = re.compile(
    r"(?i)\bauthorization\b\s*[:=]\s*bearer\s+[A-Za-z0-9._~+\-/]+=*"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+\-/]+=*")
_KNOWN_TOKEN_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"
)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)


def _ersetze_zuweisung(match: re.Match[str]) -> str:
    """Schreibt Schlüssel und Trennzeichen zurück, ersetzt nur den Wert.

    Vorher wurde jede Zuweisung auf ``schluessel=[REDACTED]`` normalisiert. Das
    machte aus ``{"password": "geheim"}`` ein ``{password=[REDACTED]}`` — der
    Wert war weg, die Datei aber auch kaputt. Da der Text als Kontext an ein
    Modell geht, ist die erhaltene Form die brauchbarere.
    """
    quote = match.group("quote") or ""
    return f"{match.group('key')}{match.group('sep')}{quote}[REDACTED]{quote}"


def redact_and_count(value: str) -> tuple[str, int]:
    """Wie `redact_sensitive_text`, aber sagt auch, wie oft es zugeschlagen hat.

    Die Zahl braucht, wer den Benutzer unterrichten will statt ihn abzuweisen.
    Bei Anhaengen ist genau das der Unterschied: ein Serverlog enthaelt fast
    immer irgendwo ein Tokenmuster, und "abgelehnt" hilft niemandem weiter —
    "drei Stellen unkenntlich gemacht" schon.

    Gezaehlt werden Ersetzungen, nicht Geheimnisse. Ein und dasselbe Passwort,
    das zehnmal im Log steht, zaehlt zehnmal; das ist fuer den Zweck — "hier
    wurde etwas veraendert, sieh es dir an" — die brauchbarere Zahl.
    """
    text, a = _PRIVATE_KEY_RE.subn("[REDACTED_PRIVATE_KEY]", value)
    text, b = _AUTHORIZATION_BEARER_RE.subn("Authorization=[REDACTED]", text)
    text, c = _SECRET_ASSIGNMENT_RE.subn(_ersetze_zuweisung, text)
    text, d = _BEARER_RE.subn("Bearer [REDACTED]", text)
    text, e = _KNOWN_TOKEN_RE.subn("[REDACTED_TOKEN]", text)
    return text, a + b + c + d + e


def redact_sensitive_text(value: str) -> str:
    """Entfernt typische Credentials vor Persistenz und Providertransfer."""
    return redact_and_count(value)[0]
