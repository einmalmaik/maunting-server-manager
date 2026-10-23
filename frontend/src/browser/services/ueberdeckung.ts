/**
 * Eine Webview liegt immer über der Oberfläche: das Betriebssystem zeichnet
 * sie als eigenes Fenster. Ein Dialog, ein Menü oder eine Vorschlagsliste,
 * die in den Seitenbereich ragt, wäre darunter unsichtbar.
 *
 * Deshalb verschwinden die Tabs, solange so etwas offen ist. Erkannt wird es
 * am DOM, nicht an jeder einzelnen Komponente: alles mit `aria-modal="true"`
 * (Dialoge, Blätter, Lichtbox) oder `role="menu"`/`role="listbox"` (Menüs,
 * Auswahllisten, Vorschläge) und jedes `Ankerfenster`. Kurzinfos zählen
 * nicht, sonst verschwände die Seite beim Überfahren jedes Knopfs.
 *
 * Was darüber hinaus verdecken muss, trägt `data-msb-verdeckt`.
 *
 * Damit die Seite dabei nicht verschwindet, nimmt Rust vorher Standbilder auf,
 * für geteilte Ansicht von beiden Seiten, und die Seitenfläche zeigt sie an
 * ihrer Stelle, bis die Tabs wieder vorne liegen.
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

/** Ein Standbild je sichtbarem Tab (in der geteilten Ansicht sind es zwei). */
export const useStandbild = create<{ bilder: Record<string, string> }>(() => ({ bilder: {} }))

export function bytesZuBlob(daten: unknown): Blob | null {
  if (!daten) return null
  let bytes: Uint8Array | null = null
  if (daten instanceof Uint8Array) {
    bytes = daten
  } else if (daten instanceof ArrayBuffer) {
    bytes = new Uint8Array(daten)
  } else if (ArrayBuffer.isView(daten)) {
    bytes = new Uint8Array(daten.buffer, daten.byteOffset, daten.byteLength)
  } else if (Array.isArray(daten)) {
    bytes = new Uint8Array(daten)
  } else if (typeof daten === 'string') {
    try {
      const roh = atob(daten)
      bytes = new Uint8Array(roh.length)
      for (let i = 0; i < roh.length; i++) bytes[i] = roh.charCodeAt(i)
    } catch {
      return null
    }
  } else if (typeof daten === 'object' && daten !== null) {
    const o = daten as Record<string, unknown>
    if ('data' in o && Array.isArray(o.data)) bytes = new Uint8Array(o.data)
    else if ('body' in o && Array.isArray(o.body)) bytes = new Uint8Array(o.body)
  }
  if (!bytes || bytes.length === 0) return null
  const istPng = bytes.length >= 4 && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47
  return new Blob([bytes as unknown as BlobPart], { type: istPng ? 'image/png' : 'image/jpeg' })
}

export function bilderSetzen(neu: Record<string, string>): void {
  const behalten = new Set(Object.values(neu))
  for (const url of Object.values(useStandbild.getState().bilder)) {
    if (!behalten.has(url)) URL.revokeObjectURL(url)
  }
  useStandbild.setState({ bilder: neu })
}

/** Nimmt ein einzelnes Standbild zurück, etwa wenn der Browser es nicht zeichnen kann. */
export function bilderEntfernen(tab: string): void {
  const alt = useStandbild.getState().bilder
  const url = alt[tab]
  if (!url) return
  URL.revokeObjectURL(url)
  const { [tab]: _, ...rest } = alt
  useStandbild.setState({ bilder: rest })
}

const naechstesBild = () => new Promise<void>((fertig) => requestAnimationFrame(() => requestAnimationFrame(() => fertig())))

async function standbildAufnehmen(): Promise<void> {
  const { aktivId, split } = useTabsStore.getState()
  // In der geteilten Ansicht liegen beide Seiten als eigene Webview vor; nur
  // ein Bild ließe die andere Hälfte leer zurück.
  const ids = [aktivId, ...(split ? [split.sekundaerId] : [])].filter((id): id is string => Boolean(id))
  const neu: Record<string, string> = {}
  for (const id of ids) {
    try {
      const daten = await nativ.tabStandbild(id)
      const blob = bytesZuBlob(daten)
      if (blob) neu[id] = URL.createObjectURL(blob)
    } catch {
      // Startseite, abgestürzter Tab oder zu langsam: dieser Tab geht ohne Bild weg.
    }
  }
  if (Object.keys(neu).length === 0) return
  bilderSetzen(neu)
  // Erst wenn die Bilder gezeichnet sind, dürfen die Tabs weg.
  await naechstesBild()
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
      if (!ziel) bilderSetzen({})
    }
  } catch {
    // Der nächste Wechsel im DOM versucht es erneut.
  } finally {
    laeuft = false
  }
}

function istUeberdeckung(el: Element, mainRect: DOMRect | null): boolean {
  if (el.hasAttribute('data-msb-verdeckt')) return true

  // Dialoge/Menüs innerhalb von Seitenpanels (.msb-bereich oder aside ohne Dialog)
  // oder Dropdown-Menüs aus solchen Panels liegen neben der Seite und verdecken sie nicht.
  if (el.closest('.msb-bereich, aside:not([role="dialog"])')) return false
  if (el.hasAttribute('data-msm-in-panel')) return false

  // Wenn eine Haupt-Seitenfläche im DOM existiert und reale Abmessungen hat:
  // Nur verdecken, wenn das Overlay tatsächlich in den Seitenbereich ragt.
  if (mainRect && mainRect.width > 0 && mainRect.height > 0) {
    const r = el.getBoundingClientRect()
    if (r.width > 0 && r.height > 0) {
      const schneidet = !(
        r.right <= mainRect.left ||
        r.left >= mainRect.right ||
        r.bottom <= mainRect.top ||
        r.top >= mainRect.bottom
      )
      if (!schneidet) return false
    }
  }

  return true
}

function abgleichen(): void {
  const main = document.querySelector('main')
  const mainRect = main ? main.getBoundingClientRect() : null
  const kandidaten = document.querySelectorAll(UEBERLAGERUNG)
  let gefunden = false
  for (let i = 0; i < kandidaten.length; i++) {
    if (istUeberdeckung(kandidaten[i], mainRect)) {
      gefunden = true
      break
    }
  }
  gewuenscht = gefunden
  void nachziehen()
}

/**
 * Wartet, bis nichts mehr über der Seite liegt und der Tab wieder vorne ist,
 * etwa nach dem Schließen eines Menüs. Ein verdeckter Tab gibt kein Bild her.
 */
export async function seiteFrei(frist = 2000): Promise<boolean> {
  const ende = performance.now() + frist
  while (performance.now() < ende) {
    abgleichen()
    if (!gewuenscht && !angewandt && !laeuft) {
      await naechstesBild()
      return true
    }
    await naechstesBild()
  }
  return false
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
      attributeFilter: ['aria-modal', 'role', 'data-ankerfenster', 'data-msb-verdeckt', 'data-msm-in-panel'],
    })
    abgleichen()
    return () => {
      beobachter.disconnect()
      cancelAnimationFrame(geplant)
    }
  }, [])
}
