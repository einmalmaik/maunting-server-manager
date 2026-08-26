"""Die drei Wandler des Sprachmodus: Ohr, Trennung, Mund.

Zwischen Mikrofon und Chatlauf steht jetzt Handarbeit, wo vorher OpenAIs
Realtime-API war. Sie besteht aus drei kleinen Teilen, und alle drei haben eine
Eigenschaft gemeinsam: **wenn sie danebenliegen, sieht man keinen Fehler.** Man
hört einen abgeschnittenen Anlaut, ein zerhacktes Wort, eine Pause an der
falschen Stelle — Dinge, die wie ein schlechtes Netz klingen und keine sind.

Genau deshalb stehen sie hier einzeln und nicht nur im Zusammenspiel:

* `ai_voice_vad` — wann hat der Mensch aufgehört zu reden?
* `ai_voice_bridge.Belegfilter` — was wird gesprochen, was gezeigt?
* `ai_tts_elevenlabs._naechstes_stueck` — wann geht ein Stück Text zur Stimme?
"""

from __future__ import annotations

import math
import struct

from services import ai_stt, ai_tts_elevenlabs, ai_voice_bridge, ai_voice_vad


# ── Das Ohr ───────────────────────────────────────────────────────────────


def _ton(sekunden: float, *, pegel: int, frequenz: float = 220.0) -> bytes:
    """Ein Sinuston. Kein Rauschen: ein Sinus hat einen stabilen Effektivwert.

    Mit Zufallsrauschen wäre dieser Test flatterhaft — mal knapp über der
    Schwelle, mal knapp darunter —, und ein flatterhafter Test über eine
    Schwelle ist schlimmer als keiner.
    """
    anzahl = int(sekunden * ai_voice_vad.ABTASTRATE)
    werte = (
        int(pegel * math.sin(2 * math.pi * frequenz * i / ai_voice_vad.ABTASTRATE))
        for i in range(anzahl)
    )
    return struct.pack(f"<{anzahl}h", *werte)


def _stille(sekunden: float) -> bytes:
    return b"\x00\x00" * int(sekunden * ai_voice_vad.ABTASTRATE)


def test_eine_aeusserung_endet_an_der_pause_und_nicht_vorher() -> None:
    """Die Nachlaufzeit ist der einzige Regler, der sich anfühlt.

    Zu kurz, und die KI fällt einem ins Wort, sobald man Luft holt — genau das
    prüft die Mitte dieses Tests: eine Pause von 0,8 Sekunden mitten im Satz
    darf ihn **nicht** beenden.
    """
    erkennung = ai_voice_vad.Pausenerkennung()

    assert erkennung.fuettern(_stille(0.5)) is None
    assert erkennung.fuettern(_ton(0.6, pegel=6000)) is None
    assert erkennung.spricht is True
    # Luft holen. Kürzer als STILLE_SEKUNDEN (1.5s) — der Satz läuft weiter.
    assert erkennung.fuettern(_stille(0.8)) is None
    assert erkennung.fuettern(_ton(0.6, pegel=6000)) is None
    # Und jetzt wirklich fertig (über 1,5s Stille).
    aeusserung = erkennung.fuettern(_stille(2.0))

    assert aeusserung is not None
    assert erkennung.spricht is False
    # Anfang, Pause und zweite Hälfte sind alle drin — die Äusserung ist ein
    # Satz und nicht zwei Bruchstücke.
    assert aeusserung.sekunden > 1.4
    assert aeusserung.abgeschnitten is False


def test_der_anlaut_geht_nicht_verloren() -> None:
    """Ohne Vorlauf fehlt vorne, was zum Überschreiten der Schwelle fehlte."""
    erkennung = ai_voice_vad.Pausenerkennung()
    erkennung.fuettern(_stille(0.5))
    erkennung.fuettern(_ton(0.5, pegel=6000))
    aeusserung = erkennung.fuettern(_stille(2.0))

    assert aeusserung is not None
    # Länger als die reine Rede: der Vorlauf ist mit drin.
    assert aeusserung.sekunden > 0.5


def test_kadenz_skalierung_und_pausenmessung() -> None:
    """Prüft, dass der Kadenzfaktor die Stillegrenzen skaliert und Pausen misst."""
    erkennung = ai_voice_vad.Pausenerkennung(kadenz_faktor=1.5)
    assert erkennung.kadenz_faktor == 1.5

    erkennung.fuettern(_stille(0.5))
    erkennung.fuettern(_ton(0.5, pegel=6000))
    # 1.0s Pause innerhalb der Rede: wird erfasst, beendet den Satz bei Kadenz 1.5 (2.25s Basis) noch nicht
    assert erkennung.fuettern(_stille(1.0)) is None
    assert erkennung.fuettern(_ton(0.5, pegel=6000)) is None
    assert len(erkennung.gemessene_pausen) > 0

    # Dynamische Anpassung
    erkennung.kadenz_anpassen(0.8)
    assert erkennung.kadenz_faktor == 0.8


def test_ist_gedanke_abgeschlossen() -> None:
    """Prüft sprachunabhängig abgeschlossene und unvollständige Satzstrukturen."""
    assert ai_voice_bridge._ist_gedanke_abgeschlossen("Der Server läuft einwandfrei.") is True
    assert ai_voice_bridge._ist_gedanke_abgeschlossen("Kannst du das bitte prüfen?") is True
    assert ai_voice_bridge._ist_gedanke_abgeschlossen("Fertig!") is True
    assert ai_voice_bridge._ist_gedanke_abgeschlossen("Würdest du mal bitte") is False
    assert ai_voice_bridge._ist_gedanke_abgeschlossen("und um...") is False
    assert ai_voice_bridge._ist_gedanke_abgeschlossen("ich hätte gerne,") is False
    assert ai_voice_bridge._ist_gedanke_abgeschlossen("please restart the server and") is False


def test_ein_huster_loest_keine_anfrage_aus() -> None:
    """Zu kurz ist keine Äusserung — und kostet deshalb auch nichts.

    Jede Äusserung ist eine Anfrage an einen Anbieter und steht auf der
    Rechnung. Ein Stuhlrücken, ein Klicken, ein Räuspern: alles laut, nichts
    davon eine Frage.
    """
    erkennung = ai_voice_vad.Pausenerkennung()
    erkennung.fuettern(_stille(0.5))
    erkennung.fuettern(_ton(0.12, pegel=9000))

    assert erkennung.fuettern(_stille(1.0)) is None


def test_ein_stiller_raum_faengt_nicht_von_selbst_an_zu_reden() -> None:
    """Der Grundpegel startet hoch und sinkt — nicht andersherum.

    Von null kommend gälte das erste Mikrofonrauschen als Rede, weil es den
    Grundpegel um ein Vielfaches übersteigt. Die Sitzung begänne dann mit einer
    Äusserung, die niemand gesprochen hat — und mit einer Rechnung dafür.
    """
    erkennung = ai_voice_vad.Pausenerkennung()

    # Leises Rauschen, wie es jedes Mikrofon liefert.
    for _ in range(50):
        assert erkennung.fuettern(_ton(0.1, pegel=40)) is None
    assert erkennung.spricht is False


def test_wer_auflegt_verliert_seinen_letzten_satz_nicht() -> None:
    """`ausklingen()` gibt heraus, was die Nachlaufzeit noch festhält."""
    erkennung = ai_voice_vad.Pausenerkennung()
    erkennung.fuettern(_stille(0.5))
    erkennung.fuettern(_ton(0.8, pegel=6000))

    aeusserung = erkennung.ausklingen()

    assert aeusserung is not None
    assert aeusserung.sekunden > 0.7


# ── Die Trennung ──────────────────────────────────────────────────────────


def test_ein_codeblock_wird_gezeigt_und_nicht_gesprochen() -> None:
    """Der Kern der gesprochenen Fassung von `ai_prompt.BELEGE`.

    Das Modell schreibt im Sprachmodus dasselbe wie im Chat: die Stelle als
    Codeblock, darunter die Deutung. Vorgelesen gehört nur die Deutung — ein
    Codeblock ist gesprochen eine Aneinanderreihung von Satzzeichen, und der
    Betreiber hat ausdrücklich verlangt, dass Logzeilen erscheinen statt
    vorgelesen zu werden.
    """
    filter_ = ai_voice_bridge.Belegfilter()

    gesprochen, belege = filter_.fuettern(
        "Der Server startet nicht.\n"
        "```log\n"
        "[ERROR] Port 25565 already in use\n"
        "```\n"
        "Der Port ist belegt.\n"
    )

    assert "Der Server startet nicht." in gesprochen
    assert "Der Port ist belegt." in gesprochen
    assert "25565" not in gesprochen, "Die Logzeile wurde vorgelesen"
    assert "```" not in gesprochen
    assert len(belege) == 1
    assert belege[0]["zeilen"] == ["[ERROR] Port 25565 already in use"]
    assert belege[0]["quelle"] == "log"


def test_ein_zaun_zwischen_zwei_stuecken_zerreisst_nichts() -> None:
    """Der Text kommt zeichenweise — ein ``` kann auf zwei Stücke fallen.

    Genau daran scheitert die naheliegende Lösung, jedes ankommende Stück für
    sich zu betrachten. Deshalb arbeitet der Filter zeilenweise und hält den
    Rest bis zum nächsten Umbruch fest.
    """
    filter_ = ai_voice_bridge.Belegfilter()
    stuecke = ["Sieh her:\n", "``", "`ini\n", "max-play", "ers=20\n", "``", "`\n", "Zwanzig.\n"]

    gesprochen: list[str] = []
    belege: list[dict] = []
    for stueck in stuecke:
        text, gefunden = filter_.fuettern(stueck)
        gesprochen.append(text)
        belege.extend(gefunden)

    ganz = "".join(gesprochen)
    assert "Sieh her:" in ganz
    assert "Zwanzig." in ganz
    assert "max-players" not in ganz
    assert [beleg["zeilen"] for beleg in belege] == [["max-players=20"]]


def test_ein_offener_codeblock_geht_beim_ausklingen_trotzdem_heraus() -> None:
    """Abgeschnitten ist nicht dasselbe wie ungültig.

    Wird das Modell mitten im Block abgebrochen (Grenze erreicht, Verbindung
    weg), sind die Zeilen, die es schon geschrieben hat, so gültig wie die
    anderen. Sie zu verwerfen hiesse, dem Menschen genau die Stelle
    vorzuenthalten, um die es ging.
    """
    filter_ = ai_voice_bridge.Belegfilter()
    filter_.fuettern("Hier:\n```\nzeile eins\n")

    gesprochen, belege = filter_.ausklingen()

    assert "zeile eins" not in gesprochen
    assert belege == [{"art": "beleg", "quelle": "", "zeilen": ["zeile eins"]}]


def test_gewoehnlicher_text_geht_unveraendert_durch() -> None:
    filter_ = ai_voice_bridge.Belegfilter()

    gesprochen, belege = filter_.fuettern("Alle vier Server laufen.\n")

    assert gesprochen == "Alle vier Server laufen.\n"
    assert belege == []


# ── Der Mund ──────────────────────────────────────────────────────────────


def test_ein_satz_geht_hinaus_sobald_er_fertig_ist() -> None:
    """Der Grund, warum hier ueberhaupt getrennt wird.

    Die Gegenstelle puffert bis 120 Zeichen, bevor sie erzeugt. Wer ihr Zeichen
    fuer Zeichen schickt, wartet also auf 120 Zeichen — bei einer kurzen Antwort
    heisst das: bis zum Schluss. Ein fertiger Satz geht dagegen sofort.
    """
    stueck, rest = ai_tts_elevenlabs._naechstes_stueck(
        "Alle vier Server laufen normal. Und weiter"
    )

    assert stueck == "Alle vier Server laufen normal."
    assert rest.strip() == "Und weiter"


def test_ein_punkt_in_einer_abkuerzung_beendet_keinen_satz() -> None:
    """„z. B." und „Nr. 5" — ueberall ein Punkt, nirgends ein Satzende.

    Ohne Mindestmass ginge nach „z." ein Stueck von zwei Zeichen hinaus, und die
    Stimme spraeche eine eigene, winzige Erzeugung dafuer. Hoerbar als Stocken
    mitten im Satz. Eine Abkuerzungsliste braucht es dafuer nicht.
    """
    stueck, rest = ai_tts_elevenlabs._naechstes_stueck("z. B. der")

    assert stueck == ""
    assert rest == "z. B. der"


def test_ein_langer_nebensatz_haelt_den_ton_nicht_an() -> None:
    """Kein Satzzeichen in Sicht heisst nicht: gar nichts sagen.

    Getrennt wird dann an der letzten Wortgrenze — nie mitten im Wort, sonst
    spricht die Stimme die beiden Haelften als zwei Woerter aus.
    """
    lang = "und " * 80
    stueck, rest = ai_tts_elevenlabs._naechstes_stueck(lang)

    assert stueck
    assert len(stueck) <= ai_tts_elevenlabs.MAX_STUECK_ZEICHEN
    assert not stueck.endswith("un"), "mitten im Wort getrennt"
    assert stueck + " " + rest.strip() == lang.strip()


def test_beim_ausklingen_geht_auch_ein_halber_satz_hinaus() -> None:
    stueck, rest = ai_tts_elevenlabs._naechstes_stueck("Und dann", letzter=True)

    assert stueck == "Und dann"
    assert rest == ""


# ── Der Weg zum hoerenden Modell ──────────────────────────────────────────


def test_der_wav_kopf_beschreibt_genau_das_was_folgt() -> None:
    """Rohes PCM sagt nicht, wie schnell es abgespielt gehoert.

    Ohne Kopf klingt dieselbe Aufnahme je nach Annahme der Gegenstelle zu hoch
    oder zu tief, und das hoerende Modell versteht Kauderwelsch statt einer
    Frage. Der Fehler sieht danach aus wie ein schlechtes Mikrofon.
    """
    pcm = _stille(0.1)
    datei = ai_stt.wav_verpacken(pcm)

    assert datei[:4] == b"RIFF"
    assert datei[8:12] == b"WAVE"
    # Abtastrate und Datenlaenge stehen wirklich drin — und stimmen.
    assert struct.unpack("<I", datei[24:28])[0] == ai_stt.ABTASTRATE
    assert struct.unpack("<I", datei[40:44])[0] == len(pcm)
    assert len(datei) == 44 + len(pcm)


def test_anfuehrungszeichen_um_das_transkript_fallen_weg() -> None:
    """Nachsichtig lesen, streng speichern — dieselbe Regel wie am Werkzeugrand.

    Ein Modell, das den Wortlaut in Anfuehrungszeichen setzt, hat die Aufgabe
    verstanden und die Form verfehlt. Ein Gespraech daran scheitern zu lassen
    waere die falsche Strenge.
    """
    assert ai_stt._saeubern('"Starte den Server"') == "Starte den Server"
    assert ai_stt._saeubern("  Starte   den Server  ") == "Starte den Server"
    # Aber ein Modell, das erzaehlt statt abzuschreiben, wird **nicht**
    # zurechtgeschnitten: das ist kein verungluecktes Transkript, sondern ein
    # anderes Ergebnis, und es zu retten hiesse zu raten.
    erzaehlt = "Der Sprecher fragt, ob der Server laeuft."
    assert ai_stt._saeubern(erzaehlt) == erzaehlt


# ── Die Geduld waechst mit der Laenge ─────────────────────────────────
#
# Gemeldet am 18.08.2026: der Betreiber wurde beim Diktieren eines laengeren
# Auftrags mitten im Satz abgeschnitten, weil er kurz Luft geholt hat. Sein
# Bild dafuer: "ich erzaehle was, trinke einen Schluck, das dauert vielleicht
# 10-20 Sekunden". Gleichzeitig soll ein kurzes "Ja" weiterhin sofort
# durchgehen — eine feste Nachlaufzeit kann nur eines von beidem.


def test_ein_kurzes_ja_geht_weiterhin_sofort_durch() -> None:
    """Die Geduld darf die schnelle Antwort nicht ausbremsen."""
    erkennung = ai_voice_vad.Pausenerkennung()

    erkennung.fuettern(_stille(0.5))
    erkennung.fuettern(_ton(0.4, pegel=6000))
    assert erkennung.spricht is True

    # Knapp ueber STILLE_SEKUNDEN (1.5s): bei kurzer Rede reicht das.
    aeusserung = erkennung.fuettern(_stille(1.7))

    assert aeusserung is not None, (
        "ein kurzes Ja wartet auf die volle Geduld — dann fuehlt sich jede "
        "schnelle Antwort langsam an"
    )


def test_eine_atempause_im_diktat_beendet_den_satz_nicht() -> None:
    """Nach laengerem Sprechen darf die Pause deutlich laenger sein."""
    erkennung = ai_voice_vad.Pausenerkennung()

    erkennung.fuettern(_stille(0.5))
    # Lange genug fuer die volle Geduld (STILLE_VOLL_AB_SEKUNDEN).
    erkennung.fuettern(_ton(10.0, pegel=6000))
    assert erkennung.spricht is True

    # Eine Pause von 2.0s beendet ein langes Diktat noch nicht (Max ist 4.0s).
    assert erkennung.fuettern(_stille(2.0)) is None, (
        "die Atempause hat den Satz beendet — genau der gemeldete Fehler"
    )

    # Weiterreden geht, und der Satz bleibt einer.
    erkennung.fuettern(_ton(1.0, pegel=6000))
    # Erst bei Stille über der Max-Schwelle fertig.
    aeusserung = erkennung.fuettern(_stille(4.5))

    assert aeusserung is not None
    assert aeusserung.sekunden > 11.0, "der Satz wurde in Stuecke zerlegt"


def test_die_geduld_waechst_und_bleibt_begrenzt() -> None:
    """Die Grenze steigt mit der lauten Zeit — und nicht darueber hinaus.

    Ohne Deckel wuerde ein langes Diktat die Wartezeit immer weiter
    verlaengern, bis am Ende gar keine Antwort mehr kaeme.

    Geprüft wird die Rampe und nicht nur ihre Richtung: nach einer Sekunde Rede
    muss die Grenze **echt** über dem Ausgangswert und **echt** unter dem
    Deckel liegen. Für den ersten Punkt stand hier ein `> oder ==`, also ein
    `>=` — das hätte auch eine Grenze bestanden, die bis
    `STILLE_VOLL_AB_SEKUNDEN` unverändert bleibt und dann springt. Genau dieses
    Sprunghafte soll die Interpolation vermeiden: eine Schwelle, an der sich
    das Verhalten plötzlich ändert, fühlt sich für den Sprechenden wie ein
    Fehler an.
    """
    erkennung = ai_voice_vad.Pausenerkennung()

    # Frisch: der Ausgangswert.
    assert erkennung._stille_grenze() == erkennung._stille_rahmen

    erkennung.fuettern(_stille(0.5))
    erkennung.fuettern(_ton(1.0, pegel=6000))
    nach_kurz = erkennung._stille_grenze()

    erkennung.fuettern(_ton(6.0, pegel=6000))
    nach_lang = erkennung._stille_grenze()

    assert nach_kurz > erkennung._stille_rahmen, (
        "eine Sekunde Rede lässt die Geduld unverändert — die Grenze wächst "
        "nicht mit, sondern schaltet um"
    )
    assert nach_kurz < erkennung._stille_rahmen_max, (
        "eine Sekunde Rede springt schon auf den Deckel — dann ist die Rampe "
        "eine Schwelle, und ein kurzes Ja wartet die volle Geduld ab"
    )
    assert nach_lang > nach_kurz, "die Geduld ist nicht mitgewachsen"
    assert nach_lang <= erkennung._stille_rahmen_max, "die Geduld kennt keinen Deckel"


def test_ein_langes_diktat_wird_nicht_mitten_im_satz_zerteilt() -> None:
    """`MAX_SEKUNDEN` schneidet erst weit jenseits eines normalen Auftrags.

    Die Schranke bleibt noetig (ein dauerlautes Mikrofon soll nicht endlos
    aufnehmen), darf aber kein zusammenhaengendes Diktat treffen. Dass sie
    zuschlaegt, merkt der Sprechende naemlich nicht: `abgeschnitten` wertet
    die Bruecke gar nicht aus.
    """
    assert ai_voice_vad.MAX_SEKUNDEN >= 90.0, (
        "unter anderthalb Minuten wird ein diktierter Auftrag zerteilt"
    )
