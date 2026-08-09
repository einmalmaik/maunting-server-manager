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


def redact_sensitive_text(value: str) -> str:
    """Entfernt typische Credentials vor Persistenz und Providertransfer."""
    text = _PRIVATE_KEY_RE.sub("[REDACTED_PRIVATE_KEY]", value)
    text = _AUTHORIZATION_BEARER_RE.sub("Authorization=[REDACTED]", text)
    text = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    return _KNOWN_TOKEN_RE.sub("[REDACTED_TOKEN]", text)
