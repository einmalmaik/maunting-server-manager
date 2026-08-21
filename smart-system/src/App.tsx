/**
 * Hauptfenster des Smart Systems.
 *
 * Phase 2 (Grundgerüst): zeigt den Produktrahmen und den Status der
 * Desktop-Integration. Der Einrichtungs-Assistent (Backend-URL, Login/2FA,
 * Personalisierung, Sandbox, Wake-Word) kommt in Phase 4 und ersetzt den
 * Platzhalter unten — der Rahmen (Tray, Hotkey, Overlay) steht bereits.
 */
import { useState } from "react";

import { duckingSetzen, overlaySichtbar, setzeStatus, type AgentStatus } from "./lib/tauri";

const STATUS_TEXTE: Record<AgentStatus, string> = {
  bereit: "Bereit",
  hoert: "Hört zu",
  denkt: "Denkt",
  spricht: "Spricht",
};

export default function App() {
  const [status, setStatus] = useState<AgentStatus>("bereit");
  const [overlayAn, setOverlayAn] = useState(false);
  const [duckt, setDuckt] = useState(false);

  async function duckingTesten() {
    // Hörprobe: Musik nebenher laufen lassen — 3 Sekunden leiser, dann zurück.
    setDuckt(true);
    try {
      await duckingSetzen(true);
      await new Promise((fertig) => setTimeout(fertig, 3000));
      await duckingSetzen(false);
    } finally {
      setDuckt(false);
    }
  }

  async function statusWechseln(neu: AgentStatus) {
    setStatus(neu);
    await setzeStatus(neu);
  }

  async function overlayUmschalten() {
    const neu = !overlayAn;
    setOverlayAn(neu);
    await overlaySichtbar(neu);
  }

  return (
    <main className="flex h-full flex-col items-center justify-center gap-8 bg-neutral-950 text-neutral-100">
      <header className="flex flex-col items-center gap-2">
        <h1 className="text-3xl font-semibold tracking-tight">Singra Smart System</h1>
        <p className="text-sm text-neutral-400">
          Dein Desktop-Companion zum MSM-Panel — Einrichtung folgt in der nächsten Ausbaustufe.
        </p>
      </header>

      <section className="flex flex-col items-center gap-4" aria-label="Desktop-Integration testen">
        <div className="flex gap-2">
          {(Object.keys(STATUS_TEXTE) as AgentStatus[]).map((s) => (
            <button
              key={s}
              onClick={() => void statusWechseln(s)}
              className={`rounded-lg px-3 py-1.5 text-sm transition ${
                status === s
                  ? "bg-blue-600 text-white"
                  : "bg-neutral-800 text-neutral-300 hover:bg-neutral-700"
              }`}
            >
              {STATUS_TEXTE[s]}
            </button>
          ))}
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => void overlayUmschalten()}
            className="rounded-lg bg-neutral-800 px-4 py-2 text-sm text-neutral-200 hover:bg-neutral-700"
          >
            {overlayAn ? "Overlay ausblenden" : "Overlay einblenden"}
          </button>
          <button
            onClick={() => void duckingTesten()}
            disabled={duckt}
            className="rounded-lg bg-neutral-800 px-4 py-2 text-sm text-neutral-200 hover:bg-neutral-700 disabled:opacity-50"
          >
            {duckt ? "Ducking läuft …" : "Ducking testen (3 s)"}
          </button>
        </div>
        <p className="text-xs text-neutral-500">
          Tray-Status und Overlay laufen über das Rust-Backend — Hotkey: Alt+Space.
        </p>
      </section>
    </main>
  );
}
