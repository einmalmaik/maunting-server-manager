/**
 * Eine Webview liegt immer über der Oberfläche: das Betriebssystem zeichnet
 * sie als eigenes Fenster. Ein Dialog, ein Menü oder eine Vorschlagsliste,
 * die in den Seitenbereich ragt, wäre darunter unsichtbar.
 *
 * Deshalb verschwindet der vordere Tab, solange so etwas offen ist. Erkannt
 * wird es am DOM, nicht an jeder einzelnen Komponente: alles mit
 * `aria-modal="true"` (Dialoge, Blätter, Lichtbox) oder `role="menu"`/
 * `role="listbox"` (Menüs, Auswahllisten, Vorschläge) und jedes
 * `Ankerfenster`. Kurzinfos zählen nicht, sonst verschwände die Seite beim
 * Überfahren jedes Knopfs.
 *
 * Was darüber hinaus verdecken muss, trägt `data-msb-verdeckt`.
 *
 * Damit die Seite dabei nicht verschwindet, nimmt Rust vorher ein Standbild
 * auf, und die Seitenfläche zeigt es an ihrer Stelle, bis der Tab wieder
 * vorne liegt.
 */
import { useEffect } from 'react'
import { create } from 'zustand'

import { nativ } from './nativ'
import { useTabsStore } from './tabsStore'

const UEBERLAGERUNG = '[aria-modal="true"], [role="menu"], [role="listbox"], [data-ankerfenster], [data-msb-verdeckt]'

export interface Standbild {
  tab: string
  /** Blob-Adresse des JPEG, nur im Speicher. */
  url: string
}

export const useStandbild = create<{ bild: Standbild | null }>(() => ({ bild: null }))

function bildSetzen(neu: Standbild | null): void {
  const alt = useStandbild.getState().bild
  if (alt) URL.revokeObjectURL(alt.url)
  useStandbild.setState({ bild: neu })
}

const naechstesBild = () => new Promise<void>((fertig) => requestAnimationFrame(() => requestAnimationFrame(() => fertig())))

async function standbildAufnehmen(): Promise<void> {
  const tab = useTabsStore.getState().aktivId
  if (!tab) return
  try {
    const daten = await nativ.tabStandbild()
    if (!daten) return
    bildSetzen({ tab, url: URL.createObjectURL(new Blob([daten], { type: 'image/jpeg' })) })
    // Erst wenn das Bild gezeichnet ist, darf der Tab weg.
    await naechstesBild()
  } catch {
    // Startseite, abgestürzter Tab oder zu langsam: der Tab geht ohne Bild weg.
  }
}

/** Was das DOM verlangt und was Rust zuletzt bestätigt hat. */
let gewuenscht = false
let angewandt = false
let laeuft = false

async function nachziehen(): Promise<void> {
  if (laeuft) return
  laeuft = true
  try {
    while (angewandt !== gewuenscht) {
      const ziel = gewuenscht
      if (ziel) await standbildAufnehmen()
      await nativ.tabsVerdecken(ziel)
      angewandt = ziel
      if (!ziel) bildSetzen(null)
    }
  } catch {
    // Der nächste Wechsel im DOM versucht es erneut.
  } finally {
    laeuft = false
  }
}

function abgleichen(): void {
  gewuenscht = document.querySelector(UEBERLAGERUNG) !== null
  void nachziehen()
}

/** Einmal in der Wurzel. */
export function useUeberdeckungBeobachten(): void {
  useEffect(() => {
    let geplant = 0
    const beobachter = new MutationObserver(() => {
      if (geplant) return
      geplant = requestAnimationFrame(() => {
        geplant = 0
        abgleichen()
      })
    })
    beobachter.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['aria-modal', 'role', 'data-ankerfenster', 'data-msb-verdeckt'],
    })
    abgleichen()
    return () => {
      beobachter.disconnect()
      cancelAnimationFrame(geplant)
    }
  }, [])
}
