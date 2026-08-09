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
    bei erteilter Freigabe.

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
    """

    art: str
    gruppe: str | None = None
    immer_bestaetigen: bool = False
    recht: str | None = None
    recht_global: bool = False

    def __post_init__(self) -> None:
        if self.art not in ARTEN:
            raise ValueError(f"Unbekannte Werkzeugart: {self.art}")
        if self.recht_global and not self.recht:
            raise ValueError("recht_global ohne recht ist sinnlos")


WERKZEUGE: dict[str, Werkzeug] = {
    # ── Serverbezogen lesen ───────────────────────────────────────────
    "read_server_status": Werkzeug("server_read"),
    "read_server_capacity": Werkzeug("server_read"),
    "read_server_logs": Werkzeug("server_read"),
    "read_config": Werkzeug("server_read"),
    "read_server_ports": Werkzeug("server_read"),
    "read_server_network": Werkzeug("server_read"),
    "check_server_reachability": Werkzeug("server_read"),
    "read_server_mods": Werkzeug("server_read"),
    "read_mod_updates": Werkzeug("server_read"),
    "search_workshop_mods": Werkzeug("server_read"),
    "read_server_backups": Werkzeug("server_read"),
    "read_guardian_incidents": Werkzeug("server_read"),
    "read_ai_action_history": Werkzeug("server_read"),

    # ── Global lesen ──────────────────────────────────────────────────
    "list_my_servers": Werkzeug("global_read"),
    "list_blueprints": Werkzeug("global_read"),
    "read_node_capacity": Werkzeug("global_read"),
    "read_node_health": Werkzeug("global_read"),
    "web_search": Werkzeug("global_read"),

    # `remember` und `forget_memory` schreiben, stehen aber bei den
    # Lesewerkzeugen. Der Unterschied zwischen den Mengen ist nicht "aendert
    # etwas", sondern "fasst einen Server an und braucht deshalb eine
    # Bestaetigung". Ein gemerkter Satz im Profil des Benutzers tut das nicht.
    "remember": Werkzeug("global_read", gruppe="memory"),
    "search_memory": Werkzeug("global_read", gruppe="memory"),
    "forget_memory": Werkzeug("global_read", gruppe="memory"),

    # Dasselbe fuer Skills, mit einem zweiten Grund: **Prosa fuehrt nichts
    # aus.** Ein gelernter Skill kann nichts, was das Modell nicht ohnehin
    # duerfte — er aendert nur, wie es an eine Aufgabe herangeht.
    "read_skill": Werkzeug("global_read", gruppe="skill"),
    "learn_skill": Werkzeug("global_read", gruppe="skill"),
    "forget_skill": Werkzeug("global_read", gruppe="skill"),

    # ── Rueckfrage ────────────────────────────────────────────────────
    "ask_user": Werkzeug("ask"),

    # ── Schreiben: erzeugen ausschliesslich Vorschlaege ───────────────
    #
    # `propose_server_lifecycle` traegt kein `recht`: es haengt vom Vorgang ab
    # (start/stop/restart sind drei verschiedene Rechte). Die Zuordnung steht
    # als ausdrueckliche Ausnahme in `ai_proposal_service._permission_for`.
    "propose_server_lifecycle": Werkzeug("server_write"),
    "propose_backup": Werkzeug("server_write", recht="server.backups.create"),
    "propose_config_update": Werkzeug("server_write", recht="server.files.write"),
    "propose_mod_install": Werkzeug("server_write", recht="server.mods.write"),
    # Eine Netzwerkaenderung startet den Container neu und kann einen Server
    # unerreichbar machen, wenn die Adresse falsch ist.
    "propose_bind_ip_update": Werkzeug(
        "server_write", immer_bestaetigen=True, recht="server.network.manage"
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
    "propose_server_create": Werkzeug(
        "global_write", recht="servers.create", recht_global=True
    ),
}


# Werkzeuge aus dem Zielbild, die es noch nicht gibt. Sie stehen hier, damit
# ein kuenftiges Tool sich ausdruecklich einordnen muss, statt stillschweigend
# autonomiefaehig zu sein. Beim Bauen wandert der Name nach oben in `WERKZEUGE`
# — mit `immer_bestaetigen=True`.
GEPLANT_IMMER_BESTAETIGEN = frozenset({
    "propose_server_wipe",
    "propose_server_reinstall",
    "propose_backup_restore",
    "propose_permission_change",
    "propose_secret_rotation",
    "propose_blueprint_change",
})


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
ASK_TOOLS = _mit_art("ask")
ALWAYS_CONFIRM_TOOLS = (
    {name for name, spec in WERKZEUGE.items() if spec.immer_bestaetigen}
    | set(GEPLANT_IMMER_BESTAETIGEN)
)


def bekannt(name: str) -> bool:
    return name in WERKZEUGE
