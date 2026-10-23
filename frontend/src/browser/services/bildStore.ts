/**
 * Die Symbole importierter Lesezeichen, je Herkunft eine PNG-Datenadresse.
 *
 * Sie liegen getrennt vom Verlauf (`msb:verlauf`), weil Bilder den Speicher
 * schnell füllen. Der Quota-Toast der Ablage bleibt die letzte Sicherung;
 * hier gelten zusätzlich 128 Herkünfte und 1 MiB.
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

import { ablage } from './ablage'
import { BILDER_MAX, BILD_GESAMT_MAX, herkunft, istBildDaten } from './importDaten'

interface BildZustand {
  bilder: Record<string, string>
  /** Ergänzt vorhandene Symbole; vorhandene Herkünfte bleiben. */
  ergaenzen: (neu: Record<string, string>) => number
  /** Behält nur die Herkünfte dieser Adressen. */
  behalten: (herkuenfte: string[]) => void
}

function bereinige(roh: unknown): Record<string, string> {
  if (!roh || typeof roh !== 'object' || Array.isArray(roh)) return {}
  const aus: Record<string, string> = {}
  let gesamt = 0
  for (const [schluessel, wert] of Object.entries(roh)) {
    if (Object.keys(aus).length >= BILDER_MAX) break
    if (!istBildDaten(wert)) continue
    const h = herkunft(schluessel)
    if (!h || aus[h]) continue
    if (gesamt + wert.length > BILD_GESAMT_MAX) break
    aus[h] = wert
    gesamt += wert.length
  }
  return aus
}

export const useBildStore = create<BildZustand>()(
  persist(
    (set) => ({
      bilder: {},
      ergaenzen: (neu) => {
        let hinzu = 0
        set((s) => {
          const bilder = { ...s.bilder }
          let gesamt = Object.values(bilder).reduce((summe, daten) => summe + daten.length, 0)
          for (const [schluessel, daten] of Object.entries(neu)) {
            if (Object.keys(bilder).length >= BILDER_MAX) break
            if (!istBildDaten(daten)) continue
            const h = herkunft(schluessel)
            if (!h || bilder[h]) continue
            if (gesamt + daten.length > BILD_GESAMT_MAX) break
            bilder[h] = daten
            gesamt += daten.length
            hinzu++
          }
          return { bilder }
        })
        return hinzu
      },
      behalten: (herkuenfte) =>
        set((s) => {
          const erlaubt = new Set(herkuenfte.map(herkunft).filter((h): h is string => !!h))
          return { bilder: Object.fromEntries(Object.entries(s.bilder).filter(([h]) => erlaubt.has(h))) }
        }),
    }),
    {
      name: 'msb:bilder',
      version: 1,
      storage: ablage,
      merge: (gespeichert, aktuell) => ({
        ...aktuell,
        bilder: bereinige((gespeichert as { bilder?: unknown } | null)?.bilder),
      }),
    },
  ),
)
