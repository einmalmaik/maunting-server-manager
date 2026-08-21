"""Die Grenze zwischen Panel und Smart System — eine Richtung, nicht zwei.

Betreiberentscheid vom 21.08.2026: die Desktop-App ist derselbe Zugang wie das
Panel, nur mit einem Rechner daran. Die KI darin bekommt alles, was der
Benutzer darf, **plus** die Werkzeuge fuer seinen Rechner. Hier stand bis dahin
das Gegenteil (aus der App kein Serverwerkzeug, mit Hoster-Neutralitaet
begruendet) — das war eine Fehllesung: gemeint war, dass die App als
*Oberflaeche* keine Serververwaltung zeigt, nicht dass die KI dort weniger darf.

Was bleibt, ist die Gegenrichtung: **aus dem Panel erreicht kein Werkzeug den
Rechner.** Die Desktop-Werkzeuge brauchen die laufende App und einen Menschen
vor der Bestaetigungskarte; aus dem Browser liefen sie in die Frist. Und ein
uebernommener Browser-Tab soll nicht nach Maus und Tastatur greifen koennen.

Zwei Schranken, wie ueberall im Haus:

1. Der **Katalog** bietet gar nicht erst an, was nicht laufen wuerde
   (`herkunft_schnitt`). Das ist Fuehrung.
2. Der **Spiegel** je Aufruf sortiert aus, was trotzdem gerufen wird
   (`_tool_followup_messages`). Das ist die Schranke — ein halluzinierter
   Werkzeugname darf sie nicht unterlaufen.

Und die Herkunft selbst ist eingefroren: ein Lauf, der als Desktop-Lauf
begonnen hat, bleibt einer. Ein unlesbarer Wert faellt auf die **engere**
Seite, und die heisst seit der Umdrehung "panel".
"""

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
    def test_aus_dem_smart_system_bleiben_die_serverwerkzeuge(self):
        erlaubt = herkunft_schnitt(ALLE, "desktop")
        # Der eigentliche Punkt dieser Datei: die App ist kein kleineres Panel.
        assert SERVER_READ_TOOLS <= erlaubt
        assert SERVER_WRITE_TOOLS <= erlaubt
        assert DESKTOP_TOOLS <= erlaubt

    def test_aus_dem_panel_kein_fremder_rechner(self):
        erlaubt = herkunft_schnitt(ALLE, "panel")
        assert not (erlaubt & DESKTOP_TOOLS)
        # Serverwerkzeuge bleiben unangetastet: das Panel ist ihr Ort.
        assert SERVER_READ_TOOLS <= erlaubt
        assert SERVER_WRITE_TOOLS <= erlaubt

    def test_der_desktop_schnitt_nimmt_ueberhaupt_nichts_weg(self):
        """Sonst ist die Umdrehung nur halb passiert."""
        assert herkunft_schnitt(ALLE, "desktop") == ALLE

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
        # Seit der Umdrehung ist "panel" die engere Seite: sie verliert die
        # Desktop-Werkzeuge. Ein Tippfehler darf keinen Rechner ansteuern.
        assert herkunft_aus_zustand({"herkunft": "Desktop "}) == "panel"
        assert herkunft_aus_zustand({"herkunft": "quatsch"}) == "panel"
        assert herkunft_aus_zustand({"herkunft": 7}) == "panel"

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

    def test_block_nennt_die_sandbox_und_den_fremdtext(self):
        # Der Block ist keine Schranke, aber er muss sagen, woran sich das
        # Modell orientiert: der Ordner und die Herkunft dessen, was es liest.
        assert "Sandbox" in ai_prompt.DESKTOP
        assert "Material" in ai_prompt.DESKTOP

    def test_block_verbietet_die_server_nicht_mehr(self):
        # Ein Prompt, der Server verbietet, waehrend der Katalog sie anbietet,
        # ist schlimmer als beides einzeln: das Modell weigert sich mit einer
        # Begruendung, die nicht mehr stimmt.
        assert "Server bedienst du von hier aus nicht" not in ai_prompt.DESKTOP


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


def test_die_app_sieht_den_ganzen_katalog():
    """Frueher war die Vereinigung beider Seiten der ganze Katalog, weil jede
    Seite etwas verlor. Jetzt genuegt die App allein — und das Panel ist genau
    um die Desktop-Werkzeuge kleiner."""
    aus_desktop = herkunft_schnitt(ALLE, "desktop")
    aus_panel = herkunft_schnitt(ALLE, "panel")
    assert aus_desktop == ALLE
    assert aus_panel == ALLE - DESKTOP_TOOLS
