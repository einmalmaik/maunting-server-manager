/**
 * Hauptfenster des Smart Systems.
 *
 * Ablauf beim Start: Konfiguration laden → wenn nicht eingerichtet, den
 * Assistenten zeigen → sonst still über den OS-Tresor anmelden → gelingt
 * das nicht, nur den Anmeldeschritt zeigen. Angemeldet zeigt die App den
 * Chat (derselbe Dauerchat wie im Panel) und daneben die Einstellungen
 * (Desktop-Integration, Wake-Word).
 */
import { useEffect, useState, type ReactNode } from "react";

import { abmelden, ich, type Benutzer } from "./api/auth";
import { setzeBackendUrl, stillAnmelden } from "./api/client";
import Uebernahmekarte from "./auftraege/Uebernahmekarte";
import { useAuftragsschleife } from "./auftraege/useAuftragsschleife";
import Chat from "./chat/Chat";
import Gefahrenzone from "./einstellungen/Gefahrenzone";
import Wizard from "./einrichtung/Wizard";
import {
  duckingSetzen,
  konfigLaden,
  overlaySichtbar,
  setzeStatus,
  type AgentStatus,
  type AppKonfig,
} from "./lib/tauri";
import Splash from "./Splash";
import { Karte, Knopf } from "./ui";
import WakewordEinrichtung from "./WakewordEinrichtung";

type Phase = "laedt" | "einrichtung" | "anmeldung" | "bereit";

export default function App() {
  const [phase, setPhase] = useState<Phase>("laedt");
  const [konfig, setKonfig] = useState<AppKonfig | null>(null);
  const [benutzer, setBenutzer] = useState<Benutzer | null>(null);
  // Die Boot-Sequenz läuft über allem, während darunter Konfiguration und
  // stille Anmeldung schon laden — wie bei einem Spielstart.
  const [splash, setSplash] = useState(true);
  // Die Aufträge des Panels holt der Rechner selbst ab — aber erst, wenn
  // jemand angemeldet ist: vorher gibt es kein Token und jede Frage wäre ein
  // 401. Läuft weiter, auch wenn das Fenster im Tray liegt.
  const offeneUebernahme = useAuftragsschleife(phase === "bereit");

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

  let inhalt: ReactNode;
  if (phase === "laedt" || konfig === null) {
    inhalt = (
      <main className="flex h-full items-center justify-center bg-background text-muted-foreground">
        <p className="text-sm">Startet …</p>
      </main>
    );
  } else if (phase === "einrichtung" || phase === "anmeldung") {
    inhalt = (
      <Wizard
        konfig={konfig}
        startSchritt={phase === "anmeldung" ? "anmeldung" : "backend"}
        onFertig={fertig}
      />
    );
  } else {
    inhalt = <Hauptansicht benutzer={benutzer} onAbmelden={() => void abmeldenUndZurueck()} />;
  }

  return (
    <>
      {inhalt}
      {/* Über allem außer der Boot-Sequenz: eine Bitte um die Übernahme von
          Maus und Tastatur darf nicht hinter einem Reiter verschwinden. */}
      <Uebernahmekarte offenerAuftragId={offeneUebernahme} />
      {splash && <Splash onFertig={() => setSplash(false)} />}
    </>
  );
}

const STATUS_TEXTE: Record<AgentStatus, string> = {
  bereit: "Bereit",
  hoert: "Hört zu",
  denkt: "Denkt",
  spricht: "Spricht",
};

type Ansicht = "chat" | "einstellungen";

function Hauptansicht({
  benutzer,
  onAbmelden,
}: {
  benutzer: Benutzer | null;
  onAbmelden: () => void;
}) {
  const [ansicht, setAnsicht] = useState<Ansicht>("chat");
  const agentName = benutzer?.agent_name ?? "Singra";

  return (
    <main className="flex h-full flex-col gap-4 bg-background p-6 text-foreground">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{agentName}</h1>
          <p className="text-xs text-muted-foreground">
            {benutzer ? `Angemeldet als ${benutzer.username}` : "Nicht angemeldet"}
          </p>
        </div>
        <nav className="flex items-center gap-2" aria-label="Ansicht">
          {(
            [
              ["chat", "Chat"],
              ["einstellungen", "Einstellungen"],
            ] as const
          ).map(([wert, text]) => (
            <button
              key={wert}
              onClick={() => setAnsicht(wert)}
              className={`rounded-[var(--radius-control)] px-3 py-1.5 text-sm transition ${
                ansicht === wert
                  ? "bg-primary text-primary-foreground"
                  : "bg-secondary text-muted-foreground hover:bg-muted"
              }`}
            >
              {text}
            </button>
          ))}
          <Knopf stimme="leise" onClick={onAbmelden}>
            Abmelden
          </Knopf>
        </nav>
      </header>

      {ansicht === "chat" ? (
        <div className="min-h-0 flex-1">
          <Chat agentName={agentName} />
        </div>
      ) : (
        <Einstellungen agentName={agentName} />
      )}
    </main>
  );
}

function Einstellungen({ agentName }: { agentName: string }) {
  const [status, setStatus] = useState<AgentStatus>("bereit");
  const [overlayAn, setOverlayAn] = useState(false);
  const [duckt, setDuckt] = useState(false);

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
    <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto">
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
      <Gefahrenzone />
    </div>
  );
}
