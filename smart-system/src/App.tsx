/**
 * Hauptfenster des Smart Systems.
 *
 * Ablauf beim Start: Konfiguration laden → wenn nicht eingerichtet, den
 * Assistenten zeigen → sonst still über den OS-Tresor anmelden → gelingt
 * das nicht, nur den Anmeldeschritt zeigen. Die Chat-Ansicht (1:1 wie im
 * Panel) folgt als nächste Ausbaustufe; bis dahin zeigt die Hauptansicht
 * die Desktop-Integration (Tray-Status, Overlay, Ducking, Wake-Word).
 */
import { useEffect, useState } from "react";

import { abmelden, ich, type Benutzer } from "./api/auth";
import { setzeBackendUrl, stillAnmelden } from "./api/client";
import Wizard from "./einrichtung/Wizard";
import {
  duckingSetzen,
  konfigLaden,
  overlaySichtbar,
  setzeStatus,
  type AgentStatus,
  type AppKonfig,
} from "./lib/tauri";
import { Karte, Knopf } from "./ui";
import WakewordEinrichtung from "./WakewordEinrichtung";

type Phase = "laedt" | "einrichtung" | "anmeldung" | "bereit";

export default function App() {
  const [phase, setPhase] = useState<Phase>("laedt");
  const [konfig, setKonfig] = useState<AppKonfig | null>(null);
  const [benutzer, setBenutzer] = useState<Benutzer | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const geladen = await konfigLaden();
        setKonfig(geladen);
        if (!geladen.eingerichtet || !geladen.backend_url) {
          setPhase("einrichtung");
          return;
        }
        setzeBackendUrl(geladen.backend_url);
        if (await stillAnmelden()) {
          setBenutzer(await ich());
          setPhase("bereit");
        } else {
          setPhase("anmeldung");
        }
      } catch {
        setPhase("einrichtung");
      }
    })();
  }, []);

  function fertig(neueKonfig: AppKonfig, neuerBenutzer: Benutzer) {
    setKonfig(neueKonfig);
    setBenutzer(neuerBenutzer);
    setPhase("bereit");
  }

  async function abmeldenUndZurueck() {
    try {
      await abmelden();
    } finally {
      setBenutzer(null);
      setPhase("anmeldung");
    }
  }

  if (phase === "laedt" || konfig === null) {
    return (
      <main className="flex h-full items-center justify-center bg-background text-muted-foreground">
        <p className="text-sm">Startet …</p>
      </main>
    );
  }

  if (phase === "einrichtung" || phase === "anmeldung") {
    return (
      <Wizard
        konfig={konfig}
        startSchritt={phase === "anmeldung" ? "anmeldung" : "backend"}
        onFertig={fertig}
      />
    );
  }

  return <Hauptansicht benutzer={benutzer} onAbmelden={() => void abmeldenUndZurueck()} />;
}

const STATUS_TEXTE: Record<AgentStatus, string> = {
  bereit: "Bereit",
  hoert: "Hört zu",
  denkt: "Denkt",
  spricht: "Spricht",
};

function Hauptansicht({
  benutzer,
  onAbmelden,
}: {
  benutzer: Benutzer | null;
  onAbmelden: () => void;
}) {
  const [status, setStatus] = useState<AgentStatus>("bereit");
  const [overlayAn, setOverlayAn] = useState(false);
  const [duckt, setDuckt] = useState(false);
  const agentName = benutzer?.agent_name ?? "Singra";

  async function statusWechseln(neu: AgentStatus) {
    setStatus(neu);
    await setzeStatus(neu);
  }

  async function overlayUmschalten() {
    const neu = !overlayAn;
    setOverlayAn(neu);
    await overlaySichtbar(neu);
  }

  async function duckingTesten() {
    setDuckt(true);
    try {
      await duckingSetzen(true);
      await new Promise((fertig) => setTimeout(fertig, 3000));
      await duckingSetzen(false);
    } finally {
      setDuckt(false);
    }
  }

  return (
    <main className="flex h-full flex-col gap-6 overflow-y-auto bg-background p-8 text-foreground">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{agentName}</h1>
          <p className="text-xs text-muted-foreground">
            {benutzer ? `Angemeldet als ${benutzer.username}` : "Nicht angemeldet"} — die
            Chat-Ansicht folgt in der nächsten Ausbaustufe.
          </p>
        </div>
        <Knopf stimme="leise" onClick={onAbmelden}>
          Abmelden
        </Knopf>
      </header>

      <Karte className="flex flex-col gap-4">
        <h2 className="text-sm font-medium">Desktop-Integration</h2>
        <div className="flex flex-wrap gap-2">
          {(Object.keys(STATUS_TEXTE) as AgentStatus[]).map((s) => (
            <button
              key={s}
              onClick={() => void statusWechseln(s)}
              className={`rounded-[var(--radius-control)] px-3 py-1.5 text-sm transition ${
                status === s
                  ? "bg-primary text-primary-foreground"
                  : "bg-secondary text-muted-foreground hover:bg-muted"
              }`}
            >
              {STATUS_TEXTE[s]}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-2">
          <Knopf stimme="leise" onClick={() => void overlayUmschalten()}>
            {overlayAn ? "Overlay ausblenden" : "Overlay einblenden"}
          </Knopf>
          <Knopf stimme="leise" onClick={() => void duckingTesten()} disabled={duckt}>
            {duckt ? "Ducking läuft …" : "Ducking testen (3 s)"}
          </Knopf>
        </div>
        <p className="text-xs text-muted-foreground">
          Tray-Status und Overlay laufen über das Rust-Backend — Hotkey: Alt+Space.
        </p>
      </Karte>

      <WakewordEinrichtung wortVorschlag={agentName} />
    </main>
  );
}
