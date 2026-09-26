"""Spaltentyp fuer Text, der in der Datenbank nur verschluesselt steht.

Im Code ist ein ``DisText`` gewoehnlicher Text. Beim Schreiben verschluesselt
ihn ``DisClient.encrypt``, beim Lesen entschluesselt ihn ``DisClient.decrypt``.
Wer die Tabelle direkt ansieht (PostgreSQL-Studio, Dump, Backup), sieht nur
``msm-dis-v1:...``.

Warum ein Spaltentyp und nicht Aufrufe an jeder Stelle: Chatnachrichten allein
werden an rund hundert Stellen gelesen und geschrieben. Wer an jeder davon
selbst ver- und entschluesseln muss, vergisst irgendwann eine, und dann steht
dort wieder Klartext. Der Typ laesst keine Stelle aus.

Der Preis ist die AAD. Ein Spaltentyp kennt seine Zeile nicht, die AAD bindet
den Wert deshalb nur an Tabelle und Spalte (``msm:ai:ai_messages.content``),
nicht an Zeile oder Besitzer. Gegen Lesen reicht das: ohne den Sidecar ist
der Wert unlesbar. Wer schreiben darf, kann ein Chiffrat innerhalb derselben
Spalte in eine andere Zeile kopieren. Er kann aber auch gleich ein
Passwort-Hash austauschen, der Schutz davor liegt nicht in dieser Spalte.

**Altbestand.** Zeilen von vor der Umstellung stehen noch im Klartext. Sie
werden unveraendert gelesen und von ``services.dis_altbestand`` nachgezogen.
Erkannt wird der Unterschied am Praefix, nicht an einem Entschluesselungsversuch:
ein Versuch je Klartextzeile kostete einen Sidecar-Aufruf, und ein Fehlschlag
waere von einem manipulierten Chiffrat nicht zu unterscheiden.

**Vergleiche.** ``coerce_compared_value`` gibt schlichtes ``Text`` zurueck.
Sonst verschluesselte SQLAlchemy auch den Vergleichswert, etwa das Muster
in ``spalte.like("msm-dis-v1:%")``, und kein Filter traefe je.
"""

from __future__ import annotations

from sqlalchemy.types import Text, TypeDecorator


class DisText(TypeDecorator):
    impl = Text
    cache_ok = True

    def __init__(self, aad: str) -> None:
        super().__init__()
        self.aad = aad

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        # Erst hier importiert: `services/__init__` laedt `models`, ein
        # Import auf Modulebene waere ein Zyklus (wie in `models/user.py`).
        from services.dis_client import DisClient

        return DisClient.encrypt(value, aad=self.aad)

    def process_result_value(self, value: str | None, dialect) -> str | None:
        from services.dis_client import DisClient

        if value is None or not DisClient.ist_verschluesselt(value):
            return value
        return DisClient.decrypt(value, aad=self.aad)

    def coerce_compared_value(self, op, value):
        return Text()


def ai_text(tabelle_spalte: str) -> DisText:
    """``DisText`` mit der AAD ``msm:ai:<tabelle>.<spalte>``."""
    return DisText(aad=f"msm:ai:{tabelle_spalte}")
