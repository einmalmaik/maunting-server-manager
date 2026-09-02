"""Ein OpenAI-kompatibler Anbieter fuer den lokalen Rauchtest — mit Drehbuch.

Kein Ersatz fuer ein echtes Modell. Der Zweck ist ein anderer: eine
**vorhersagbare** mehrschrittige Aufgabe, mit der sich die Verdrahtung des
Laufs auf einem echten uvicorn pruefen laesst — Hintergrundausfuehrung,
Wiederanhaengen, Parken an einer Bestaetigung und Fortsetzung danach.

Ein echtes Modell taugt dafuer nicht: es entscheidet jedes Mal anders, und ein
Test, der bei jedem Lauf etwas anderes prueft, prueft nichts.

Das Drehbuch entsteht aus dem, was der Anbieter in den Nachrichten **sieht**:

  1. Erste Runde              -> `list_my_servers` (Lesewerkzeug)
  2. Nach dem Leseergebnis    -> `propose_server_lifecycle` (Schreibwerkzeug)
  3. Nach "wartet auf Mensch" -> Text: "Bitte bestaetigen."
  4. Nach der Panel-Meldung   -> Text: "Erledigt."

Start:  python tools/rauchtest_anbieter.py [PORT]
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9310


def _sse(block: dict) -> bytes:
    return b"data: " + json.dumps(block).encode("ascii") + b"\n\n"


def _werkzeugaufruf(rufnummer: str, name: str, argumente: dict) -> list[bytes]:
    return [
        _sse({
            "choices": [{
                "index": 0,
                "delta": {"tool_calls": [{
                    "index": 0,
                    "id": rufnummer,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(argumente)},
                }]},
            }],
        }),
    ]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # Ruhe im Protokoll
        pass

    def do_POST(self) -> None:
        laenge = int(self.headers.get("Content-Length") or 0)
        anfrage = json.loads(self.rfile.read(laenge) or b"{}")
        nachrichten = anfrage.get("messages") or []
        werkzeuge = anfrage.get("tools")
        verlauf = json.dumps(nachrichten, ensure_ascii=False)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        teile: list[bytes] = []
        if "Meldung des Panels" in verlauf:
            # Der Mensch hat entschieden — die Aufgabe ist zu Ende zu bringen.
            teile.append(_sse({"choices": [{"index": 0, "delta": {
                "content": "Erledigt: der Server wurde gestartet."}}]}))
        elif werkzeuge is None:
            # Abschlussrunde ohne Werkzeuge: erklaeren, nicht handeln.
            teile.append(_sse({"choices": [{"index": 0, "delta": {
                "content": "Ich habe den Start vorbereitet. Bitte bestaetigen."}}]}))
        elif '"tool"' in verlauf and "propose_server_lifecycle" not in verlauf:
            # Das Leseergebnis liegt vor -> jetzt handeln.
            teile.append(_sse({"choices": [{"index": 0, "delta": {
                "content": "Ich starte den Server."}}]}))
            teile.extend(_werkzeugaufruf("ruf-2", "propose_server_lifecycle", {
                "server_id": 1,
                "operation": "start",
                "reason": "Der Benutzer hat darum gebeten.",
                "expected_effect": "Der Server laeuft danach.",
            }))
        else:
            # Erste Runde: erst nachsehen, was es gibt.
            teile.append(_sse({"choices": [{"index": 0, "delta": {
                "content": "Ich sehe kurz nach."}}]}))
            teile.extend(_werkzeugaufruf("ruf-1", "list_my_servers", {}))

        teile.append(_sse({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                           "usage": {"total_tokens": 42}}))
        teile.append(b"data: [DONE]\n\n")
        for teil in teile:
            self.wfile.write(teil)
            self.wfile.flush()


if __name__ == "__main__":
    print(f"Rauchtest-Anbieter auf http://127.0.0.1:{PORT}/v1", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
