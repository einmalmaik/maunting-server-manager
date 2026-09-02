"""Faehrt den Lauf gegen ein echtes uvicorn — die Probe, die kein Unittest kann.

Geprueft wird genau das, was der Betreiber gemeldet hat:

  1. Die Verbindung wird **mitten im Lauf gekappt**. Frueher starb der Zug damit.
  2. Der Lauf parkt an der Bestaetigung, statt zu enden.
  3. Nach dem Bestaetigen arbeitet er weiter, ohne dass jemand etwas schreibt.
  4. Wer sich spaeter wieder anhaengt, sieht den vollstaendigen Stand.

Die Testsuite kann (1) und (3) nur nachstellen; hier laufen sie auf demselben
ASGI-Server wie im Betrieb, mit echter Hintergrundaufgabe auf der
Ereignisschleife der Anwendung.
"""

from __future__ import annotations

import json
import sys
import time
import uuid

import httpx


BASIS = "http://127.0.0.1:8000/api"
ok = True


def melde(schritt: str, geglueckt: bool, zusatz: str = "") -> None:
    global ok
    ok = ok and geglueckt
    print(f"  [{'OK ' if geglueckt else 'FEHL'}] {schritt}{(' — ' + zusatz) if zusatz else ''}",
          flush=True)


def main() -> int:
    with httpx.Client(base_url=BASIS, timeout=30.0, follow_redirects=False) as c:
        anmeldung = c.post("/auth/login",
                           json={"username": "rauch", "password": "Rauchtest!2026"})
        if anmeldung.status_code != 200:
            print("Anmeldung fehlgeschlagen:", anmeldung.status_code, anmeldung.text[:300])
            return 1
        # Die Sitzungscookies tragen `Secure`; ueber http schickt httpx sie nicht
        # mit. Fuer den Rauchtest werden sie deshalb von Hand gesetzt — im
        # Betrieb laeuft das Panel ohnehin nur ueber https.
        csrf = anmeldung.headers.get("X-CSRF-Token") or c.cookies.get("__Secure-csrf_token") or ""
        kekse = "; ".join(f"{name}={wert}" for name, wert in c.cookies.items())
        kopf = {"X-CSRF-Token": csrf, "Cookie": kekse}
        c.headers.update({"Cookie": kekse})
        melde("Anmeldung", True)

        c.get("/ai/conversation")
        anbieter = c.get("/ai/providers").json()
        anbieter_id = anbieter[0]["id"]

        # ── 1. Senden und die Verbindung nach dem ersten Stueck kappen ──────
        print("\n1) Verbindung mitten im Lauf kappen")
        gelesen = 0
        with c.stream("POST", "/ai/conversation/messages/stream",
                      json={"content": "Starte bitte den RauchServer und sag Bescheid.",
                            "provider_id": anbieter_id,
                            "request_id": str(uuid.uuid4()),
                            "reasoning": False},
                      headers=kopf) as strom:
            melde("Strom geoeffnet", strom.status_code == 200, f"HTTP {strom.status_code}")
            for stueck in strom.iter_bytes():
                gelesen += len(stueck)
                if gelesen > 0:
                    break  # ab hier tut der Browser so, als waere er zu
        melde("Verbindung abgebrochen", True, f"{gelesen} Bytes gelesen")

        # ── 2. Der Lauf muss trotzdem weiterarbeiten und parken ────────────
        print("\n2) Laeuft er ohne Zuschauer weiter?")
        zustand = None
        for _ in range(40):
            time.sleep(0.5)
            antwort = c.get("/ai/conversation/run")
            zustand = antwort.json() if antwort.status_code == 200 else None
            if zustand and zustand.get("status") == "waiting_confirmation":
                break
        melde("Lauf hat ohne Zuschauer geparkt",
              bool(zustand) and zustand.get("status") == "waiting_confirmation",
              json.dumps(zustand) if zustand else "kein Lauf")
        if not zustand:
            return 1

        vorschlaege = c.get("/ai/conversation/actions").json()
        offen = [v for v in vorschlaege if v["status"] == "proposed"]
        melde("Ein Vorschlag wartet", len(offen) == 1,
              ", ".join(f"{v['tool_name']}={v['status']}" for v in vorschlaege))
        melde("Der Vorschlag kennt seinen Lauf",
              bool(offen) and offen[0].get("run_id") == zustand["id"],
              str(offen[0].get("run_id")) if offen else "-")

        nachrichten = c.get("/ai/conversation").json()["messages"]
        letzte = [m for m in nachrichten if m["role"] == "assistant"][-1]
        melde("Die KI hat den Vorgang vorher erklaert", bool(letzte["content"].strip()),
              repr(letzte["content"][:60]))

        # ── 3. Bestaetigen — und es muss von selbst weitergehen ────────────
        print("\n3) Nach dem Bestaetigen weiterarbeiten")
        vorschlag = offen[0]
        token = c.post(f"/ai/actions/{vorschlag['id']}/confirm", headers=kopf).json()
        ausgefuehrt = c.post(f"/ai/actions/{vorschlag['id']}/execute", headers=kopf,
                             json={"confirmation_token": token["confirmation_token"]})
        # 200 heisst gelaufen, 409 heisst gescheitert — auf dieser Maschine
        # laeuft kein Docker, ein echter Serverstart kann also gar nicht
        # gelingen. Beides ist ein **Ergebnis**, und genau darauf kommt es hier
        # an: der Lauf muss danach weiterarbeiten und es dem Benutzer sagen.
        # Ein Fehlschlag ist sogar der wichtigere Fall — bliebe der Lauf dann
        # geparkt, waere die Karte rot und der Chat stumm.
        melde("Aktion entschieden (gelaufen oder ehrlich gescheitert)",
              ausgefuehrt.status_code in (200, 409),
              f"HTTP {ausgefuehrt.status_code} " + ausgefuehrt.text[:100])

        vorher = len([m for m in nachrichten if m["role"] == "assistant"])
        fertig = None
        for _ in range(60):
            time.sleep(0.5)
            lauf = c.get("/ai/conversation/run")
            aktuell = lauf.json() if lauf.status_code == 200 else None
            if aktuell is None:
                fertig = "beendet"
                break
        neue = c.get("/ai/conversation").json()["messages"]
        antworten = [m for m in neue if m["role"] == "assistant"]
        melde("Der Lauf ist von selbst zu Ende gekommen", fertig == "beendet", str(fertig))
        melde("Es gibt eine zweite Antwort — ohne dass jemand geschrieben hat",
              len(antworten) > vorher,
              f"{vorher} -> {len(antworten)}")
        if antworten:
            melde("Die Fortsetzung meldet den Abschluss",
                  "Erledigt" in antworten[-1]["content"],
                  repr(antworten[-1]["content"][:80]))
        # Und niemand hat zwischendurch etwas geschrieben.
        eigene = [m for m in neue if m["role"] == "user"]
        melde("Genau eine Benutzernachricht im ganzen Vorgang", len(eigene) == 1,
              f"{len(eigene)} Nachricht(en)")

        # ── 4. Wer sich spaeter anhaengt, sieht den vollstaendigen Stand ────
        print("\n4) Nachtraeglich anhaengen")
        antwort = c.get(f"/ai/conversation/run/{zustand['id']}/stream", timeout=10.0)
        melde("Anhaengen beantwortet", antwort.status_code == 200,
              f"HTTP {antwort.status_code}")
        melde("Der Abzug traegt den Schlussstand",
              "event: snapshot" in antwort.text and "Erledigt" in antwort.text,
              antwort.text[:90].replace("\n", " "))

    print("\n" + ("ALLES GRUEN" if ok else "ES GIBT FEHLSCHLAEGE"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
