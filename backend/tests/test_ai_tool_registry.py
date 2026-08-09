"""Eine Tabelle statt zehn von Hand gepflegter Mengen.

Vorher brauchte ein neues Werkzeug Eintraege an mehreren Stellen —
Mengendefinition, Katalog, Verteilung — und eine vergessene fiel erst zur
Laufzeit auf: das Modell rief etwas auf, das der Allowlist nicht bekannt war,
und der Stream endete mit einem Fehler.

Diese Datei sichert die Invariante, die den Fall unmoeglich macht: **Katalog
und Tabelle decken sich vollstaendig**.
"""

from __future__ import annotations

import pytest

from services import ai_action_service, ai_tool_registry


def test_every_offered_tool_has_a_table_entry() -> None:
    """Ohne Zeile waere ein Werkzeug im Katalog, aber in keiner Menge.

    Das Modell duerfte es aufrufen und die Allowlist wuerde es abweisen — ein
    Fehler, der ausschliesslich im Betrieb auffaellt.
    """
    angeboten = {
        item["function"]["name"]
        for item in ai_action_service.provider_tool_definitions()
    }
    assert angeboten - set(ai_tool_registry.WERKZEUGE) == set()


def test_the_sets_are_derived_not_maintained() -> None:
    """Was `ai_action_service` exportiert, kommt aus der Tabelle."""
    assert ai_tool_registry.READ_TOOLS is ai_tool_registry.READ_TOOLS
    assert ai_tool_registry.WRITE_TOOLS is ai_tool_registry.WRITE_TOOLS
    assert ai_tool_registry.ALWAYS_CONFIRM_TOOLS is ai_tool_registry.ALWAYS_CONFIRM_TOOLS


def test_read_and_write_never_overlap() -> None:
    """Die Trennung ist die Grundlage der Bestaetigungspflicht.

    Ein Werkzeug in beiden Mengen wuerde je nach Auswertungsreihenfolge mal
    einen Vorschlag erzeugen und mal sofort laufen.
    """
    assert ai_tool_registry.READ_TOOLS & ai_tool_registry.WRITE_TOOLS == set()


def test_every_write_tool_is_server_or_global_but_not_both() -> None:
    assert (
        ai_tool_registry.SERVER_WRITE_TOOLS & ai_tool_registry.GLOBAL_WRITE_TOOLS
    ) == set()


def test_an_unknown_kind_is_refused() -> None:
    """Ein Tippfehler in der Art darf nicht still eine leere Menge erzeugen."""
    with pytest.raises(ValueError):
        ai_tool_registry.Werkzeug("lesen_vielleicht")


def test_defining_a_tool_without_a_table_entry_fails_loudly() -> None:
    """Der eigentliche Gewinn: der vergessene Eintrag faellt beim Definieren auf."""
    with pytest.raises(AssertionError, match="ai_tool_registry"):
        ai_action_service._function("erfundenes_werkzeug", "Test", {}, [])


def test_planned_confirm_only_tools_are_not_offered() -> None:
    """Platzhalter duerfen im Katalog nicht auftauchen.

    Sie stehen in der Tabelle, damit ein kuenftiges Werkzeug sich ausdruecklich
    einordnen muss — gebaut sind sie nicht, und was nicht gebaut ist, darf das
    Modell nicht sehen.
    """
    angeboten = {
        item["function"]["name"]
        for item in ai_action_service.provider_tool_definitions()
    }
    assert angeboten & ai_tool_registry.GEPLANT_IMMER_BESTAETIGEN == set()


def test_the_bind_ip_tool_stays_confirm_only() -> None:
    """Das einzige gebaute Werkzeug, das nie autonom laufen darf."""
    assert "propose_bind_ip_update" in ai_tool_registry.ALWAYS_CONFIRM_TOOLS
