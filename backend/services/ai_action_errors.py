"""Die zwei Fehlerarten der KI-Aktionsschicht.

Sie stehen bewusst in einem eigenen, winzigen Modul. Beide Haelften der
Aktionsschicht brauchen sie — die Lesewerkzeuge in `ai_action_service` und der
Vorschlags-Lebenszyklus in `ai_proposal_service`. Lebten sie in einer der
beiden, muesste die andere zurueckimportieren, und aus einer klaren Schichtung
waere ein Kreis geworden.

So zeigt die Abhaengigkeit in genau eine Richtung:

    ai_action_errors  ←  ai_action_service  ←  ai_proposal_service
"""

from __future__ import annotations


class AiActionValidationError(ValueError):
    """Die Anfrage taugt nicht — falsche Argumente, fehlendes Recht, kein Server.

    Wird dem Modell als Werkzeugergebnis zurueckgegeben, damit es den naechsten
    Versuch anders anstellt, statt den Stream abzubrechen.
    """


class AiActionStateError(ValueError):
    """Der Vorschlag ist in einem Zustand, der den Schritt nicht zulaesst.

    ``code`` ist eine stabile Kennung (``expired``, ``already_executed``, …),
    die der Router in eine HTTP-Antwort und die Oberflaeche in einen Satz
    uebersetzt. Deshalb eine Kennung und kein Fliesstext: die Zeichenkette ist
    Teil der Schnittstelle.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
