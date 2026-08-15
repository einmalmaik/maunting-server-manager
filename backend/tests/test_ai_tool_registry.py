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

    `propose_blueprint_delete` ist der juengste Eintrag, und er ist genau die
    bewusste Entscheidung, fuer die diese Liste ausgeschrieben dasteht: als das
    Werkzeug gebaut wurde, fehlte die Sperre, und der Test fiel nicht auf, weil
    niemand ihn angefasst hatte. `delete_community_blueprint` entfernt die Datei
    per `unlink`; einen Versionsschnappschuss wie bei den Serverdateien gibt es
    hier nicht, und die Registry haelt nur, was auf der Platte liegt. Der einzige
    Weg zurueck ist ein Export, den vielleicht jemand gemacht hat — und
    "vielleicht" ist kein nachgewiesener Rueckweg. Damit greift dasselbe
    Kriterium wie bei `propose_server_delete`.
    """
    gebaut = {
        name for name, spec in ai_tool_registry.WERKZEUGE.items()
        if spec.immer_bestaetigen
    }
    assert gebaut == {
        "propose_server_delete",
        "propose_blueprint_delete",
        "propose_backup_restore",
        "propose_hoster_integration",
        "propose_hoster_product",
        "propose_ai_tarif_role",
    }


def test_ein_loeschwerkzeug_traegt_die_sperre_oder_nennt_seinen_rueckweg() -> None:
    """Die Zusicherung, die den Befund verhindert haette — kuenftig statt rueckwirkend.

    Der Test darueber zaehlt auf, was heute in der Sperre steht; er faellt auf,
    wenn jemand etwas hinzufuegt. Er faellt aber **nicht** auf, wenn jemand ein
    neues Loeschwerkzeug baut und die Sperre schlicht vergisst — dann sieht die
    ausgeschriebene Liste weiterhin so aus, wie sie soll, und ein
    `propose_..._delete` laeuft im autonomen Modus ohne Rueckfrage durch. Genau
    das ist bei `propose_blueprint_delete` passiert.

    Deshalb hier die Regel statt der Aufzaehlung: wer ein Werkzeug auf `_delete`
    tauft, traegt `immer_bestaetigen` — oder er traegt sich unten ein und sagt
    dabei, wo der Rueckweg liegt. Ein Name ist kein Beweis, aber er ist der
    einzige Hinweis, den ein neues Werkzeug von sich aus gibt, und diese
    Zusicherung macht daraus eine Entscheidung, die jemand treffen muss.

    Die beiden Ausnahmen sind keine Nachlaessigkeit, sondern haben einen
    nachpruefbaren Rueckweg im Code:

    * `propose_file_delete` — im Heilungslauf laeuft es ueberhaupt nur mit einem
      nachweislich geglueckten Backup, das juenger ist als der Vorfall
      (`ai_proposal_service._verlangt_gesichertes_backup`, geprueft beim Anlegen
      und noch einmal vor der Ausfuehrung); im Chat ist der
      Versionsschnappschuss aus `file_history_service` **Vorbedingung** —
      `delete_server_text` loescht nicht, wenn er ausbleibt.
    * `propose_task_delete` — eine stehende Aufgabe ist eine Datenbankzeile mit
      Zeitplan und Prompt. Sie wieder anzulegen kostet einen Vorschlag, keine
      Wiederherstellung; es geht nichts verloren, das ausserhalb der Zeile
      existiert.

    Faellt dieser Test, ist das die Frage: verschwindet hier etwas, das niemand
    zurueckholt? Dann gehoert `immer_bestaetigen` an die Zeile. Sonst gehoert
    der Grund hierher.
    """
    RUECKWEG_NACHGEWIESEN = {"propose_file_delete", "propose_task_delete"}

    loeschwerkzeuge = {
        name for name in ai_tool_registry.WERKZEUGE if name.endswith("_delete")
    }
    assert loeschwerkzeuge, "kein Werkzeug auf _delete — dann prueft das hier nichts"

    ohne_sperre = {
        name for name in loeschwerkzeuge
        if not ai_tool_registry.WERKZEUGE[name].immer_bestaetigen
    }
    assert ohne_sperre == RUECKWEG_NACHGEWIESEN


def test_die_beiden_heilungswerkzeuge_sind_eingeordnet() -> None:
    """`propose_server_repair` und `propose_file_delete` sind autonomiefaehig.

    Das sieht beim Loeschwerkzeug nach einem Widerspruch aus, ist aber keiner:
    das Kriterium der Registry ist **Unumkehrbarkeit**, nicht gefuehltes Risiko.
    `propose_server_delete` und `propose_backup_restore` stehen in der Sperre,
    weil danach kein Backup mehr hilft — das eine nimmt die Backups mit, das
    andere ueberschreibt einen Stand, von dem es nie eines gab.

    Beim Loeschen einer einzelnen Datei ist es umgekehrt: das Werkzeug laeuft im
    Heilungslauf ueberhaupt nur, wenn ein nachweislich geglecktes Backup
    vorliegt, das **juenger als der Vorfall** ist. Der Weg zurueck ist damit Teil
    des Vorgangs — dieselbe Begruendung, aus der der Blueprint-Wechsel
    autonomiefaehig ist, obwohl er das ganze Verzeichnis leert.

    Wichtig ist, **wo** dieser Beweis liegt: in
    `ai_proposal_service._verlangt_gesichertes_backup`, geprueft beim Anlegen und
    noch einmal vor der Ausfuehrung. Nicht in einer Prompt-Regel. Eine Regel im
    Prompt ist eine Bitte an ein Modell, dessen Eingaben aus Logzeilen eines
    Servers stammen, auf dem Fremde spielen; sie kann die Sperre hier nicht
    tragen. Wandert die Schranke je aus dem Vorschlagspfad in den Prompt, ist
    diese Einordnung falsch geworden — und dieser Test die Stelle, an der das
    auffaellt.

    Die Rechte sind bewusst schon vorhandene: `server.config.write` ist dasselbe
    Recht wie am Panel-Knopf, hinter dem dieselben Reparaturfunktionen liegen,
    `server.files.write` dasselbe wie beim Schreiben derselben Datei. Ein eigens
    erfundenes Recht haette eine Handlung mit zwei Rechten erzeugt — der Fehler,
    der bei `propose_server_blueprint_switch` zweimal gemacht wurde.
    """
    reparatur = ai_tool_registry.WERKZEUGE["propose_server_repair"]
    loeschen = ai_tool_registry.WERKZEUGE["propose_file_delete"]

    assert reparatur.art == "server_write"
    assert reparatur.recht == "server.config.write"
    assert reparatur.recht_global is False
    assert reparatur.immer_bestaetigen is False

    assert loeschen.art == "server_write"
    assert loeschen.recht == "server.files.write"
    assert loeschen.recht_global is False
    assert loeschen.immer_bestaetigen is False

    # Und dieselbe Aussage noch einmal ueber die abgeleiteten Mengen: wer die
    # Zeile spaeter umhaengt, faellt auch dann auf, wenn er die Spalten oben
    # unberuehrt laesst.
    assert {"propose_server_repair", "propose_file_delete"} <= (
        ai_tool_registry.SERVER_WRITE_TOOLS
    )
    assert {"propose_server_repair", "propose_file_delete"} & (
        ai_tool_registry.ALWAYS_CONFIRM_TOOLS
    ) == set()


def test_die_heilungsmenge_kennt_nur_wirklich_vorhandene_werkzeuge() -> None:
    """Ein Name in der Heilungsmenge, den es nicht gibt, waere eine Luege.

    `GUARDIAN_HEILUNG_TOOLS` ist die Allowlist eines Laufs, den **kein Mensch
    angestossen hat**. Ein Tippfehler darin faellt nirgends auf: die Menge wird
    nur zum Filtern benutzt, und ein Filter gegen einen Namen, den es nicht gibt,
    laesst schlicht ein Werkzeug weniger durch. Das Modell bekommt dann mitten im
    Vorfall ein Werkzeug nicht angeboten, das jemand ihm ausdruecklich geben
    wollte — und niemand erfaehrt davon.

    Echte Teilmenge und nicht Gleichheit: die Heilung darf per Bauart weniger als
    der Chat. Waeren beide Mengen gleich, waere die Aufzaehlung sinnlos geworden.
    """
    assert ai_tool_registry.GUARDIAN_HEILUNG_TOOLS < set(ai_tool_registry.WERKZEUGE)


def test_die_heilung_kann_nichts_dauerhaftes_und_nichts_fremdes() -> None:
    """Die Ausschluesse sind die eigentliche Zusage dieser Menge.

    Der Lauf beginnt nicht mit der Bitte eines Menschen, sondern mit einem
    Ereignis auf einem Server, auf dem Fremde spielen. Was das Modell dort liest —
    Logzeilen, Dateiinhalte — kann jemand geschrieben haben, der genau darauf
    hofft. Der Prompt sagt dem Modell, es solle Weisungen darin nicht befolgen;
    diese Menge sorgt dafuer, dass es sie auch dann nicht **kann**, wenn es sie
    befolgen wollte.

    Wo es geht, wird der Ausschluss abgeleitet statt abgeschrieben — dann gilt er
    auch fuer ein Werkzeug, das es heute noch nicht gibt:

    * `ALWAYS_CONFIRM_TOOLS` — was ein Mensch selbst im autonomen Chat bestaetigen
      muss, darf ein unbeaufsichtigter Lauf erst recht nicht. Das deckt
      `propose_server_delete`, `propose_backup_restore`, die drei
      Hoster-Werkzeuge und jeden kuenftigen Platzhalter aus
      `GEPLANT_IMMER_BESTAETIGEN` mit ab.
    * `MEMORY_TOOLS` und `SKILL_TOOLS` — aus einem Vorfall soll sich das Modell
      nichts Dauerhaftes anlernen. Sonst waere ein praeparierter Logeintrag der
      Weg, eine Weisung in jeden spaeteren Chat des Benutzers zu tragen.

    Ausgeschrieben bleiben nur die, die unter keine der beiden Regeln fallen:
    die Servererstellung (Reichweite ueber den Vorfall hinaus), beide
    Blueprint-Werkzeuge (der Wechsel leert das Verzeichnis) und `web_search` —
    der Name eines selbstgebauten Servers hat draussen nichts zu suchen, schon
    gar nicht, wenn ihn niemand gefragt hat.
    """
    heilung = ai_tool_registry.GUARDIAN_HEILUNG_TOOLS

    assert heilung & ai_tool_registry.ALWAYS_CONFIRM_TOOLS == set()
    assert heilung & ai_tool_registry.MEMORY_TOOLS == set()
    assert heilung & ai_tool_registry.SKILL_TOOLS == set()

    # Die Hoster-Anbindung als Ganzes, nicht nur ihr schreibender Teil: auch das
    # Lesen der Shop-Einrichtung hat in einem Vorfall nichts verloren.
    hoster = {name for name in ai_tool_registry.WERKZEUGE if "hoster" in name}
    assert hoster  # sonst prueft die Zeile darunter nichts
    assert heilung & (hoster | {"propose_ai_tarif_role"}) == set()

    for ausgeschlossen in (
        "propose_server_create",
        "propose_server_delete",
        "propose_blueprint_change",
        "propose_server_blueprint_switch",
        "web_search",
    ):
        assert ausgeschlossen in ai_tool_registry.WERKZEUGE, (
            f"{ausgeschlossen} wurde umbenannt — der Ausschluss zeigt ins Leere"
        )
        assert ausgeschlossen not in heilung


def test_die_backup_pflicht_gilt_nur_fuer_erreichbare_werkzeuge() -> None:
    """Eine Backup-Pflicht auf ein gesperrtes Werkzeug waere tote Regel.

    `GUARDIAN_BACKUP_PFLICHT_TOOLS` wird ausschliesslich im Heilungslauf
    ausgewertet. Steht dort ein Werkzeug, das die Heilung gar nicht aufrufen
    darf, sieht die Regel nach Schutz aus und greift nie — schlimmer noch: sie
    verdeckt, dass fuer ein tatsaechlich erreichbares Werkzeug keine steht.

    `propose_backup` fehlt bewusst. Es unter Backup-Pflicht zu stellen waere ein
    Zirkel: das Modell braeuchte ein Backup, um ein Backup anlegen zu duerfen,
    und der einzige Ausweg aus einem Server ohne geprueftes Backup waere zu.

    Die zweite Zusicherung geht die andere Richtung, und sie ist die
    sicherheitsrelevante: **jedes** schreibende Werkzeug der Heilung steht hinter
    der Schranke. Ohne sie fällt ein künftig aufgenommenes Werkzeug still
    durch — es liefe im unbeaufsichtigten Lauf gegen einen Kundenserver, ohne
    dass ein Rückweg nachgewiesen wäre, und kein Test würde rot. Draußen stehen
    genau zwei, beide mit Grund (ai_tool_registry.py bei
    `GUARDIAN_BACKUP_PFLICHT_TOOLS`): `propose_backup` wegen des Zirkels und
    `propose_server_lifecycle`, weil ein Neustart keine Datei ändert und die
    Pflicht ihn ausgerechnet dann sperren würde, wenn die volle Platte kein
    Backup mehr zulässt.
    """
    assert (
        ai_tool_registry.GUARDIAN_BACKUP_PFLICHT_TOOLS
        <= ai_tool_registry.GUARDIAN_HEILUNG_TOOLS
    )
    assert "propose_backup" not in ai_tool_registry.GUARDIAN_BACKUP_PFLICHT_TOOLS
    assert "propose_backup" in ai_tool_registry.GUARDIAN_HEILUNG_TOOLS
    assert (
        ai_tool_registry.GUARDIAN_HEILUNG_TOOLS & ai_tool_registry.WRITE_TOOLS
    ) - ai_tool_registry.GUARDIAN_BACKUP_PFLICHT_TOOLS == {
        "propose_backup",
        "propose_server_lifecycle",
    }
