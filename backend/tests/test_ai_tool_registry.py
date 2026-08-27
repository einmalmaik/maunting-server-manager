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
        if art in {"server_read", "global_read", "ask", "delegation"}
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
    assert ai_tool_registry.DELEGATION_TOOLS == {
        name for name, art in aus_tabelle.items() if art == "delegation"
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

    `propose_server_blueprint_switch` ist der juengste Eintrag und die
    Korrektur einer Fehleinschaetzung, die genau umgekehrt begruendet war: er
    galt als umkehrbar, "weil zwingend ein Backup angelegt wird". Das Backup
    gibt es wirklich — es macht den Vorgang aber wiederherstellbar, nicht
    harmlos. `switch_server_blueprint` ruft `wipe_server_root` und loescht das
    **gesamte** Serververzeichnis: Welt, Configs, Mods. Der Weg zurueck ist eine
    Wiederherstellung, die selbst Stunden dauert — genau das Kriterium, unter
    `propose_email_send`, `propose_calendar_event_create` und
    `propose_calendar_event_delete` sind externe Interaktionen mit fremden
    Systemen (E-Mail-Empfaenger, Kalenderserver). Eine versendete E-Mail laesst
    sich nicht zurueckholen ("Draft & Confirm"-Invariante).
    """
    gebaut = {
        name for name, spec in ai_tool_registry.WERKZEUGE.items()
        if spec.immer_bestaetigen
    }
    assert gebaut == {
        "propose_server_delete",
        "propose_blueprint_delete",
        "propose_backup_restore",
        "propose_server_blueprint_switch",
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

    Die drei Ausnahmen sind keine Nachlaessigkeit, sondern haben einen
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
    * `propose_calendar_event_delete` — ein Kalendertermin kann im Chat oder Kalender
      jederzeit neu angelegt oder angepasst werden.

    Faellt dieser Test, ist das die Frage: verschwindet hier etwas, das niemand
    zurueckholt? Dann gehoert `immer_bestaetigen` an die Zeile. Sonst gehoert
    der Grund hierher.
    """
    RUECKWEG_NACHGEWIESEN = {
        "propose_file_delete",
        "propose_task_delete",
        "propose_calendar_event_delete",
    }

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

    Die Rechte sind bewusst schon vorhandene, und sie spiegeln die Panel-Routen
    fuer **denselben Vorgang** — nicht ein aehnliches Werkzeug. Hier stand
    `server.config.write` fuer die Reparatur („dasselbe Recht wie am
    Panel-Knopf") und `server.files.write` fuer das Loeschen. Beides war
    nachgeprueft falsch: einen Reparatur-Knopf gibt es im Panel nicht, die
    einzige Route, die Ports aendert, verlangt `server.network.manage`, der
    Root-Chown laeuft dort nur innerhalb einer `server.files.write`-Operation —
    und Loeschen verlangt am Panel `server.files.delete`, nicht das
    Schreibrecht. Der Chat war damit der Umweg, auf dem ein Benutzer ohne
    Loesch- bzw. Netzrecht doch loeschte bzw. Firewallregeln umbaute. Die
    Reparatur haengt deshalb wie der Lebenszyklus am **Vorgang**
    (`ai_proposal_service._permission_for` am `action`-Argument), nicht an
    einer Tabellenzeile.
    """
    from services.ai_proposal_service import _permission_for

    reparatur = ai_tool_registry.WERKZEUGE["propose_server_repair"]
    loeschen = ai_tool_registry.WERKZEUGE["propose_file_delete"]

    assert reparatur.art == "server_write"
    # Kein Tabellenrecht: die Zuordnung steht am Vorgang. `angebot` nennt beide,
    # weil eines genuegt, damit das Werkzeug angeboten wird.
    assert reparatur.recht is None
    assert reparatur.angebot == ("server.files.write", "server.network.manage")
    assert _permission_for(
        "propose_server_repair", {"action": "repair_permissions"}
    ) == ("server.files.write",)
    assert _permission_for(
        "propose_server_repair", {"action": "reallocate_port"}
    ) == ("server.network.manage",)
    # Ein unbekannter Vorgang verlangt die Vereinigung beider Rechte — strenger
    # als jede gueltige Wahl. Kein Recht (die leere Menge) waere hier falsch:
    # dann braeche die Ausfuehrung schon an der Rechtepruefung ab, statt die
    # manipulierte Nutzlast als `AI_ACTION_TOOL_NOT_ALLOWED` mit `failed` zu
    # beenden (siehe `_permission_for`-Docstring).
    assert _permission_for("propose_server_repair", {"action": "anders"}) == (
        "server.files.write", "server.network.manage",
    )
    assert reparatur.recht_global is False
    assert reparatur.immer_bestaetigen is False

    assert loeschen.art == "server_write"
    assert loeschen.recht == "server.files.delete"
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
    die Servererstellung (Reichweite ueber den Vorfall hinaus) und `web_search` —
    der Name eines selbstgebauten Servers hat draussen nichts zu suchen, schon
    gar nicht, wenn ihn niemand gefragt hat.

    Der **Blueprint-Wechsel** stand einmal in dieser Aufzaehlung und ist daraus
    verschwunden, ohne dass sich etwas gelockert haette — im Gegenteil: er
    traegt jetzt `immer_bestaetigen` und faellt damit unter die erste,
    abgeleitete Regel. Das ist die bessere Stelle. Eine abgeleitete Sperre gilt
    auch fuer das naechste Werkzeug, das jemand aehnlich baut; eine
    ausgeschriebene gilt nur fuer den Namen, der dasteht.

    Die **Ableitung eines Blueprints** dagegen ist bewusst hinzugekommen. Sie
    legt eine neue Datei an und ruehrt keinen Server an — das ist der Weg fuer
    den Fall, in dem wirklich die Vorlage falsch ist. Was noch nichts tut, ist
    sie in Betrieb zu nehmen, und genau dieser Schritt braucht weiterhin einen
    Menschen.
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
    genau fünf, jedes mit Grund (ai_tool_registry.py bei
    `GUARDIAN_BACKUP_PFLICHT_TOOLS`):

    * `propose_backup` wegen des Zirkels,
    * `propose_server_lifecycle`, weil ein Neustart keine Datei ändert und die
      Pflicht ihn ausgerechnet dann sperren würde, wenn die volle Platte kein
      Backup mehr zulässt,
    * `propose_guardian_tuning`, weil es eine Spalte am Server ändert und keine
      Datei auf ihm — der Rückweg ist `reset` und kostet nichts,
    * `propose_blueprint_change`, weil es eine neue Datei im Panel anlegt und
      keinen Server anfasst; ein Serverbackup bewiese darüber gar nichts,
    * `propose_mod_toggle`, weil der Schalter keine Datei berührt: die Mod
      bleibt liegen, nur die Startzeile ändert sich. Es gäbe nichts
      zurückzuspielen — und eine kaputte Mod auszuschalten ist oft genau der
      Weg zurück, den ein Server ohne geprüftes Backup noch hat.

    Die Liste wächst hier bewusst mit — sie ist die Stelle, an der man sich
    festlegen muss, statt ein Werkzeug stillschweigend an der Schranke vorbei
    in den unbeaufsichtigten Lauf zu heben.
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
        "propose_guardian_tuning",
        "propose_blueprint_change",
        "propose_mod_toggle",
    }


# ── Gehirn und Worker (docs/agentic-framework.md) ─────────────────────────


def test_das_gehirn_hat_nie_server_werkzeuge() -> None:
    """Die Rollentrennung ist die Sicherheitsinvariante von v3.

    Das Gehirn ist die schnelle, dauerpraesente Instanz. Sein Katalog besteht
    aus drei Dingen: dem Gedaechtnis (der Charakter gehoert ihm), den drei
    Handgriffen, Auftraege zu deklarieren, ihnen Antworten zuzustellen und sie
    einzufangen — und seit dem 23.08.2026 dem Rechner, vor dem der Benutzer
    sitzt (`GEHIRN_DESKTOP`). Kein Lese-, kein Schreib-, kein Frage-Werkzeug
    eines Servers — auch kein kuenftiges: die Menge ist eine Aufzaehlung, kein
    Filter.

    Der Satz "das Gehirn darf strukturell keine Aussenwirkung entfalten" stand
    hier bis zu diesem Tag und ist bewusst weg: `desktop_steuern` bewegt eine
    echte Maus. Was traegt, ist die engere und wahre Fassung darunter — kein
    Server, kein Vorschlag.
    """
    assert ai_tool_registry.GEHIRN_TOOLS == (
        ai_tool_registry.MEMORY_TOOLS
        | {"worker_start", "worker_cancel", "worker_antwort"}
        | {"desktop_system", "desktop_steuern", "desktop_launch_app"}
        | ai_tool_registry.CHAT_INTERACTION_TOOLS
    )
    assert ai_tool_registry.GEHIRN_TOOLS & ai_tool_registry.SERVER_READ_TOOLS == set()
    assert ai_tool_registry.GEHIRN_TOOLS & ai_tool_registry.SERVER_WRITE_TOOLS == set()
    assert (
        ai_tool_registry.GEHIRN_TOOLS & ai_tool_registry.WRITE_TOOLS
        == ai_tool_registry.CHAT_INTERACTION_TOOLS & ai_tool_registry.WRITE_TOOLS
    )
    assert "ask_user" not in ai_tool_registry.GEHIRN_TOOLS
    assert "web_search" not in ai_tool_registry.GEHIRN_TOOLS


def test_dem_gehirn_gehoert_das_sehen_und_zeigen_nicht_die_arbeit() -> None:
    """Die Grenze innerhalb der Desktop-Werkzeuge.

    Sehen, Steuern und Programme starten gehoert dem direkten Computer-Use am Rechner.
    Dateien in der Sandbox und System-Aufraeumen bleiben beim Worker.
    """
    assert ai_tool_registry.GEHIRN_DESKTOP <= ai_tool_registry.DESKTOP_TOOLS
    for arbeit in ("desktop_dateien", "desktop_aufraeumen"):
        assert arbeit in ai_tool_registry.DESKTOP_TOOLS
        assert arbeit not in ai_tool_registry.GEHIRN_TOOLS


def test_aus_dem_panel_bleibt_das_gehirn_ohne_rechner() -> None:
    """Zwei Schnitte hintereinander, und der zweite ist der scharfe.

    `GEHIRN_TOOLS` sagt, was dem Gehirn gehoert; `herkunft_schnitt` sagt, was
    diese Herkunft ueberhaupt erreicht. Aus dem Browser bleibt der Desktop-Katalog
    des Gehirns draußen — dort sitzt niemand vor der Bestaetigungskarte.
    """
    aus_dem_panel = ai_tool_registry.herkunft_schnitt(
        ai_tool_registry.GEHIRN_TOOLS, "panel"
    )
    assert aus_dem_panel == (
        ai_tool_registry.MEMORY_TOOLS
        | {"worker_start", "worker_cancel", "worker_antwort"}
        | ai_tool_registry.CHAT_INTERACTION_TOOLS
    )
    aus_der_app = ai_tool_registry.herkunft_schnitt(
        ai_tool_registry.GEHIRN_TOOLS, "desktop"
    )
    assert ai_tool_registry.GEHIRN_DESKTOP <= aus_der_app


def test_keine_worker_tiefe_ueber_eins() -> None:
    """Ein Auftrag, der Auftraege anlegt, waere ein Auftrag ohne Ende.

    Drei Laufarten duerfen nie delegieren: der Worker selbst (Ausschluss ueber
    `worker_ausschluss()`), die Guardian-Heilung und die stehenden Aufgaben
    (beide ausgeschriebene Mengen — die neuen Werkzeuge duerfen dort nie
    hineingeraten).
    """
    steuerung = ai_tool_registry.WORKER_STEUERUNG
    assert steuerung <= ai_tool_registry.worker_ausschluss()
    assert steuerung & ai_tool_registry.GUARDIAN_HEILUNG_TOOLS == set()
    assert steuerung & ai_tool_registry.aufgaben_tools("act") == set()


def test_der_worker_fragt_ueber_die_meldestelle_nie_direkt() -> None:
    """`ask_user` faellt namentlich weg, `worker_frage` bleibt.

    Der Ausschluss darf nie als `-= ASK_TOOLS` geschrieben werden — dann
    verschwaende die Worker-Frage gleich mit, und der Worker koennte gar nicht
    mehr fragen. Dieser Test haelt beide Haelften fest.
    """
    ausschluss = ai_tool_registry.worker_ausschluss()
    assert "ask_user" in ausschluss
    assert "worker_frage" not in ausschluss
    assert "wait_until" not in ausschluss
    assert "worker_frage" in ai_tool_registry.ASK_TOOLS


def test_delegation_traegt_weder_recht_noch_bestaetigung() -> None:
    """Ein `recht` an einer Delegation waere eine Schranke, die nie greift.

    Der Vorschlagspfad prueft `recht`, der autonome Modus `immer_bestaetigen` —
    durch beide kommt eine Delegation nie. `__post_init__` weist beides ab,
    damit niemand eine Pruefung hinschreibt, die nirgends laeuft; das
    Angebots-Gate leistet `angebot`.
    """
    with pytest.raises(ValueError, match="delegation"):
        ai_tool_registry.Werkzeug("delegation", recht="ai.background.use")
    with pytest.raises(ValueError, match="delegation"):
        ai_tool_registry.Werkzeug("delegation", immer_bestaetigen=True)

    for name in ai_tool_registry.DELEGATION_TOOLS:
        spec = ai_tool_registry.WERKZEUGE[name]
        assert spec.recht is None and not spec.immer_bestaetigen


def test_die_worker_steuerung_haengt_am_hintergrundrecht() -> None:
    """Ohne `ai.background.use` sieht das Modell die Worker-Werkzeuge nicht.

    Das ist der Fallback aus Abschnitt 5 der Doku: wem das Recht fehlt, dessen
    Chat arbeitet wie bisher in einem Lauf. `wait_until` und `worker_frage`
    tragen bewusst kein Angebotsrecht — sie existieren nur im Laufart-Schnitt
    eines Worker-Laufs, und ein Rechteschluessel waere die falsche Achse.
    """
    for name in ("worker_start", "worker_cancel", "worker_antwort"):
        assert ai_tool_registry.angebotsrechte(name) == ("ai.background.use",)
    for name in ("wait_until", "worker_frage"):
        assert ai_tool_registry.angebotsrechte(name) == ()
