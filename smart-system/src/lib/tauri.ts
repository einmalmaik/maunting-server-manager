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
