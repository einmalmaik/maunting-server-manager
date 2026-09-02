from __future__ import annotations

from models.ai_task import ARTEN as _AUFGABENARTEN
from models.ai_meldung import KANAELE as _MELDEKANAELE
from services.ai_tool_registry import (
    GLOBAL_READ_TOOLS,
    READ_TOOLS,
    WERKZEUGE,
    angebotsrechte,
)
from services.ai_tools.base import (
    _function,
    _PLAN_SCHEMA,
    _RATIONALE_SCHEMA,
    _RATIONALE_REQUIRED,
    MAX_QUESTION_OPTIONS,
    MAX_QUESTION_CHARS,
    MAX_OPTION_CHARS,
    MAX_OPTION_HINT_CHARS,
)

def _aufgaben_tool_definitions() -> list[dict]:
    """Stehende Auftraege: auflisten, anlegen, aendern, loeschen â€” und die Testmail.

    Eigene Funktion, damit der ohnehin lange Katalog nicht noch eine Handbreit
    weiter nach rechts waechst. Der Katalog geht in **jeder** Runde der
    Werkzeugschleife mit ueber die Leitung und taucht in keiner Budgetrechnung
    auf; `test_ai_tool_handler_contract` haelt ihn deshalb unter 45.000 Zeichen.
    """
    return [
        _function(
            "list_tasks",
            "Zeigt die stehenden Auftraege dieses Benutzers â€” Name, Zeitplan, "
            "Zeitzone, Zustellweg, ob aktiv, und wann sie das naechste Mal "
            "laufen. Ruf das auf, bevor du eine Aufgabe aenderst oder loeschst: "
            "die Nummern sind nicht zu erraten.",
            {},
            [],
        ),
        _function(
            "send_test_email",
            "Schickt eine Testmail an die hinterlegte Adresse **des Benutzers, "
            "der gerade fragt** â€” einen Empfaenger kannst du nicht waehlen. "
            "Dafuer, wenn er wissen will, ob sein E-Mail-Versand funktioniert. "
            "Die Antwort nennt den benutzten Weg und die maskierte Adresse.",
            {},
            [],
        ),
        # Der ganze *Anlass* steht in `ai_prompt.AUFGABEN` und geht in
        # derselben Anfrage mit: wann ein stehender Auftrag entsteht ("jeden
        # Tag um acht", "alle acht Stunden"), was in `instruction` gehÃ¶rt
        # ("dieser Text ist dein spaeterer Auftrag"), was `kind: "act"`
        # voraussetzt und dass die Zeitzone aus der Lage kommt. Das stand hier
        # ein zweites Mal und ist gestrichen.
        #
        # Was bleibt, ist die Feldkunde â€” und die trÃ¤gt hier mehr als sonst:
        # `required` nennt nur die BegrÃ¼ndung, weil dasselbe Werkzeug anlegt
        # **und** Ã¤ndert. Welche Felder beim Anlegen nÃ¶tig sind, erfÃ¤hrt das
        # Modell nirgends sonst; ein fehlendes kostet eine ganze Runde.
        _function(
            "propose_task_set",
            "Legt einen stehenden Auftrag an oder aendert einen vorhandenen.\n"
            "**Ohne `task_id` wird angelegt**; dann sind `title`, "
            "`instruction`, `kind`, `plan_kind` und `timezone` "
            "noetig. **Mit `task_id` (aus `list_tasks`) wird geaendert**, und "
            "nur genannte Felder werden angefasst. Aenderst du den Plan, gib "
            "`plan_kind` und dessen Felder zusammen an.",
            {
                "task_id": {
                    "type": "string",
                    "maxLength": 36,
                    "description": "Zum Aendern. Weglassen legt neu an.",
                },
                "title": {"type": "string", "maxLength": 120},
                "instruction": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "Was bei jeder Faelligkeit zu tun ist.",
                },
                "kind": {"type": "string", "enum": list(_AUFGABENARTEN)},
                "enabled": {
                    "type": "boolean",
                    "description": "false pausiert, true nimmt wieder auf.",
                },
                **_PLAN_SCHEMA,
                **_RATIONALE_SCHEMA,
            },
            [*_RATIONALE_REQUIRED],
        ),
        _function(
            "propose_task_delete",
            "Entfernt einen stehenden Auftrag endgueltig. Soll er nur ruhen, "
            "nimm `propose_task_set` mit `enabled: false` â€” das laesst sich "
            "zuruecknehmen. `task_id` aus `list_tasks`.",
            {
                "task_id": {"type": "string", "maxLength": 36},
                **_RATIONALE_SCHEMA,
            },
            ["task_id", *_RATIONALE_REQUIRED],
        ),
    ]

def _worker_tool_definitions() -> list[dict]:
    """Gehirn und Worker (docs/agentic-framework.md).

    Fuenf Werkzeuge, zwei Adressaten: `worker_start`/`worker_cancel`/
    `worker_antwort` gehoeren dem Gehirn, `wait_until`/`worker_frage` nur den
    Workern selbst. Welcher Lauf welche sieht, entscheidet der
    Laufart-Schnitt â€” hier stehen nur die Schemata, und die stehen wie alle
    im einen Katalog.
    """
    from services.ai_worker_service import (
        MAX_AUFTRAG_CHARS,
        MAX_TITEL_CHARS,
        WAIT_MAX_MINUTEN,
        WAIT_MIN_MINUTEN,
    )

    return [
        _function(
            "worker_start",
            "Ãœbergibt einen Auftrag an einen Worker, der ihn im Hintergrund "
            "erledigt, wÃ¤hrend du weiter im GesprÃ¤ch bleibst. Der "
            "`auftrag` ist dessen **einzige** Wissensquelle â€” schreib alles "
            "hinein, was er braucht: was zu tun ist, woran der Erfolg zu "
            "erkennen ist, und jede Angabe des Benutzers. Nach dem Start "
            "antworte sofort weiter; das Ergebnis kommt spÃ¤ter als Meldung. "
            "Versprich nichts Ã¼ber die Dauer.",
            {
                "auftrag": {
                    "type": "string",
                    "maxLength": MAX_AUFTRAG_CHARS,
                    "description": "VollstÃ¤ndiger, aus sich heraus verstÃ¤ndlicher Auftrag.",
                },
                "titel": {
                    "type": "string",
                    "maxLength": MAX_TITEL_CHARS,
                    "description": "Kurzer Name fÃ¼r die Auftragsliste des Benutzers.",
                },
                "kanal": {
                    "type": "string",
                    # Die Liste der Meldestelle, nicht die des Aufgabenmodells:
                    # dieser Wert landet in `ai_meldungen`, und was der Katalog
                    # anbietet, muss der Konsument annehmen. Beide Tupel sind
                    # heute gleich; weichen sie einmal ab, boete der Katalog
                    # sonst einen Kanal an, den `ai_worker_service` abweist.
                    "enum": list(_MELDEKANAELE),
                    "description": (
                        "Wohin das Ergebnis gemeldet wird. chat = nur im "
                        "Panel (Standard), email = zusÃ¤tzlich per Mail, "
                        "both = beides. Im Chat steht das Ergebnis immer."
                    ),
                },
            },
            ["auftrag"],
        ),
        _function(
            "worker_cancel",
            "Bricht einen laufenden Auftrag ab. `worker_id` stammt aus der "
            "Antwort von worker_start oder aus der Lage. Nutze das, wenn der "
            "Benutzer einen Auftrag stoppen will oder er sich erledigt hat.",
            {
                "worker_id": {"type": "string", "maxLength": 36},
            },
            ["worker_id"],
        ),
        _function(
            "worker_antwort",
            "Gibt die Antwort des Benutzers an einen Auftrag zurÃ¼ck, der "
            "eine Frage gestellt hat. `worker_id` steht in der Meldung mit "
            "der Frage. Schreib in `antwort`, was der Benutzer entschieden "
            "hat â€” wÃ¶rtlich genug, dass der Auftrag danach handeln kann. "
            "Nicht nutzen, wenn kein Auftrag gefragt hat.",
            {
                "worker_id": {"type": "string", "maxLength": 36},
                "antwort": {
                    "type": "string",
                    "maxLength": MAX_AUFTRAG_CHARS,
                    "description": "Die Entscheidung des Benutzers, vollstÃ¤ndig.",
                },
            },
            ["worker_id", "antwort"],
        ),
        _function(
            "wait_until",
            "Parkt **diesen** Lauf und weckt ihn nach der angegebenen Zeit "
            "wieder â€” fÃ¼r AuftrÃ¤ge, die auf etwas warten (\"in 30 Minuten "
            "nachsehen\", \"heute Nacht prÃ¼fen\"). WÃ¤hrend des Wartens "
            "kostet der Lauf nichts. Nach dem Wecken prÃ¼fst du den Stand im "
            "Verlauf, statt blind zu wiederholen. Nicht fÃ¼r Wartezeiten "
            "unter einer Minute â€” arbeite dann einfach weiter.",
            {
                "minuten": {
                    "type": "integer",
                    "minimum": WAIT_MIN_MINUTEN,
                    "maximum": WAIT_MAX_MINUTEN,
                },
                "grund": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "Worauf gewartet wird. Erscheint in der Auftragsliste.",
                },
            },
            ["minuten"],
        ),
        _function(
            "worker_frage",
            "Stellt dem Benutzer eine Frage, obwohl er dieses Fenster nie "
            "sieht: dein Lauf parkt, die Frage wird ihm im GesprÃ¤ch gestellt, "
            "und die Antwort weckt genau diesen Lauf. Nutze sie **nur**, wenn "
            "du ohne die Entscheidung nicht weiterkommst â€” Raten wÃ¤re teuer, "
            "Warten sinnlos. Rechne damit, dass die Antwort dauert.",
            {
                "question": {
                    "type": "string",
                    "maxLength": MAX_QUESTION_CHARS,
                    "description": "Die Frage, vollstÃ¤ndig und aus sich heraus verstÃ¤ndlich.",
                },
                "options": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": MAX_QUESTION_OPTIONS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "maxLength": MAX_OPTION_CHARS},
                            "hint": {
                                "type": "string",
                                "maxLength": MAX_OPTION_HINT_CHARS,
                                "description": "Was diese Wahl bedeutet. Kurz.",
                            },
                        },
                        "required": ["label"],
                        "additionalProperties": False,
                    },
                },
            },
            ["question", "options"],
        ),
    ]
