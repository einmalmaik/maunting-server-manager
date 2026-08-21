"""Die Grenze zwischen Panel und Smart System — beide Richtungen.

Ein Betreiberbeschluss vom 14.08.2026: die Desktop-App ist ausdruecklich
**keine** zweite Serververwaltung (Hoster-Neutralitaet). Eine Bitte, die von
dort kommt, erreicht deshalb kein Serverwerkzeug — und umgekehrt erreicht eine
Bitte aus dem Browser keinen fremden Rechner.

Zwei Schranken, wie ueberall im Haus:

1. Der **Katalog** bietet gar nicht erst an, was nicht laufen wuerde
   (`herkunft_schnitt`). Das ist Fuehrung.
2. Der **Spiegel** je Aufruf sortiert aus, was trotzdem gerufen wird
   (`_tool_followup_messages`). Das ist die Schranke — ein halluzinierter
   Werkzeugname darf sie nicht unterlaufen.

Und die Herkunft selbst ist eingefroren: ein Lauf, der als Desktop-Lauf
begonnen hat, bleibt einer. Ein unlesbarer Wert faellt auf die **engere**
Seite, nicht auf die weitere.
"""

import pytest

from services import ai_prompt
from services.ai_stream_service import herkunft_aus_zustand
from services.ai_tool_registry import (
    DESKTOP_TOOLS,
    SERVER_READ_TOOLS,
    SERVER_WRITE_TOOLS,
    WERKZEUGE,
    herkunft_schnitt,
)


ALLE = frozenset(WERKZEUGE)


class TestKatalogschnitt:
    def test_aus_dem_smart_system_kein_serverwerkzeug(self):
        erlaubt = herkunft_schnitt(ALLE, "desktop")
        assert not (erlaubt & SERVER_READ_TOOLS)
        assert not (erlaubt & SERVER_WRITE_TOOLS)
        # Die Desktop-Werkzeuge bleiben — sonst waere der Schnitt sinnlos.
        assert DESKTOP_TOOLS <= erlaubt

    def test_aus_dem_panel_kein_fremder_rechner(self):
        erlaubt = herkunft_schnitt(ALLE, "panel")
        assert not (erlaubt & DESKTOP_TOOLS)
        # Serverwerkzeuge bleiben unangetastet: das Panel ist ihr Ort.
        assert SERVER_READ_TOOLS <= erlaubt
        assert SERVER_WRITE_TOOLS <= erlaubt

    def test_schnitt_holt_nie_etwas_zurueck(self):
        """Ein Schnitt, keine Ersetzung — ein fehlendes Recht bleibt fehlend."""
        knapp = frozenset({"list_my_servers"})
        assert herkunft_schnitt(knapp, "desktop") <= knapp
        assert herkunft_schnitt(knapp, "panel") <= knapp

    def test_desktop_werkzeuge_verlangen_das_desktop_recht(self):
        from services.ai_tool_registry import angebotsrechte

        for name in DESKTOP_TOOLS:
            assert angebotsrechte(name) == ("ai.desktop.use",), name


class TestEingefroreneHerkunft:
    def test_ohne_eintrag_gilt_das_panel(self):
        assert herkunft_aus_zustand({}) == "panel"

    def test_unbekannter_wert_faellt_auf_die_engere_seite(self):
        # Die gefaehrliche Richtung ist, die Servergrenze stillschweigend zu
        # oeffnen — also faellt ein Tippfehler auf "desktop", nicht auf "panel".
        assert herkunft_aus_zustand({"herkunft": "Panel "}) == "desktop"
        assert herkunft_aus_zustand({"herkunft": "quatsch"}) == "desktop"
        assert herkunft_aus_zustand({"herkunft": 7}) == "desktop"

    def test_gesetzte_werte_bleiben(self):
        assert herkunft_aus_zustand({"herkunft": "panel"}) == "panel"
        assert herkunft_aus_zustand({"herkunft": "desktop"}) == "desktop"


class TestPrompt:
    def test_desktop_block_nur_mit_schalter(self):
        ohne = ai_prompt.build()
        mit = ai_prompt.build(desktop=True)
        assert ai_prompt.DESKTOP not in ohne
        assert ai_prompt.DESKTOP in mit
        # Angehaengt, nicht eingesetzt: alles davor bleibt Zeichen fuer Zeichen
        # gleich, sonst waere der Anbieter-Zwischenspeicher entwertet.
        assert mit.startswith(ohne)

    def test_gesprochen_bleibt_das_zuletzt_gelesene(self):
        text = ai_prompt.build(gesprochen=True, desktop=True)
        assert text.index(ai_prompt.DESKTOP) < text.index(ai_prompt.GESPROCHEN)

    def test_block_nennt_die_sandbox_und_die_grenze(self):
        # Der Block ist keine Schranke, aber er muss die drei Dinge sagen, an
        # denen sich das Modell orientiert: Ordner, Servergrenze, Fremdtext.
        assert "Sandbox" in ai_prompt.DESKTOP
        assert "Server bedienst du von hier aus nicht" in ai_prompt.DESKTOP
        assert "Material" in ai_prompt.DESKTOP


class TestWerkzeugkatalog:
    def test_desktop_werkzeuge_stehen_im_katalog_des_anbieters(self):
        from services.ai_action_service import provider_tool_definitions

        namen = {
            eintrag["function"]["name"] for eintrag in provider_tool_definitions()
        }
        assert DESKTOP_TOOLS <= namen

    def test_desktop_werkzeuge_tragen_keinen_server(self):
        """Kein `server_id` im Schema — sonst waere die Grenze eine Bitte."""
        from services.ai_action_service import provider_tool_definitions

        for eintrag in provider_tool_definitions():
            name = eintrag["function"]["name"]
            if name in DESKTOP_TOOLS:
                felder = eintrag["function"]["parameters"]["properties"]
                assert "server_id" not in felder, name


@pytest.mark.parametrize("herkunft", ["panel", "desktop"])
def test_jedes_werkzeug_gehoert_genau_einer_welt(herkunft):
    """Kein Werkzeug faellt durch beide Schnitte — sonst waere es nie nutzbar."""
    aus_panel = herkunft_schnitt(ALLE, "panel")
    aus_desktop = herkunft_schnitt(ALLE, "desktop")
    assert aus_panel | aus_desktop == ALLE
