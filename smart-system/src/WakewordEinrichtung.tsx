/**
 * Wake-Word-Kalibrierung: 10x einsprechen, trainieren, lauschen.
 *
 * Phase 3 (Technik-Durchstich): der Ablauf ist der des späteren
 * Onboarding-Assistenten (Phase 4), nur roh dargestellt. Alles läuft lokal —
 * die Aufnahmen bleiben im App-Datenverzeichnis, das Event `wakeword-erkannt`
 * trägt nur Name und Score, nie Audio.
 */
import { useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";

import {
  wakewordAufnehmen,
  wakewordLauschen,
  wakewordStand,
  wakewordTrainieren,
  wakewordZuruecksetzen,
  type WakewordStand,
} from "./lib/tauri";

const AUFNAHMEN_SOLL = 10;

export default function WakewordEinrichtung() {
  const [stand, setStand] = useState<WakewordStand>({ aufnahmen: 0, trainiert: false, lauscht: false });
  const [wort, setWort] = useState("");
  const [beschaeftigt, setBeschaeftigt] = useState<string | null>(null);
  const [meldung, setMeldung] = useState<string | null>(null);

  async function standLaden() {
    try {
      setStand(await wakewordStand());
    } catch (fehler) {
      setMeldung(String(fehler));
    }
  }

  useEffect(() => {
    void standLaden();
    const abmelden = listen<{ name: string; score: number }>("wakeword-erkannt", (ereignis) => {
      setMeldung(`Erkannt: „${ereignis.payload.name}“ (Score ${ereignis.payload.score.toFixed(2)})`);
    });
    return () => {
      void abmelden.then((f) => f());
    };
  }, []);

  async function aktion(name: string, tun: () => Promise<unknown>) {
    setBeschaeftigt(name);
    setMeldung(null);
    try {
      await tun();
      await standLaden();
    } catch (fehler) {
      setMeldung(String(fehler));
    } finally {
      setBeschaeftigt(null);
    }
  }

  const naechste = Math.min(stand.aufnahmen + 1, AUFNAHMEN_SOLL);

  return (
    <section className="flex w-full max-w-md flex-col gap-3 rounded-xl border border-neutral-800 p-4" aria-label="Wake-Word einrichten">
      <h2 className="text-sm font-medium text-neutral-200">Wake-Word (lokal)</h2>

      <div className="flex items-center gap-2">
        <input
          value={wort}
          onChange={(e) => setWort(e.target.value)}
          placeholder="Agenten-Name, z. B. Singra"
          className="flex-1 rounded-lg border border-neutral-700 bg-neutral-900 px-3 py-1.5 text-sm text-neutral-100 placeholder:text-neutral-500"
        />
        <span className="text-xs text-neutral-500">
          {stand.aufnahmen}/{AUFNAHMEN_SOLL} Aufnahmen
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => void aktion("aufnahme", () => wakewordAufnehmen(naechste))}
          disabled={beschaeftigt !== null || stand.lauscht || stand.aufnahmen >= AUFNAHMEN_SOLL}
          className="rounded-lg bg-neutral-800 px-3 py-1.5 text-sm text-neutral-200 hover:bg-neutral-700 disabled:opacity-50"
        >
          {beschaeftigt === "aufnahme" ? "Sprich jetzt …" : `Aufnahme ${naechste} starten`}
        </button>
        <button
          onClick={() => void aktion("training", () => wakewordTrainieren(wort))}
          disabled={beschaeftigt !== null || stand.aufnahmen < 3 || wort.trim() === ""}
          className="rounded-lg bg-neutral-800 px-3 py-1.5 text-sm text-neutral-200 hover:bg-neutral-700 disabled:opacity-50"
        >
          {beschaeftigt === "training" ? "Trainiert …" : "Trainieren"}
        </button>
        <button
          onClick={() => void aktion("lauschen", () => wakewordLauschen(!stand.lauscht))}
          disabled={beschaeftigt !== null || !stand.trainiert}
          className="rounded-lg bg-neutral-800 px-3 py-1.5 text-sm text-neutral-200 hover:bg-neutral-700 disabled:opacity-50"
        >
          {stand.lauscht ? "Lauschen stoppen" : "Lauschen starten"}
        </button>
        <button
          onClick={() => void aktion("reset", () => wakewordZuruecksetzen())}
          disabled={beschaeftigt !== null || (stand.aufnahmen === 0 && !stand.trainiert)}
          className="rounded-lg bg-neutral-900 px-3 py-1.5 text-sm text-neutral-400 hover:bg-neutral-800 disabled:opacity-50"
        >
          Zurücksetzen
        </button>
      </div>

      {meldung && <p className="text-xs text-neutral-400">{meldung}</p>}
      <p className="text-xs text-neutral-600">
        Alles bleibt auf diesem Rechner: Aufnahmen und Modell liegen im App-Datenverzeichnis,
        kein Audio verlässt den Prozess.
      </p>
    </section>
  );
}
