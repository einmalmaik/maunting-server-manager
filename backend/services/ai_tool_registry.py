"""Eine Tabelle, aus der alle Werkzeugmengen abgeleitet werden.

Vorher standen die Zugehoerigkeiten in zehn von Hand gepflegten Mengen —
`SERVER_READ_TOOLS`, `GLOBAL_READ_TOOLS`, `WRITE_TOOLS`, `MEMORY_TOOLS`,
`SKILL_TOOLS`, `ASK_TOOLS`, `ALWAYS_CONFIRM_TOOLS` und mehr. Ein neues Werkzeug
brauchte damit Eintraege an mehreren Stellen, und eine vergessene fiel erst zur
Laufzeit auf: das Modell rief etwas auf, das der Allowlist-Pruefung nicht
bekannt war, und der Stream endete mit einem Fehler.

Jetzt gibt es **eine** Zeile je Werkzeug. Die Mengen sind Ableitungen, keine
Quellen. Und `_function()` prueft beim Definieren gegen diese Tabelle: ein
Werkzeug ohne Zeile fliegt sofort auf, nicht erst beim ersten Aufruf.

Bewusst **keine** Handler in dieser Tabelle. Die Ausfuehrungen brauchen
unterschiedliche Signaturen — serverbezogene bekommen den aufgeloesten Server,
globale nicht — und ein gemeinsamer Nenner dafuer waere eine Abstraktion, die
mehr verdeckt als sie ordnet. Die Tabelle ordnet die *Klassifikation*, nicht
den Aufruf.
"""

from __future__ import annotations

from dataclasses import dataclass


# Die Art entscheidet ueber alles Weitere:
#
# - `server_read`  — liest, braucht eine `server_id`, laeuft ueber `_resolve_server`
# - `global_read`  — liest ohne Serverbezug
# - `server_write` — erzeugt einen bestaetigungspflichtigen Vorschlag zu einem Server
# - `global_write` — erzeugt einen Vorschlag ohne Serverbezug (Servererstellung)
# - `ask`          — beendet den Zug und uebergibt an den Menschen
ARTEN = ("server_read", "global_read", "server_write", "global_write", "ask")


@dataclass(frozen=True)
class Werkzeug:
    """Die Einordnung eines Werkzeugs — nicht seine Definition.

    ``gruppe`` fasst thematisch zusammen, was die Oberflaeche unterschiedlich
    darstellt: Gedaechtnis- und Skill-Aufrufe bekommen im Verlauf ein eigenes
    Symbol statt des allgemeinen Werkzeugsymbols.

    ``immer_bestaetigen`` schliesst ein Werkzeug vom autonomen Modus aus, auch
    bei erteilter Freigabe. Das Kriterium ist **Unumkehrbarkeit**, nicht Risiko:
    was die KI selbst wieder zurueckstellen kann, darf sie im autonomen Modus
    tun; was Daten vernichtet, die niemand zurueckholt, fragt immer.

    Die Unterscheidung ist ausdrueckliche Vorgabe des Betreibers ("im autonomen
    Modus wird alles automatisch bestaetigt, ausser Loeschvorgaenge") und
    ersetzt eine frueher gefuehlte Einteilung nach "das klingt heikel". Nach
    Gefuehl standen Blueprint-Wechsel und Bind-IP-Aenderung in der Sperre,
    obwohl beide umkehrbar sind — der Wechsel legt sogar zwingend ein Backup an,
    bevor er etwas anfasst.

    ``recht`` ist der Permission-Key, den ein Schreibwerkzeug verlangt. Er stand
    frueher in einer if-Kette in `ai_proposal_service._permission_for` — ein
    zweiter Ort, an dem ein neues Werkzeug eingetragen werden musste, und der
    Ort, an dem man es am ehesten vergisst. Ein Schreibwerkzeug ohne `recht`
    kommt gar nicht erst durch die Pruefung.

    ``recht_global`` entscheidet, **wie** geprueft wird. Die meisten Rechte
    haengen am Server; einige sind bewusst global und nicht delegierbar —
    `servers.delete` etwa, weil es destruktiv ist. Ein serverbezogenes Werkzeug
    kann also durchaus ein globales Recht verlangen: `propose_server_delete`
    braucht eine `server_id` und trotzdem die globale Loeschbefugnis.

    ``angebot`` sind die Rechte, von denen **eines** genuegt, damit das Werkzeug
    dem Modell ueberhaupt angeboten wird. Ohne Angabe gilt `recht`; steht auch
    das nicht da, wird das Werkzeug jedem angeboten.

    Der Unterschied zu `recht` ist die Richtung, nicht die Strenge: `recht`
    entscheidet, ob ein Aufruf **laeuft**, `angebot` nur, ob er im Katalog
    **steht**. Ein Lesewerkzeug prueft sein Recht im eigenen Handler und traegt
    deshalb kein `recht` — `angebot` schreibt denselben Schluessel hin, damit
    der Katalog ihn kennt, ohne dass sich an der Pruefung etwas aendert. Zwei
    Eintraege sind noetig, weil zwei Werkzeuge mit einem einzelnen Schluessel
    nicht auskommen: `read_blueprint` genuegt `servers.create` **oder**
    `blueprints.manage`, und `propose_server_lifecycle` haengt am Vorgang.

    Warum es das ueberhaupt gibt: bis hierher bekam jeder Benutzer alle 51
    Werkzeuge angeboten — auch die, die seine KI in seinem Namen gar nicht
    ausfuehren darf, weil ihm das Recht fehlt. Das Modell versuchte sie,
    prallte ab und verbrauchte dabei eine Runde. Der Katalog ging in **jeder**
    Runde ueber die Leitung und machte 94 Prozent des Prompts aus.
    """

    art: str
    gruppe: str | None = None
    immer_bestaetigen: bool = False
    recht: str | None = None
    recht_global: bool = False
    angebot: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.art not in ARTEN:
            raise ValueError(f"Unbekannte Werkzeugart: {self.art}")
        if self.recht_global and not self.recht:
            raise ValueError("recht_global ohne recht ist sinnlos")


WERKZEUGE: dict[str, Werkzeug] = {
    # ── Serverbezogen lesen ───────────────────────────────────────────
    #
    # Ohne `angebot` steht hier nur `server.view` dahinter, und das prueft
    # `_resolve_server` bei jedem Aufruf am **konkreten** Server. Es hier
    # nochmal hinzuschreiben brauchte fuenfzehn gleichlautende Zeilen und
    # sparte nur bei einem Benutzer etwas ein, der ueberhaupt keinen Server
    # sieht — und dessen Gespraech endet ohnehin an `list_my_servers`.
    # Eingetragen ist deshalb nur, was ein **eigenes** Recht verlangt.
    "read_server_status": Werkzeug("server_read"),
    "read_server_capacity": Werkzeug("server_read"),
    "read_server_logs": Werkzeug("server_read", angebot=("server.console.read",)),
    "read_config": Werkzeug("server_read", angebot=("server.files.read",)),
    "read_server_ports": Werkzeug("server_read"),
    "read_server_network": Werkzeug("server_read"),
    "check_server_reachability": Werkzeug("server_read"),
    "read_server_mods": Werkzeug("server_read", angebot=("server.mods.read",)),
    "read_mod_updates": Werkzeug("server_read", angebot=("server.mods.read",)),
    "search_workshop_mods": Werkzeug("server_read", angebot=("server.mods.read",)),
    "list_server_files": Werkzeug("server_read", angebot=("server.files.read",)),
    "search_server_files": Werkzeug("server_read", angebot=("server.files.read",)),
    "read_server_backups": Werkzeug("server_read", angebot=("server.backups.read",)),
    "read_guardian_incidents": Werkzeug("server_read"),
    "read_ai_action_history": Werkzeug("server_read"),

    # ── Global lesen ──────────────────────────────────────────────────
    "list_my_servers": Werkzeug("global_read"),
    "list_blueprints": Werkzeug("global_read", angebot=("servers.create",)),
    # Zwei Rechte, von denen eines genuegt — genau der Fall, fuer den `angebot`
    # eine Aufzaehlung ist und kein einzelner Schluessel: wer Blueprints pflegen
    # darf, muss seine eigene Vorlage ansehen koennen, auch ohne Server anlegen
    # zu duerfen (`_execute_global_read_tool`).
    "read_blueprint": Werkzeug(
        "global_read", angebot=("servers.create", "blueprints.manage")
    ),
    "read_node_capacity": Werkzeug("global_read", angebot=("servers.create",)),
    "read_node_health": Werkzeug("global_read", angebot=("nodes.read",)),
    # Zusaetzlich zum Recht entscheidet die Einrichtung: ohne hinterlegten
    # Suchschluessel steht `web_search` gar nicht erst im Katalog
    # (`ai_action_service._global_tool_definitions`).
    "web_search": Werkzeug("global_read", angebot=("ai.web_search.use",)),

    # Die Doku des Panels. Kein zusaetzliches Recht: dieselben Seiten stehen
    # jedem angemeldeten Benutzer im Panel offen — ein Gate hier waere eine
    # Schranke, die es nebenan nicht gibt.
    "search_docs": Werkzeug("global_read", gruppe="docs"),
    "read_docs": Werkzeug("global_read", gruppe="docs"),

    # Die Shop-Anbindung. Beide pruefen `panel.hoster.read` im eigenen Zweig —
    # bei `global_read` wertet die Registry `recht` nicht aus, das tut nur der
    # Vorschlagspfad.
    "read_hoster_setup": Werkzeug("global_read", angebot=("panel.hoster.read",)),
    "read_hoster_integration_guide": Werkzeug(
        "global_read", angebot=("panel.hoster.read",)
    ),

    # Die stehenden Auftraege. `list_tasks` liest nur, was diesem Benutzer
    # gehoert.
    #
    # `send_test_email` steht hier aus demselben Grund wie `remember` weiter
    # unten: das Kriterium fuer die Bestaetigungspflicht ist nicht "aendert
    # etwas", sondern "fasst einen Server an". Eine Mail an die **eigene**
    # hinterlegte Adresse tut das nicht — und einen Empfaengerparameter gibt es
    # bewusst nicht, sonst waere MSM ueber die KI ein Mailversender fuer Fremde.
    # Der Betreiber hat diesen Weg ausdruecklich ohne Bestaetigungsknopf
    # gewuenscht: wer "teste mal meine Mails" tippt, hat die Frage schon
    # beantwortet.
    "list_tasks": Werkzeug("global_read", gruppe="tasks"),
    "send_test_email": Werkzeug("global_read", gruppe="tasks"),

    # `remember` und `forget_memory` schreiben, stehen aber bei den
    # Lesewerkzeugen. Der Unterschied zwischen den Mengen ist nicht "aendert
    # etwas", sondern "fasst einen Server an und braucht deshalb eine
    # Bestaetigung". Ein gemerkter Satz im Profil des Benutzers tut das nicht.
    "remember": Werkzeug("global_read", gruppe="memory", angebot=("ai.memory.use",)),
    "search_memory": Werkzeug(
        "global_read", gruppe="memory", angebot=("ai.memory.use",)
    ),
    "forget_memory": Werkzeug(
        "global_read", gruppe="memory", angebot=("ai.memory.use",)
    ),

    # Dasselbe fuer Skills, mit einem zweiten Grund: **Prosa fuehrt nichts
    # aus.** Ein gelernter Skill kann nichts, was das Modell nicht ohnehin
    # duerfte — er aendert nur, wie es an eine Aufgabe herangeht.
    "read_skill": Werkzeug("global_read", gruppe="skill", angebot=("ai.skills.use",)),
    "learn_skill": Werkzeug("global_read", gruppe="skill", angebot=("ai.skills.use",)),
    "forget_skill": Werkzeug(
        "global_read", gruppe="skill", angebot=("ai.skills.use",)
    ),

    # ── Rueckfrage ────────────────────────────────────────────────────
    "ask_user": Werkzeug("ask"),

    # ── Schreiben: erzeugen ausschliesslich Vorschlaege ───────────────
    #
    # `propose_server_lifecycle` traegt kein `recht`: es haengt vom Vorgang ab
    # (start/stop/restart sind drei verschiedene Rechte). Die Zuordnung steht
    # als ausdrueckliche Ausnahme in `ai_proposal_service._permission_for`.
    # `angebot` zaehlt alle drei auf, weil eines genuegt: wer nur starten darf,
    # soll das Werkzeug bekommen. Welches Recht der einzelne Aufruf braucht,
    # entscheidet weiterhin `_permission_for` am `operation`-Argument.
    "propose_server_lifecycle": Werkzeug(
        "server_write",
        angebot=("server.start", "server.stop", "server.restart"),
    ),
    "propose_backup": Werkzeug("server_write", recht="server.backups.create"),
    # Einspielen ueberschreibt den aktuellen Spielstand mit einem aelteren. Was
    # seit dem Backup passiert ist, existiert danach nicht mehr — und kein
    # zweites Backup holt es zurueck, weil es nie eines davon gab. Unumkehrbar,
    # also gesperrt. Anlegen bleibt autonomiefaehig: ein zusaetzliches Backup
    # schadet nie.
    "propose_backup_restore": Werkzeug(
        "server_write", immer_bestaetigen=True, recht="server.backups.restore"
    ),
    "propose_config_update": Werkzeug("server_write", recht="server.files.write"),
    # Dieselbe Sache wie `propose_config_update`, nur nicht die ganze Datei —
    # und deshalb dasselbe Recht. Die Trennung ist keine Rechtefrage, sondern
    # eine der Reichweite: die Vollersetzung setzt voraus, dass das Modell die
    # Datei vollstaendig gesehen hat, die Teilaenderung nur die eine Stelle.
    # Ueber 24.000 Zeichen ist die erste unerreichbar; die zweite bleibt es
    # nicht. Ohne sie war jede echte Spielkonfiguration fuer die KI nur lesbar.
    #
    # Umkehrbar und damit autonomiefaehig: `write_server_text` legt vor jedem
    # Schreiben einen Versionsschnappschuss an, aus dem der Dateimanager den
    # alten Stand zurueckholt.
    "propose_config_patch": Werkzeug("server_write", recht="server.files.write"),
    "propose_mod_install": Werkzeug("server_write", recht="server.mods.write"),
    # Eine falsche Bind-IP macht den Server unerreichbar — aber nur, bis jemand
    # sie zurueckstellt, und das kann die KI selbst. Kein Datenverlust, also
    # keine Sperre. Die Freigabe des Betreibers ist hier die Entscheidung, nicht
    # die Bestaetigung jedes einzelnen Falls.
    "propose_bind_ip_update": Werkzeug(
        "server_write", recht="server.network.manage"
    ),
    # Loeschen ist nicht rueckgaengig zu machen — deshalb auch im autonomen
    # Modus bestaetigungspflichtig, ausdrueckliche Vorgabe des Betreibers.
    #
    # `servers.delete` ist global und nicht delegierbar (permission_catalog:
    # "BEWUSST global, destruktiv, nur Admin/Owner"). Das Werkzeug ist trotzdem
    # serverbezogen: es braucht eine `server_id`, und `_resolve_server` stellt
    # vorher sicher, dass der Benutzer diesen Server ueberhaupt sehen darf. Es
    # gilt also beides — sehen duerfen **und** global loeschen duerfen.
    "propose_server_delete": Werkzeug(
        "server_write",
        immer_bestaetigen=True,
        recht="servers.delete",
        recht_global=True,
    ),
    # Eine Ableitung legt eine **neue** Datei an und laesst die Vorlage, aus der
    # sie stammt, unberuehrt. Ist sie falsch, loescht man sie wieder; kein Server
    # aendert sich, solange niemand ihn umstellt. Der Betreiber hat die Sperre
    # hier ausdruecklich aufgehoben.
    "propose_blueprint_change": Werkzeug(
        "global_write",
        recht="blueprints.manage",
        recht_global=True,
    ),
    "propose_blueprint_delete": Werkzeug(
        "global_write",
        recht="blueprints.manage",
        recht_global=True,
    ),
    # Der Wechsel des Spiels bzw. Blueprints eines bestehenden Servers.
    #
    # Autonomiefaehig auf ausdrueckliche Vorgabe des Betreibers — und es passt
    # zum Kriterium: `switch_server_blueprint` legt **zwingend** ein Backup an,
    # bevor es die erste Datei anfasst, und bricht ab, wenn das Backup scheitert.
    # Der Weg zurueck ist damit Teil des Vorgangs selbst.
    #
    # `server.config.write` — dasselbe Recht wie am Panel-Knopf "Spiel /
    # Blueprint wechseln" (`routers/servers.py::switch_server_blueprint_endpoint`).
    #
    # Zwei Entwuerfe davor waren falsch: erst das globale `blueprints.manage`,
    # dann ein eigens erfundenes `server.blueprint.switch`. Beide erzeugten
    # dasselbe Problem in verschiedener Form — eine Handlung mit **zwei**
    # Rechten. Jemand haette sie ueber die KI gedurft und ueber das Panel nicht,
    # oder umgekehrt. Das Recht, nach dem der Betreiber gefragt hat, gab es
    # bereits; es fehlte nur die Verbindung dorthin.
    "propose_server_blueprint_switch": Werkzeug(
        "server_write",
        recht="server.config.write",
    ),
    "propose_server_create": Werkzeug(
        "global_write", recht="servers.create", recht_global=True
    ),

    # ── Stehende Auftraege anlegen, aendern, loeschen ─────────────────
    #
    # Kein `immer_bestaetigen`, obwohl auch geloescht wird — das Kriterium ist
    # Unumkehrbarkeit, und eine Aufgabe ist eine Zeile, die man wieder anlegen
    # kann. Nichts an ihr vernichtet Daten.
    #
    # Und ein zweiter Grund, der wichtiger ist: die Bestaetigungskarte **ist**
    # hier die Genehmigung des stehenden Auftrags. Sie im autonomen Modus zu
    # ueberspringen hiesse, dass die KI sich selbst kuenftige Laeufe einrichtet.
    # Bei einem einzelnen Werkzeugaufruf ist das eine Handlung; bei einer
    # Aufgabe ist es eine Handlung, die sich wiederholt. Deshalb steht der
    # Vorschlag immer da — was `immer_bestaetigen` zusaetzlich absichern
    # wuerde, waere der autonome Modus, und der ist genau die Freigabe, die
    # `kind='act'` ohnehin verlangt.
    #
    # `ai.tasks.manage` ist ein **neues** Recht, und das ist hier richtig statt
    # der sonst gebotenen Wiederverwendung: es gibt keinen Panelknopf fuer
    # stehende Auftraege, also entsteht keine Handlung mit zwei Rechten.
    # `ai.chat.use` waere zu weit (jeder Chatbenutzer haette es),
    # `ai.autonomous.use` zu eng (ein reiner Bericht braucht keine Autonomie).
    #
    # Anlegen und Aendern sind **ein** Werkzeug — `task_id` weglassen heisst
    # anlegen, dasselbe Muster wie bei `propose_hoster_integration`. Zwei
    # Werkzeuge haetten den Zeitplan zweimal im Katalog gehabt, und der geht in
    # jeder Runde der Werkzeugschleife mit ueber die Leitung.
    "propose_task_set": Werkzeug(
        "global_write", recht="ai.tasks.manage", recht_global=True
    ),
    "propose_task_delete": Werkzeug(
        "global_write", recht="ai.tasks.manage", recht_global=True
    ),

    # ── Heilung: Reparatur der Anlage, nicht des Spielstands ──────────
    #
    # Das eine Werkzeug fuer alles, was unterhalb der Spieldateien kaputt geht —
    # Container, Docker-Netz, Rechte am Bind-Mount, Portvergabe. Es nimmt eine
    # **Kennung aus einer geschlossenen Liste** und keinen freien Text. Das ist
    # der ganze Punkt: es gibt damit keinen Weg von einer Modellausgabe zu einer
    # Befehlszeile, zu einem Pfad oder zu einem Containernamen. Ein geglueckter
    # Jailbreak kann hoechstens die falsche der vier Reparaturen waehlen.
    #
    # Umkehrbar und deshalb autonomiefaehig: jede der vier Aktionen stellt einen
    # Sollzustand her, den MSM ohnehin kennt. Keine loescht Spieldaten — das
    # Serververzeichnis ist ein Bind-Mount und ueberlebt den Container.
    #
    # `server.config.write` ist dasselbe Recht wie am Panel-Knopf, hinter dem
    # dieselben Funktionen liegen. Ein eigenes Recht zu erfinden hiesse, eine
    # Handlung mit zwei Rechten zu haben — der Fehler, der bei
    # `propose_server_blueprint_switch` zweimal gemacht und dokumentiert wurde.
    "propose_server_repair": Werkzeug(
        "server_write", recht="server.config.write"
    ),
    # Loeschen einer **einzelnen** Datei unterhalb des Serververzeichnisses.
    #
    # Warum kein `immer_bestaetigen`, obwohl Loeschen sonst immer bestaetigt
    # wird: das Kriterium der Registry ist Unumkehrbarkeit (siehe oben), nicht
    # gefuehltes Risiko. `propose_server_delete` und `propose_backup_restore`
    # stehen dort, weil danach **kein** Backup mehr hilft — das eine loescht die
    # Backups mit, das andere ueberschreibt einen Stand, von dem es nie einen
    # gab.
    #
    # Hier ist es anders herum, und zwar aus zwei Gruenden, die beide
    # nachpruefbar sein muessen:
    #
    # 1. **Im Guardian-Lauf** laeuft das Werkzeug ueberhaupt nur, wenn ein
    #    nachweislich geglecktes Backup vorliegt, das juenger ist als der Beginn
    #    der Heilung (`ai_proposal_service._verlangt_gesichertes_backup`). Diese
    #    Pruefung laeuft zweimal — beim Anlegen und, ueber
    #    `guardian_aus_lauf`, noch einmal in `execute_proposal`. Die
    #    Wiederholung ist nicht Zierde: zwischen beiden Punkten liegt ein Commit
    #    und ein Zeitfenster ohne Obergrenze, in dem die Aufbewahrungsregel das
    #    Archiv abgeraeumt haben kann. Als die zweite Pruefung hier noch
    #    behauptet und nicht gebaut war, war genau das der Weg zu einer
    #    geloeschten Datei ohne Backup.
    # 2. **Im gewoehnlichen Chat** gibt es diese Schranke nicht — dort
    #    entscheidet der Mensch. Der Weg zurueck ist dann der
    #    Versionsschnappschuss aus `file_history_service`, und er ist eine
    #    **Vorbedingung**: `delete_server_text` wertet seinen Rueckgabewert aus
    #    und loescht nicht, wenn er ausbleibt. Deshalb weist schon der Vorschlag
    #    ab, was sich nicht sichern laesst — binaere Dateien (ein mit U+FFFD
    #    durchsetzter Schnappschuss ist ein Rueckweg, der die Datei zerstoert)
    #    und alles ueber 512 KiB. Zuvor gab `snapshot` dort stillschweigend
    #    `False` zurueck, der Wert wurde verworfen, und eine zwei Megabyte grosse
    #    Regionsdatei verschwand ohne jede Spur.
    "propose_file_delete": Werkzeug(
        "server_write", recht="server.files.write"
    ),

    # ── Shop-Anbindung einrichten ─────────────────────────────────────
    #
    # Alle drei tragen `immer_bestaetigen`. Der Grund steht schon bei
    # `GEPLANT_IMMER_BESTAETIGEN` weiter unten: eine Rechteaenderung oder eine
    # Schluesselerzeugung wirkt auf die Grenzen, innerhalb derer die KI selbst
    # arbeitet. Ein Produkt mit `role_id` bestimmt woertlich, welche Rolle
    # **jeder kuenftige Kaeufer** bekommt; eine Tarifrolle traegt das
    # KI-Kontingent; eine Integration erzeugt einen API-Key.
    #
    # Bei der Integration kommt ein **mechanischer** Grund dazu, der unabhaengig
    # von jeder Auslegung gilt: im autonomen Modus ruft `ai_stream_service`
    # `execute_autonomously` und verwirft dessen Rueckgabewert. Genau darin
    # steckt der einmalige Klartextschluessel — `create_integration` gibt ihn
    # exakt einmal aus, gespeichert wird nur der Hash. Eine autonom angelegte
    # Integration waere unbenutzbar und nur ueber eine Rotation zu retten.
    # Bestaetigungspflicht ist hier keine Vorsicht, sondern Funktionsbedingung.
    "propose_hoster_integration": Werkzeug(
        "global_write",
        immer_bestaetigen=True,
        recht="panel.hoster.write",
        recht_global=True,
    ),
    "propose_hoster_product": Werkzeug(
        "global_write",
        immer_bestaetigen=True,
        recht="panel.hoster.write",
        recht_global=True,
    ),
    # `roles.manage` steht in der Tabelle, weil die Rolle angelegt wird.
    # Das zweite noetige Recht (`panel.settings.write` fuer das KI-Kontingent)
    # prueft der Payload-Bau zusaetzlich — die Tabelle traegt genau ein `recht`,
    # und die Handlung braucht zwei.
    "propose_ai_tarif_role": Werkzeug(
        "global_write",
        immer_bestaetigen=True,
        recht="roles.manage",
        recht_global=True,
    ),
}


# Werkzeuge aus dem Zielbild, die es noch nicht gibt. Sie stehen hier, damit
# ein kuenftiges Tool sich ausdruecklich einordnen muss, statt stillschweigend
# autonomiefaehig zu sein.
#
# Die ersten beiden vernichten Daten und fallen damit unter dasselbe Kriterium
# wie Loeschen und Einspielen. Die letzten beiden aus einem anderen Grund: eine
# Rechteaenderung oder eine Schluesselrotation wirkt auf die Grenzen, innerhalb
# derer die KI selbst arbeitet. Autonom ausgefuehrt waere das eine Autonomie,
# die ihren eigenen Rahmen verschiebt — und die kann niemand mehr erteilen oder
# entziehen.
GEPLANT_IMMER_BESTAETIGEN = frozenset({
    "propose_server_wipe",
    "propose_server_reinstall",
    "propose_permission_change",
    "propose_secret_rotation",
})


def angebotsrechte(name: str) -> tuple[str, ...]:
    """Rechte, von denen **eines** genuegt, damit dieses Werkzeug angeboten wird.

    Eine leere Menge heisst "jedem anbieten" — das gilt fuer `ask_user`, die
    Doku, `list_my_servers` und `list_tasks`, die alle kein zusaetzliches Recht
    verlangen.

    Der Rueckfall auf `recht` ist Absicht: bei den Schreibwerkzeugen steht das
    verlangte Recht bereits dort, und es ein zweites Mal in `angebot`
    hinzuschreiben waere genau die doppelte Pflege, gegen die diese Tabelle
    gebaut wurde. `angebot` ist nur fuer die Faelle da, die `recht` nicht
    ausdruecken kann: Lesewerkzeuge (die ihr Recht im eigenen Handler pruefen)
    und Werkzeuge mit mehreren gleichwertigen Rechten.
    """
    spec = WERKZEUGE[name]
    if spec.angebot:
        return spec.angebot
    return (spec.recht,) if spec.recht else ()


def _mit_art(*arten: str) -> set[str]:
    return {name for name, spec in WERKZEUGE.items() if spec.art in arten}


def _mit_gruppe(gruppe: str) -> set[str]:
    return {name for name, spec in WERKZEUGE.items() if spec.gruppe == gruppe}


SERVER_READ_TOOLS = _mit_art("server_read")
GLOBAL_READ_TOOLS = _mit_art("global_read", "ask")
READ_TOOLS = SERVER_READ_TOOLS | GLOBAL_READ_TOOLS
SERVER_WRITE_TOOLS = _mit_art("server_write")
GLOBAL_WRITE_TOOLS = _mit_art("global_write")
WRITE_TOOLS = SERVER_WRITE_TOOLS | GLOBAL_WRITE_TOOLS
MEMORY_TOOLS = _mit_gruppe("memory")
SKILL_TOOLS = _mit_gruppe("skill")
DOCS_TOOLS = _mit_gruppe("docs")
ASK_TOOLS = _mit_art("ask")
ALWAYS_CONFIRM_TOOLS = (
    {name for name, spec in WERKZEUGE.items() if spec.immer_bestaetigen}
    | set(GEPLANT_IMMER_BESTAETIGEN)
)


# Was ein von Guardian ausgeloester Heilungslauf ueberhaupt aufrufen darf.
#
# Diese Menge ist **keine Ableitung** aus den Spalten oben, sondern eine
# ausdrueckliche Aufzaehlung — und das ist Absicht. Eine Ableitung waere eine
# Regel ("alles Lesende plus alles Umkehrbare"), und jedes kuenftige Werkzeug
# faende sich stillschweigend darin wieder. Hier soll es umgekehrt sein: wer ein
# Werkzeug in die unbeaufsichtigte Heilung aufnehmen will, schreibt es hin.
#
# Der Grund ist die Bedrohungslage dieses Laufs. Er beginnt nicht mit einer Bitte
# eines Menschen, sondern mit einem Ereignis auf einem Server, auf dem Fremde
# spielen. Was das Modell dort liest — Logzeilen, Dateiinhalte — kann jemand
# geschrieben haben, der genau darauf hofft. Der Prompt sagt dem Modell, dass es
# Weisungen darin nicht befolgen soll; diese Menge sorgt dafuer, dass es sie auch
# dann nicht kann, wenn es sie befolgen wollte.
#
# Deshalb fehlen hier: Gedaechtnis und Skills (das Modell soll sich aus einem
# Vorfall nichts Dauerhaftes anlernen — und auch nichts lesen, was ihm ein
# anderes Gespräch in die Hand gelegt hat; solange `read_skill` fehlt, lässt
# `ai_context_service._skill_index_block` das Skill-Verzeichnis aus dem Prompt
# eines solchen Laufs weg), die Hoster-Werkzeuge (Rechte und
# Schluessel), `propose_server_create`/`propose_server_delete` (Reichweite ueber
# den Vorfall hinaus), der Blueprint-Wechsel (leert das Verzeichnis) und
# `web_search` (der Name eines selbstgebauten Servers hat draussen nichts zu
# suchen — schon gar nicht, wenn ihn niemand gefragt hat).
GUARDIAN_HEILUNG_TOOLS = frozenset({
    # Sehen, was los ist.
    "read_server_status",
    "read_server_capacity",
    "read_server_logs",
    "read_config",
    "read_server_ports",
    "read_server_network",
    "check_server_reachability",
    "read_server_mods",
    "list_server_files",
    "search_server_files",
    "read_server_backups",
    "read_guardian_incidents",
    "read_ai_action_history",
    "read_node_health",
    "read_blueprint",
    "search_docs",
    "read_docs",
    # Handeln.
    "propose_backup",
    "propose_server_lifecycle",
    "propose_config_patch",
    "propose_config_update",
    "propose_file_delete",
    "propose_server_repair",
    "propose_bind_ip_update",
    "propose_mod_install",
})


# Werkzeuge, die in einem Heilungslauf ein nachweislich geglecktes Backup
# voraussetzen — juenger als der Vorfall, mit gesetztem `verified_at`.
#
# Enthalten ist alles, was den Zustand des Servers **veraendert**, nicht nur was
# ihn zerstoert. Auch ein Patch an der falschen Stelle macht eine Welt
# unbrauchbar, und die Vorgabe des Betreibers lautete ausdruecklich: erst
# sichern, dann anfassen.
#
# Nicht enthalten: `propose_backup` selbst (das waere ein Zirkel) und
# `propose_server_lifecycle` — ein Neustart aendert keine Datei, und ihn hinter
# ein Backup zu stellen hiesse, den haeufigsten und harmlosesten Heilungsschritt
# genau dann zu blockieren, wenn die Platte voll ist und deshalb kein Backup
# gelingt.
# Was ein faellig gewordener stehender Auftrag aufrufen darf.
#
# Wie `GUARDIAN_HEILUNG_TOOLS` eine **ausgeschriebene** Aufzaehlung und keine
# Ableitung, aus demselben Grund: ein kuenftiges Werkzeug soll sich nicht
# stillschweigend in einem Lauf wiederfinden, bei dem niemand zusieht. Wer eines
# aufnehmen will, schreibt es hin.
#
# Die Bedrohungslage ist eine **andere** als beim Guardian, und deshalb ist die
# Menge groesser. Ein Heilungslauf beginnt mit einem Ereignis auf einem Server,
# auf dem Fremde spielen; hier beginnt er mit einem Satz, den ein Mensch getippt
# und anschliessend an einer Vorschlagskarte bestaetigt hat. Was das Modell
# **waehrend** des Laufs liest, ist trotzdem dasselbe unvertrauenswuerdige
# Material — deshalb bleibt die Menge trotzdem eng.
#
# Enthalten ist alles Lesende ausser den Hoster-Werkzeugen (Rechte und
# Schluessel gehoeren nicht in einen unbeaufsichtigten Lauf) und ausser
# `send_test_email` (ein stehender Auftrag, der Testmails verschickt, ist eine
# naechtliche Mailschleife).
#
# `web_search` ist ausdruecklich dabei, anders als beim Guardian. Dort fehlt es,
# weil niemand gefragt hat; hier hat jemand gefragt — der Betreiber hat "sag mir
# taeglich, wie das Wetter wird" als Beispiel genannt. Die Schranke gegen
# Servernamen im Netz (`docs_searchable`) gilt unveraendert weiter.
#
# Nicht enthalten: Gedaechtnis und Skills. Was das Modell hier liest, kann ein
# Spieler in ein Log geschrieben haben, und aus einem Lauf ohne Zeugen soll
# nichts Dauerhaftes gelernt werden. Der Gedaechtnisblock im Kontext kommt
# ohnehin von selbst mit — es fehlt also nichts. Das Skill-Verzeichnis kommt
# umgekehrt **nicht** mit: solange `read_skill` hier fehlt, lässt
# `ai_context_service._skill_index_block` es aus dem Prompt weg, statt zum
# Lesen aufzufordern.
#
# `ask_user` fehlt, weil niemand davorsitzt. Das ist keine Sparmassnahme: eine
# unbeantwortbare Rueckfrage haette den Lauf bis zum Ablauf geparkt und die
# Aufgabe damit still ausfallen lassen.
AUFGABEN_LESEN = frozenset({
    "read_server_status",
    "read_server_capacity",
    "read_server_logs",
    "read_config",
    "read_server_ports",
    "read_server_network",
    "check_server_reachability",
    "read_server_mods",
    "read_mod_updates",
    "search_workshop_mods",
    "list_server_files",
    "search_server_files",
    "read_server_backups",
    "read_guardian_incidents",
    "read_ai_action_history",
    "list_my_servers",
    "list_blueprints",
    "read_blueprint",
    "read_node_capacity",
    "read_node_health",
    "search_docs",
    "read_docs",
    "web_search",
    # Damit ein Auftrag ueber die eigenen Auftraege berichten kann. Liest
    # ausschliesslich, was diesem Benutzer gehoert.
    "list_tasks",
})


# Was zusaetzlich erlaubt ist, wenn die Aufgabe als `kind='act'` angelegt wurde.
#
# Diese Werkzeuge laufen im faelligen Lauf **nicht** kraft dieser Menge, sondern
# weiterhin nur, soweit `autonomy_allows` es zulaesst: erteilte Freigabe, Budget
# der Stunde, Recht des Benutzers am konkreten Server. Die Menge hier ist die
# aeussere Grenze, nicht die Erlaubnis.
#
# Nicht enthalten, jeweils mit Grund:
#
# * `propose_backup_restore`, `propose_server_delete` — stehen in
#   `ALWAYS_CONFIRM_TOOLS` und wuerden ohnehin abgewiesen. Sie hier
#   aufzuzaehlen hiesse, zwei Orte zu pflegen.
# * `propose_server_create`, `propose_blueprint_change`,
#   `propose_server_blueprint_switch` — Reichweite ueber den Auftrag hinaus. Der
#   Wechsel loescht zudem das gesamte Serververzeichnis; ein nachts angestossener
#   Blueprintwechsel ist nichts, was jemand mit "mach das taeglich" gemeint hat.
# * `propose_file_delete` — im Guardian-Lauf steht davor ein nachgewiesenes
#   Backup als Schranke. Diesen Anker gibt es hier nicht: eine Aufgabe hat
#   keinen Vorfall, ab dem gerechnet wuerde. Ohne ihn bliebe als Rueckweg nur
#   der Versionsschnappschuss, und eine stehende Anweisung, die Nacht fuer Nacht
#   Dateien loescht, ist genau der Fall, fuer den jemand davorsitzen soll.
# * die Hoster- und Aufgabenwerkzeuge — Rechte, Schluessel, und ein Auftrag, der
#   Auftraege anlegt, waere ein Auftrag ohne Ende.
AUFGABEN_HANDELN = frozenset({
    "propose_server_lifecycle",
    "propose_backup",
    "propose_config_update",
    "propose_config_patch",
    "propose_mod_install",
    "propose_bind_ip_update",
    "propose_server_repair",
})


def aufgaben_tools(kind: str) -> frozenset[str]:
    """Die Werkzeugmenge eines faelligen Laufs, je nach Art der Aufgabe.

    ``report`` liest und berichtet, ``act`` darf zusaetzlich handeln. Die
    Fallunterscheidung steht hier und nicht beim Aufrufer: sonst gaebe es zwei
    Stellen, an denen jemand die Vereinigung bilden koennte, und eine davon
    vergaesse irgendwann die Bedingung.
    """
    if kind == "act":
        return AUFGABEN_LESEN | AUFGABEN_HANDELN
    return AUFGABEN_LESEN


# Was im Sprachmodus gelesen werden darf.
#
# Dritte ausgeschriebene Menge nach `GUARDIAN_HEILUNG_TOOLS` und
# `AUFGABEN_LESEN`, und aus demselben Grund keine Ableitung: ein kuenftiges
# Werkzeug soll sich nicht stillschweigend im Sprachweg wiederfinden. Wer eines
# aufnehmen will, schreibt es hin.
#
# Die Bedrohungslage ist eine **dritte**. Beim Guardian sitzt niemand davor,
# beim stehenden Auftrag hat jemand vorher zugestimmt — hier sitzt jemand davor
# und redet, aber er **sieht nichts**. Das ist der Unterschied, der diese Menge
# bestimmt: alles, was seine Antwort nur im Panel zeigen kann, ist hier nutzlos
# oder irrefuehrend.
#
# Deshalb fehlt `ask_user`: es beendet den Zug und stellt eine Karte mit
# Knoepfen hin. Im Sprachmodus fragt das Modell, indem es **fragt** — eine Karte
# waere eine Rueckfrage, die der Sprechende nicht hoert.
#
# Deshalb fehlen die Hoster-Werkzeuge: Rechte und Schluessel gehoeren nicht in
# einen Kanal, dessen Ausgabe man ueberhoert, wenn man gerade wegsieht.
#
# Deshalb fehlt `send_test_email`: nichts daran ist ein Gespraech.
#
# Das Gedaechtnis ist ausdruecklich dabei. „Merk dir, dass mein Testserver der
# zweite ist" ist genau der Satz, den man spricht und nicht tippt — und es ist
# dieselbe Unterhaltung desselben Menschen, geschuetzt durch dasselbe
# `ai.memory.use`.
#
# `learn_skill` und `forget_skill` fehlen dagegen: ein Skill ist Prosa, die
# kuenftige Laeufe anleitet, und was jemand nebenbei ins Mikrofon sagt, soll
# nicht dauerhaft die Arbeitsweise der KI aendern. `read_skill` bleibt, damit
# Gelerntes auch im Gespraech wirkt.
SPRACHE_LESEN = frozenset({
    # Was ist mit meinen Servern?
    "list_my_servers",
    "read_server_status",
    "read_server_capacity",
    "read_server_logs",
    "read_config",
    "read_server_ports",
    "read_server_network",
    "check_server_reachability",
    "read_server_mods",
    "read_mod_updates",
    "search_workshop_mods",
    "list_server_files",
    "search_server_files",
    "read_server_backups",
    "read_guardian_incidents",
    "read_ai_action_history",
    # Was ist mit der Anlage?
    "list_blueprints",
    "read_blueprint",
    "read_node_capacity",
    "read_node_health",
    # Nachschlagen.
    "search_docs",
    "read_docs",
    "web_search",
    "list_tasks",
    # Sich etwas merken und Gemerktes wiederfinden.
    "remember",
    "search_memory",
    "forget_memory",
    "read_skill",
})


# Was im Sprachmodus geaendert werden darf — nach gesprochener Bestaetigung.
#
# Die Zusammensetzung ist fast dieselbe wie `AUFGABEN_HANDELN`, und das ist kein
# Zufall: beide Male gilt dasselbe Kriterium, nur aus einem anderen Grund.
#
# Dort fehlt `propose_file_delete`, weil kein Vorfall existiert, ab dem ein
# Backup gerechnet wuerde. Hier fehlt es aus einem zweiten Grund, der schwerer
# wiegt: **eine gesprochene Zustimmung ist schwaecher als ein Klick.** Sie kann
# missverstanden werden, im Hintergrund kann jemand anders „ja" sagen, und der
# Beweis im Audit ist ein Transkript statt einer Betaetigung. Fuer alles, wovon
# es keinen Weg zurueck gibt, ist das zu wenig.
#
# Deshalb steht hier **nichts** aus `ALWAYS_CONFIRM_TOOLS`, und das wird nicht
# nur so gemeint, sondern in `ai_voice_tools.Bruecke` geprueft: Loeschen,
# Backup-Einspielen, Hoster-Schluessel und Rollenvergabe verlangen weiterhin die
# Karte. Die KI sagt dann „schau bitte kurz ins Panel" — und das ist die
# richtige Antwort, nicht eine Einschraenkung.
#
# Ebenfalls nicht dabei, jeweils aus dem Grund, der auch bei `AUFGABEN_HANDELN`
# steht: Servererstellung und Blueprintwechsel (Reichweite ueber das Gespraech
# hinaus, der Wechsel leert zudem das Serververzeichnis), die Hoster-Werkzeuge
# (Rechte und Schluessel) und die Aufgabenwerkzeuge (ein Auftrag, den man
# nebenbei diktiert, laeuft danach jede Nacht).
SPRACHE_HANDELN = frozenset({
    "propose_server_lifecycle",
    "propose_backup",
    "propose_config_update",
    "propose_config_patch",
    "propose_mod_install",
    "propose_bind_ip_update",
    "propose_server_repair",
})


def sprache_tools(*, darf_handeln: bool) -> frozenset[str]:
    """Die Werkzeugmenge einer Sprachsitzung.

    ``darf_handeln`` ist nicht die Autonomiefreigabe, sondern die Frage, ob
    dieser Benutzer ueberhaupt Schreibwerkzeuge angeboten bekommt. Wer sie nicht
    ausfuehren darf, soll sie auch nicht vorgeschlagen bekommen — ein Modell,
    das sie versucht, prallt sonst ab und hat eine Runde verbraucht. Die
    Fallunterscheidung steht hier und nicht beim Aufrufer, aus demselben Grund
    wie bei `aufgaben_tools`: sonst gaebe es zwei Stellen, an denen jemand die
    Vereinigung bildet, und eine davon vergaesse die Bedingung.
    """
    if darf_handeln:
        return SPRACHE_LESEN | SPRACHE_HANDELN
    return SPRACHE_LESEN


GUARDIAN_BACKUP_PFLICHT_TOOLS = frozenset({
    "propose_config_patch",
    "propose_config_update",
    "propose_file_delete",
    "propose_server_repair",
    "propose_mod_install",
    "propose_bind_ip_update",
})


def bekannt(name: str) -> bool:
    return name in WERKZEUGE
