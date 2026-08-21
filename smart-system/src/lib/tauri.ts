/**
 * Typisierte Brücke zu den Rust-Commands — die einzige Stelle, an der
 * `invoke` aufgerufen wird. Komponenten importieren Funktionen, keine
 * Command-Namen: ein umbenanntes Command fällt hier auf, nicht irgendwo
 * in der UI.
 */
import { invoke } from "@tauri-apps/api/core";

export type AgentStatus = "bereit" | "hoert" | "denkt" | "spricht";

/** Setzt den Tray-Status (Icon-Farbe + Tooltip). */
export async function setzeStatus(status: AgentStatus): Promise<void> {
  await invoke("setze_status", { status });
}

/** Zeigt oder versteckt das Overlay-Fenster (Sprachblase). */
export async function overlaySichtbar(sichtbar: boolean): Promise<void> {
  await invoke("overlay_sichtbar", { sichtbar });
}

/** Senkt Hintergrundton um 60 % ab (an=true) bzw. stellt ihn wieder her. */
export async function duckingSetzen(an: boolean): Promise<void> {
  await invoke("ducking", { an });
}

// ── Wake-Word ────────────────────────────────────────────────────────────

export interface WakewordStand {
  aufnahmen: number;
  trainiert: boolean;
  lauscht: boolean;
}

export async function wakewordStand(): Promise<WakewordStand> {
  return await invoke<WakewordStand>("wakeword_stand");
}

/** Nimmt Kalibrierungs-Aufnahme Nr. `nummer` auf (blockiert ~2,2 s). */
export async function wakewordAufnehmen(nummer: number): Promise<string> {
  return await invoke<string>("wakeword_aufnehmen", { nummer });
}

export async function wakewordTrainieren(wort: string): Promise<void> {
  await invoke("wakeword_trainieren", { wort });
}

export async function wakewordLauschen(an: boolean): Promise<void> {
  await invoke("wakeword_lauschen", { an });
}

export async function wakewordZuruecksetzen(): Promise<void> {
  await invoke("wakeword_zuruecksetzen");
}
