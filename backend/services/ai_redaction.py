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


_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization|credential)\b"
    r"\s*[:=]\s*[^\s,;]+"
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
    text, c = _SECRET_ASSIGNMENT_RE.subn(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text, d = _BEARER_RE.subn("Bearer [REDACTED]", text)
    text, e = _KNOWN_TOKEN_RE.subn("[REDACTED_TOKEN]", text)
    return text, a + b + c + d + e


def redact_sensitive_text(value: str) -> str:
    """Entfernt typische Credentials vor Persistenz und Providertransfer."""
    return redact_and_count(value)[0]
