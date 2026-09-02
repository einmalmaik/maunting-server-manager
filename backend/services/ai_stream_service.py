# -*- coding: utf-8 -*-
"""Abwaertskompatibler Wrapper fuer das modularisierte AI-Streaming-Paket (`services.ai_stream`).

Die urspruengliche 5.137-Zeilen-Datei wurde in das modulare Paket `services.ai_stream`
ueberfuehrt. Alle Symbole werden 1:1 re-exportiert und Aenderungen (z.B. Monkeypatching in Tests)
transparent an das Paket weitergeleitet.
"""

from __future__ import annotations

import sys
from types import ModuleType
import services.ai_stream as _ai_stream
from services.ai_stream import *
from services.ai_stream import __all__

class _AiStreamServiceModule(ModuleType):
    """Wrapper-Modul fuer transparente Weiterleitung an services.ai_stream."""
    def __getattr__(self, name: str):
        return getattr(_ai_stream, name)

    def __setattr__(self, name: str, value):
        if name in ("__file__", "__name__", "__doc__", "__package__", "__loader__", "__spec__", "__path__", "__all__"):
            super().__setattr__(name, value)
            return
        setattr(_ai_stream, name, value)
        super().__setattr__(name, value)

sys.modules[__name__].__class__ = _AiStreamServiceModule
