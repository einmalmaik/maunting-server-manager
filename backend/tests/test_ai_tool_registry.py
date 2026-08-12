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
    """Jede Menge muss sich aus der Tabelle nachrechnen lassen.

    Diese Funktion pruefte urspruenglich, dass `ai_action_service` dieselben
    Objekte exportiert wie die Registry. Beim Umhaengen der Aufrufer hat ein
    Skript beide Seiten des Vergleichs auf dasselbe Modul gezogen, und uebrig
    blieben drei `x is x` — immer wahr, nie fehlschlagend, unter einem Namen,
    der eine Invariante verspricht. Ein Test, der nichts prueft, ist schlimmer
    als keiner: er sieht in der Uebersicht nach Deckung aus.

    Jetzt wird das nachgerechnet, was der Name behauptet. Wer eine Menge kuenftig
    von Hand pflegt, statt sie abzuleiten, faellt hier auf.
    """
    aus_tabelle = {
        name: spec.art for name, spec in ai_tool_registry.WERKZEUGE.items()
    }
    erwartet_lesend = {
        name for name, art in aus_tabelle.items()
        if art in {"server_read", "global_read", "ask"}
    }
    erwartet_schreibend = {
        name for name, art in aus_tabelle.items()
        if art in {"server_write", "global_write"}
    }
    assert ai_tool_registry.READ_TOOLS == erwartet_lesend
    assert ai_tool_registry.WRITE_TOOLS == erwartet_schreibend
    assert ai_tool_registry.SERVER_READ_TOOLS == {
        name for name, art in aus_tabelle.items() if art == "server_read"
    }
    assert ai_tool_registry.SERVER_WRITE_TOOLS == {
        name for name, art in aus_tabelle.items() if art == "server_write"
    }
    assert ai_tool_registry.MEMORY_TOOLS == {
        name for name, spec in ai_tool_registry.WERKZEUGE.items()
        if spec.gruppe == "memory"
    }
    assert ai_tool_registry.SKILL_TOOLS == {
        name for name, spec in ai_tool_registry.WERKZEUGE.items()
        if spec.gruppe == "skill"
    }
    assert ai_tool_registry.ALWAYS_CONFIRM_TOOLS == (
        {
            name for name, spec in ai_tool_registry.WERKZEUGE.items()
            if spec.immer_bestaetigen
        }
        | set(ai_tool_registry.GEPLANT_IMMER_BESTAETIGEN)
    )


def test_the_two_halves_share_the_same_set_objects() -> None:
    """Was `ai_action_service` fuehrt, ist die Registry-Menge selbst.

    Das war die urspruengliche Absicht der Funktion darueber: keine Kopie, kein
    zweiter Stand, der auseinanderlaufen kann. Hier steht sie ohne die Modulnamen
    auf beiden Seiten, die ein Ersetzungsskript zusammenziehen konnte.
    """
    assert ai_action_service.READ_TOOLS is ai_tool_registry.READ_TOOLS
    assert ai_action_service.GLOBAL_READ_TOOLS is ai_tool_registry.GLOBAL_READ_TOOLS


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


def test_only_the_irreversible_tools_are_confirm_only() -> None:
    """Das Kriterium der Sperre ist Unumkehrbarkeit, nicht Risiko.

    Vorgabe des Betreibers: im autonomen Modus laeuft alles durch, ausser was
    Daten vernichtet. Diese Liste steht hier ausgeschrieben, damit ein
    zusaetzlicher Eintrag eine bewusste Entscheidung ist und nicht ein
    Bauchgefuehl, das jemand beim Bauen eines Werkzeugs hatte — genau so waren
    Blueprint-Wechsel und Bind-IP-Aenderung hineingeraten, obwohl beide
    umkehrbar sind.

    Die drei Hoster-Werkzeuge sind bewusst dazugekommen und stehen unter dem
    **zweiten** Kriterium, das `GEPLANT_IMMER_BESTAETIGEN` seit jeher fuehrt:
    eine Rechteaenderung oder eine Schluesselerzeugung verschiebt den Rahmen, in
    dem die KI selbst arbeitet. Bei `propose_hoster_integration` kommt ein
    mechanischer Grund dazu — im autonomen Modus wird der Rueckgabewert und mit
    ihm der einmalige API-Key verworfen; die Integration waere unbenutzbar.
    """
    gebaut = {
        name for name, spec in ai_tool_registry.WERKZEUGE.items()
        if spec.immer_bestaetigen
    }
    assert gebaut == {
        "propose_server_delete",
        "propose_backup_restore",
        "propose_hoster_integration",
        "propose_hoster_product",
        "propose_ai_tarif_role",
    }
