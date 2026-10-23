// @vitest-environment node
/**
 * Die Teilmodule der P0-Etappe bleiben geschnitten.
 *
 * Der Schnitt von fünf Monolithen ist nur so lange etwas wert, wie ihn niemand
 * still zurückbaut: eine Datei wächst wieder über 800 Zeilen, ein Modul wird
 * nicht mehr von seiner Fassade geführt, oder zwei Teilmodule importieren sich
 * gegenseitig. Alles drei fällt im Betrieb nicht auf und im Test sofort.
 */

import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { describe, expect, it } from 'vitest'

const HIER = dirname(fileURLToPath(import.meta.url))
const MAX_ZEILEN = 800

/** Fassade → Module, die sie führen muss. */
const FASSADEN: Record<string, { datei: string; module: string[] }> = {
  calling: {
    datei: '../stores/useCallStore.ts',
    module: ['callZustand', 'callTeilnehmer', 'callZugriff', 'callGeraetewechsel'],
  },
  geraet: {
    datei: 'e2eeGeraet.ts',
    module: ['geraeteAblage', 'geraeteVerzeichnis', 'geraeteVertrauen', 'geraeteFreigabe'],
  },
  gruppen: {
    datei: 'gruppenSchluessel.ts',
    module: [
      'gruppenAblage',
      'gruppenMailbox',
      'gruppenVerteilung',
      'gruppenTransport',
      'gruppenNachricht',
      'gruppenSteuerung',
      'dmGeheimnis',
      'geraeteSteuerung',
      'gespraechsAbo',
    ],
  },
  ratchet: {
    datei: 'ratchetSitzung.ts',
    module: ['format', 'aufbau', 'versand', 'lesen', 'nachsend'],
  },
  auswertung: {
    datei: 'umschlagAuswertung.ts',
    module: ['quittung', 'bearbeitung', 'loeschung', 'reaktion', 'verfall', 'anheftung'],
  },
}

/** Reine Helfer ohne eigene Fassaden-Ausfuhr. Sie muessen nur existieren. */
const HILFSMODULE: Record<string, string[]> = {
  gruppen: ['typen', 'zustand'],
  auswertung: ['typen'],
}

function zeilen(pfad: string): number {
  return readFileSync(pfad, 'utf8').split('\n').length
}

function modulDateien(ordner: string): string[] {
  return readdirSync(join(HIER, ordner))
    .filter((name) => name.endsWith('.ts') && !name.endsWith('.test.ts'))
    .map((name) => join(HIER, ordner, name))
}

/** Relative Importe einer Datei, aufgelöst gegen ihren Ordner. */
function importe(pfad: string): string[] {
  const text = readFileSync(pfad, 'utf8')
  const treffer = [...text.matchAll(/from\s+'(\.\.?\/[^']+)'/g)].map((m) => m[1])
  return treffer.map((rel) => join(dirname(pfad), rel).replace(/\\/g, '/'))
}

describe('P0-Teilmodule', () => {
  it('hält jede Datei unter 800 Zeilen', () => {
    const zuGross: string[] = []
    for (const [ordner, fassade] of Object.entries(FASSADEN)) {
      const dateien = [...modulDateien(ordner), join(HIER, fassade.datei)]
      for (const pfad of dateien) {
        const anzahl = zeilen(pfad)
        if (anzahl > MAX_ZEILEN) zuGross.push(`${pfad}: ${anzahl}`)
      }
    }
    expect(zuGross).toEqual([])
  })

  it('führt jedes Teilmodul über seine Fassade', () => {
    const fehlend: string[] = []
    for (const [ordner, fassade] of Object.entries(FASSADEN)) {
      const text = readFileSync(join(HIER, fassade.datei), 'utf8')
      for (const modul of fassade.module) {
        if (!text.includes(`${ordner}/${modul}`)) {
          fehlend.push(`${fassade.datei} → ${ordner}/${modul}`)
        }
      }
    }
    expect(fehlend).toEqual([])
  })

  it('legt jede Datei der Teilmodule an', () => {
    const fehlend: string[] = []
    for (const [ordner, module] of Object.entries(HILFSMODULE)) {
      const vorhanden = new Set(
        readdirSync(join(HIER, ordner)).map((name) => name.replace(/\.ts$/, '')),
      )
      for (const modul of module) {
        if (!vorhanden.has(modul)) fehlend.push(`${ordner}/${modul}.ts`)
      }
    }
    expect(fehlend).toEqual([])
  })

  it('hat keine Zyklen zwischen den Teilmodulen', () => {
    const kanten = new Map<string, string[]>()
    for (const ordner of Object.keys(FASSADEN)) {
      const dateien = modulDateien(ordner)
      const namen = new Set(dateien.map((p) => p.replace(/\\/g, '/')))
      for (const pfad of dateien) {
        const eigen = pfad.replace(/\\/g, '/')
        kanten.set(
          eigen,
          importe(pfad)
            .map((ziel) => (ziel.endsWith('.ts') ? ziel : `${ziel}.ts`))
            .filter((ziel) => namen.has(ziel)),
        )
      }
    }

    const zustaende = new Map<string, 'offen' | 'fertig'>()
    const zyklus: string[] = []

    const besuche = (knoten: string, weg: string[]) => {
      if (zustaende.get(knoten) === 'fertig') return
      if (zustaende.get(knoten) === 'offen') {
        zyklus.push([...weg.slice(weg.indexOf(knoten)), knoten].join(' → '))
        return
      }
      zustaende.set(knoten, 'offen')
      for (const ziel of kanten.get(knoten) ?? []) besuche(ziel, [...weg, knoten])
      zustaende.set(knoten, 'fertig')
    }

    for (const knoten of kanten.keys()) besuche(knoten, [])
    expect(zyklus).toEqual([])
  })
})
