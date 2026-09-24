"""Modellersatz für `ai_embedding_service` in Tests.

`encode` liefert eine `Kodierung` — Vektoren samt Modellkennung —, und
`ai_memory_service._vektoren_nachziehen` fragt vorher `aktives_modell`. Ein
Ersatz muss beide Seiten gleich beantworten, sonst prüft der Test einen
Zustand, den es im Betrieb nicht gibt.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from services import ai_embedding_service
from services.ai_embedding_service import MODEL_TAG, Kodierung


def modell_ersetzen(
    monkeypatch: pytest.MonkeyPatch,
    rechner: Callable[[list[str]], list[list[float]] | None],
    modell: str = MODEL_TAG,
) -> None:
    """``encode`` rechnet mit ``rechner`` und meldet ``modell`` als Quelle."""

    def encode(texts: list[str], *, db=None, nur_lokal=False) -> Kodierung | None:
        vektoren = rechner(texts)
        return None if vektoren is None else Kodierung(vektoren, modell)

    monkeypatch.setattr(ai_embedding_service, "encode", encode)
    monkeypatch.setattr(ai_embedding_service, "aktives_modell", lambda *, db=None: modell)


def ohne_modell(monkeypatch: pytest.MonkeyPatch) -> None:
    """Weder lokales Modell noch Rückfall: ``encode`` liefert nichts."""
    monkeypatch.setattr(ai_embedding_service, "encode", lambda texts, *, db=None, nur_lokal=False: None)
    monkeypatch.setattr(ai_embedding_service, "aktives_modell", lambda *, db=None: None)
