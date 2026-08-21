/**
 * Deinstallation aus der App heraus — in zwei Schritten, nicht in einem.
 *
 * Erst räumt sie die lokalen Spuren ab (Konfiguration, Stimmaufnahmen,
 * Tresor-Eintrag, Autostart) und **zeigt einzeln**, was davon geklappt hat.
 * Erst danach darf der Windows-Uninstaller starten. Ein Knopf, der beides
 * zusammen täte, ließe niemanden nachlesen, ob seine Aufnahmen wirklich weg
 * sind — und genau das ist hier die Frage, die zählt.
 *
 * Der Sandbox-Ordner bleibt. Er gehört dem Benutzer, nicht der App.
 */
import { useState } from "react";

import {
  deinstallationAufraeumen,
  deinstallationStarten,
  type Aufraeumbericht,
} from "../lib/tauri";
import { Fehlertext, Karte, Knopf } from "../ui";

type Schritt = "ruhe" | "gefragt" | "aufgeraeumt";

export default function Gefahrenzone() {
  const [schritt, setSchritt] = useState<Schritt>("ruhe");
  const [bericht, setBericht] = useState<Aufraeumbericht | null>(null);
  const [fehler, setFehler] = useState<string | null>(null);

  async function aufraeumen() {
    setFehler(null);
    try {
      setBericht(await deinstallationAufraeumen());
      setSchritt("aufgeraeumt");
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    }
  }

  async function starten() {
    setFehler(null);
    try {
      await deinstallationStarten();
    } catch (e) {
      setFehler(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <Karte className="flex flex-col gap-4 border-destructive/40">
      <div>
        <h2 className="text-sm font-medium text-destructive">Gefahrenzone</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Entfernt die App und alles, was sie auf diesem Rechner angelegt hat: Einstellungen,
          deine Stimmaufnahmen für das Wake-Word, den Eintrag im Anmeldeinformations-Manager
          und den Autostart. Dein Sandbox-Ordner bleibt.
        </p>
      </div>

      {schritt === "ruhe" && (
        <div>
          <Knopf stimme="leise" onClick={() => setSchritt("gefragt")}>
            Smart System deinstallieren
          </Knopf>
        </div>
      )}

      {schritt === "gefragt" && (
        <div className="flex flex-col gap-3">
          <p className="text-sm">
            Sicher? Die Stimmaufnahmen und die gespeicherte Anmeldung werden gelöscht und
            lassen sich nicht zurückholen.
          </p>
          <div className="flex gap-2">
            <Knopf onClick={() => void aufraeumen()}>Ja, alles entfernen</Knopf>
            <Knopf stimme="leise" onClick={() => setSchritt("ruhe")}>
              Abbrechen
            </Knopf>
          </div>
        </div>
      )}

      {schritt === "aufgeraeumt" && bericht && (
        <div className="flex flex-col gap-3">
          <ul className="text-sm">
            <Zeile ok={bericht.konfiguration_entfernt} text="Einstellungen entfernt" />
            <Zeile ok={bericht.sprachdaten_entfernt} text="Stimmaufnahmen gelöscht" />
            <Zeile ok={bericht.tresor_geleert} text="Gespeicherte Anmeldung entfernt" />
            <Zeile ok={bericht.autostart_entfernt} text="Autostart abgeschaltet" />
          </ul>
          {bericht.sandbox_bleibt && (
            <p className="text-xs text-muted-foreground">
              Unangetastet geblieben ist dein Sandbox-Ordner: {bericht.sandbox_bleibt}
            </p>
          )}
          {bericht.fehler.length > 0 && (
            <ul className="text-xs text-destructive">
              {bericht.fehler.map((text, i) => (
                <li key={i}>{text}</li>
              ))}
            </ul>
          )}
          <div>
            <Knopf onClick={() => void starten()}>Windows-Uninstaller starten</Knopf>
          </div>
        </div>
      )}

      <Fehlertext text={fehler} />
    </Karte>
  );
}

function Zeile({ ok, text }: { ok: boolean; text: string }) {
  return (
    <li className={ok ? "text-foreground" : "text-destructive"}>
      {ok ? "✓" : "✗"} {text}
    </li>
  );
}
