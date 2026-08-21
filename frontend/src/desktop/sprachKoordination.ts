/**
 * Koordination der zwei Sprachorte — Hauptfenster und Overlay.
 *
 * Es darf nie zwei Sitzungen gleichzeitig geben: zwei offene Mikrofone,
 * zwei Stimmen, ein Rechner. Die Regel ist klein: wer startet, sagt es allen
 * (`SPRACHE_STARTET`), und der jeweils andere Ort beendet sich. Tauri-Events
 * erreichen beide Fenster; DOM-Events täten das nicht.
 *
 * Dazu die Verdrahtung nach draussen: `useSprachsitzung` meldet jeden
 * Zustandswechsel als DOM-Ereignis (`msm:sprachzustand`), und
 * `sprachzustandVerdrahten` übersetzt ihn in Tray-Farbe und Audio-Ducking.
 * Ducking nur beim Sprechen: gesenkt wird fremder Ton, damit man die Antwort
 * versteht — nicht schon beim Zuhören, sonst verstummt die Musik bei jedem
 * versehentlichen Wake-Word.
 */
import { emit, listen } from '@tauri-apps/api/event'

import type { Sprachzustand } from '@/components/ai/voice/useSprachsitzung'
import { duckingSetzen, setzeStatus, type AgentStatus } from './tauri'

/** Global an beide Fenster: eine Sprachsitzung beginnt an `quelle`. */
export const SPRACHE_STARTET = 'mss:sprache-startet'
/** Bitte an das Overlay: zeig dich und beginne eine Sitzung. */
export const OVERLAY_SPRACHE_START = 'mss:overlay-sprache-start'
/**
 * Bitte an das Overlay: beende die Sitzung und versteck dich. Kommt aus Rust
 * (Sprach-Hotkey, zweiter Druck) — Rust sieht nur die Fenstersichtbarkeit,
 * die Sitzung selbst gehört dem Frontend.
 */
export const OVERLAY_SPRACHE_ENDE = 'mss:overlay-sprache-ende'

export type Sprachort = 'haupt' | 'overlay'

export async function sprachstartMelden(quelle: Sprachort): Promise<void> {
  await emit(SPRACHE_STARTET, { quelle })
}

/** Ruft `beenden`, sobald der **andere** Ort eine Sitzung beginnt. */
export function beiFremdemSprachstart(
  eigenerOrt: Sprachort,
  beenden: () => void,
): () => void {
  const abo = listen<{ quelle: Sprachort }>(SPRACHE_STARTET, (ereignis) => {
    if (ereignis.payload.quelle !== eigenerOrt) {
      beenden()
    }
  })
  return () => {
    void abo.then((weg) => weg())
  }
}

const TRAY_JE_ZUSTAND: Record<Sprachzustand, AgentStatus> = {
  aus: 'bereit',
  verbindet: 'denkt',
  bereit: 'bereit',
  hoert: 'hoert',
  denkt: 'denkt',
  spricht: 'spricht',
}

/**
 * Hängt Tray und Ducking an die Zustandsmeldungen dieses Fensters.
 * Gibt die Abmeldefunktion zurück; sie stellt beides zurück auf Ruhe.
 */
export function sprachzustandVerdrahten(): () => void {
  const horcher = (ereignis: Event) => {
    const zustand = (ereignis as CustomEvent<{ zustand: Sprachzustand }>).detail
      ?.zustand
    if (!zustand) return
    void setzeStatus(TRAY_JE_ZUSTAND[zustand]).catch(() => {})
    void duckingSetzen(zustand === 'spricht').catch(() => {})
  }
  window.addEventListener('msm:sprachzustand', horcher)
  return () => {
    window.removeEventListener('msm:sprachzustand', horcher)
    void setzeStatus('bereit').catch(() => {})
    void duckingSetzen(false).catch(() => {})
  }
}
