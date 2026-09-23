/**
 * @vitest-environment node
 *
 * Die Ausbreitung von Serienterminen — gegen die gemeinsamen Vektoren.
 *
 * Dieselbe Datei liest `backend/tests/test_kalender_serie.py`. Weichen die
 * beiden Implementierungen voneinander ab, zeigt die Ansicht etwas anderes an,
 * als die Erinnerung verschickt — und niemand merkt es, weil beide Seiten für
 * sich genommen grün sind.
 *
 * Node-Umgebung, weil die Datei über `fs` gelesen wird und das Modul selbst
 * kein DOM braucht.
 */

import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

import {
  LEERE_SERIE,
  LEERES_DOKUMENT,
  SerienRegelFehler,
  type Serie,
  ausbreiten,
  kurzform,
  regelLesen,
  regelSchreiben,
  regelZerlegen,
  serieBauen,
  serieLesen,
  serieSchreiben,
} from './kalenderSerie'

const hier = dirname(fileURLToPath(import.meta.url))
const vektorenPfad = resolve(hier, '../../../tests/fixtures/kalender_serie_vektoren.json')

interface ErwartetesVorkommen {
  schluessel: string
  start: string
  ende: string
  ist_abweichung?: boolean
  titel?: string
}

interface GueltigerFall {
  name: string
  rrule: string | null
  ausnahmen?: string[]
  abweichungen?: Record<string, { start?: string; ende?: string; titel?: string }>
  start: string
  ende: string
  ganztaegig: boolean
  zeitzone: string
  fenster_von: string
  fenster_bis: string
  erwartet: ErwartetesVorkommen[]
}

const vektoren = JSON.parse(readFileSync(vektorenPfad, 'utf-8')) as {
  gueltig: GueltigerFall[]
  ungueltig: { name: string; rrule: string }[]
}

const iso = (d: Date): string => d.toISOString().replace(/\.\d{3}Z$/, 'Z')

// ── Die gemeinsamen Fälle ────────────────────────────────────────────────

describe('Ausbreitung nach den gemeinsamen Vektoren', () => {
  for (const fall of vektoren.gueltig) {
    it(fall.name, () => {
      const serie: Serie = {
        rrule: fall.rrule,
        ausnahmen: fall.ausnahmen ?? [],
        abweichungen: fall.abweichungen ?? {},
      }

      const vorkommen = ausbreiten(serie, new Date(fall.start), new Date(fall.ende), {
        ganztaegig: fall.ganztaegig,
        zeitzone: fall.zeitzone,
        fensterVon: new Date(fall.fenster_von),
        fensterBis: new Date(fall.fenster_bis),
      })

      const tatsaechlich = vorkommen.map((v) => {
        const eintrag: ErwartetesVorkommen = {
          schluessel: v.schluessel,
          start: iso(v.start),
          ende: iso(v.ende),
        }
        if (v.istAbweichung) eintrag.ist_abweichung = true
        if (v.titel) eintrag.titel = v.titel
        return eintrag
      })

      expect(tatsaechlich).toEqual(fall.erwartet)
    })
  }
})

describe('Regeln außerhalb der Teilmenge', () => {
  for (const fall of vektoren.ungueltig) {
    it(`weist ab: ${fall.name}`, () => {
      // Abgewiesen, nicht stillschweigend vereinfacht: aus
      // `FREQ=MONTHLY;BYSETPOS=-1` ("letzter Werktag") wäre sonst ein
      // schlichtes "monatlich am Ersten" geworden — falsche Daten unter dem
      // richtigen Namen.
      expect(() => regelLesen(fall.rrule)).toThrow(SerienRegelFehler)
    })
  }
})

// ── Regeltext ─────────────────────────────────────────────────────────────

describe('Regeltext ist kanonisch', () => {
  const faelle: [string, string][] = [
    ['FREQ=YEARLY', 'FREQ=YEARLY'],
    ['freq=yearly', 'FREQ=YEARLY'],
    ['RRULE:FREQ=DAILY;INTERVAL=1', 'FREQ=DAILY'],
    ['  FREQ=WEEKLY ; BYDAY=TH,MO ', 'FREQ=WEEKLY;BYDAY=MO,TH'],
    ['FREQ=WEEKLY;BYDAY=MO,MO', 'FREQ=WEEKLY;BYDAY=MO'],
    ['FREQ=DAILY;UNTIL=20260110T235959Z', 'FREQ=DAILY;UNTIL=20260110'],
  ]
  for (const [eingabe, erwartet] of faelle) {
    it(`${eingabe} → ${erwartet}`, () => {
      // Wichtig, weil der Text verschlüsselt wird: hinge die Gestalt des
      // Ciphertexts an der Reihenfolge der Eingabe, verriete schon ein
      // Vergleich zweier Zeilen etwas über ihren Inhalt.
      expect(regelSchreiben(regelLesen(eingabe))).toBe(erwartet)
    })
  }

  it('weist eine doppelt genannte Angabe ab', () => {
    expect(() => regelLesen('FREQ=DAILY;FREQ=WEEKLY')).toThrow(SerienRegelFehler)
  })
})

// ── Das Dokument ──────────────────────────────────────────────────────────

describe('Das Wiederholungsdokument', () => {
  it('sieht ohne Serie genauso aus wie mit', () => {
    // Der Kern der Metadaten-Entscheidung: wäre das Feld bei Einzelterminen
    // leer, verriete die Datenbank durch bloßes Hinsehen, welche Zeilen
    // Serien sind.
    expect(LEERES_DOKUMENT).toBeTruthy()
    expect(LEERES_DOKUMENT.trim()).not.toBe('')
    expect(serieLesen(LEERES_DOKUMENT)).toEqual(LEERE_SERIE)
  })

  it('schreibt dieselbe Gestalt wie das Backend', () => {
    // `json.dumps(..., sort_keys=True, separators=(",", ":"))` drüben.
    expect(serieSchreiben({ rrule: 'FREQ=YEARLY', ausnahmen: [], abweichungen: {} })).toBe(
      '{"rrule":"FREQ=YEARLY"}',
    )
    expect(
      serieSchreiben({ rrule: 'FREQ=YEARLY', ausnahmen: ['2027-03-14'], abweichungen: {} }),
    ).toBe('{"ausnahmen":["2027-03-14"],"rrule":"FREQ=YEARLY"}')
  })

  it('hält den Umlauf aus', () => {
    const serie: Serie = {
      rrule: 'FREQ=YEARLY',
      ausnahmen: ['2027-03-14'],
      abweichungen: { '2028-03-14': { start: '2028-03-15T09:00:00Z', titel: 'Nachgefeiert' } },
    }
    const wieder = serieLesen(serieSchreiben(serie))
    expect(wieder.rrule).toBe('FREQ=YEARLY')
    expect(wieder.ausnahmen).toEqual(['2027-03-14'])
    expect(wieder.abweichungen['2028-03-14'].titel).toBe('Nachgefeiert')
  })

  const muell = [
    '',
    '   ',
    'kein json',
    '[]',
    'null',
    'sv-cal-v1:AAAABBBBCCCC',
    '{"rrule": "FREQ=MONTHLY;BYSETPOS=-1"}',
    '{"rrule": 42}',
    '{"rrule": "FREQ=YEARLY", "ausnahmen": "keine Liste"}',
    '{"rrule": "FREQ=YEARLY", "abweichungen": ["keine Zuordnung"]}',
  ]
  for (const eingabe of muell) {
    it(`kostet nie den Termin: ${JSON.stringify(eingabe).slice(0, 40)}`, () => {
      // Nachsichtig lesen. Ein noch verschlüsselter Umschlag steht hier
      // absichtlich mit in der Liste: solange der Schlüssel fehlt, ist die
      // Regel unlesbar, und der Termin muss trotzdem erscheinen.
      const serie = serieLesen(eingabe)
      const vorkommen = ausbreiten(
        serie,
        new Date('2026-05-04T10:00:00Z'),
        new Date('2026-05-04T11:00:00Z'),
        {
          zeitzone: 'Europe/Berlin',
          fensterVon: new Date('2026-01-01T00:00:00Z'),
          fensterBis: new Date('2027-01-01T00:00:00Z'),
        },
      )
      expect(vorkommen.length).toBeGreaterThanOrEqual(1)
      expect(iso(vorkommen[0].start)).toBe('2026-05-04T10:00:00Z')
    })
  }
})

// ── Missbrauch ────────────────────────────────────────────────────────────

describe('Missbrauch', () => {
  it('hält die Ansicht bei einer Regel ohne Ende nicht fest', () => {
    const vorkommen = ausbreiten(
      { rrule: 'FREQ=DAILY', ausnahmen: [], abweichungen: {} },
      new Date('2026-01-01T10:00:00Z'),
      new Date('2026-01-01T11:00:00Z'),
      {
        zeitzone: 'Europe/Berlin',
        fensterVon: new Date('2026-01-01T00:00:00Z'),
        fensterBis: new Date('2126-01-01T00:00:00Z'),
      },
    )
    expect(vorkommen.length).toBeLessThanOrEqual(5000)
  })

  const riesig = [
    'FREQ=YEARLY;INTERVAL=9999',
    'FREQ=MONTHLY;INTERVAL=9999',
    'FREQ=WEEKLY;INTERVAL=999999;BYDAY=MO',
    'FREQ=DAILY;INTERVAL=999999',
    'FREQ=DAILY;INTERVAL=999999;COUNT=1000',
  ]
  for (const rrule of riesig) {
    it(`läuft nicht aus dem Kalender: ${rrule}`, () => {
      // Hinter dem Jahr 9999 hört der Kalender auf. Derselbe Fall, der im
      // Backend einen ValueError warf.
      const vorkommen = ausbreiten(
        { rrule, ausnahmen: [], abweichungen: {} },
        new Date('2026-01-01T10:00:00Z'),
        new Date('2026-01-01T11:00:00Z'),
        { zeitzone: 'UTC' },
      )
      expect(Array.isArray(vorkommen)).toBe(true)
    })
  }

  it('erzeugt bei verdrehten Zeiten keine negative Dauer', () => {
    const vorkommen = ausbreiten(
      { rrule: 'FREQ=DAILY;COUNT=2', ausnahmen: [], abweichungen: {} },
      new Date('2026-01-01T10:00:00Z'),
      new Date('2026-01-01T09:00:00Z'),
      { zeitzone: 'Europe/Berlin' },
    )
    expect(vorkommen.every((v) => v.ende.getTime() >= v.start.getTime())).toBe(true)
  })

  it('fällt bei unbekannter Zeitzone auf UTC zurück statt zu werfen', () => {
    const vorkommen = ausbreiten(
      { rrule: 'FREQ=DAILY;COUNT=2', ausnahmen: [], abweichungen: {} },
      new Date('2026-01-01T10:00:00Z'),
      new Date('2026-01-01T11:00:00Z'),
      { zeitzone: 'Mond/Krater' },
    )
    expect(vorkommen.map((v) => iso(v.start))).toEqual([
      '2026-01-01T10:00:00Z',
      '2026-01-02T10:00:00Z',
    ])
  })

  it('liefert nichts, wenn jedes Vorkommen ausgenommen ist', () => {
    const vorkommen = ausbreiten(
      {
        rrule: 'FREQ=DAILY;COUNT=3',
        ausnahmen: ['2026-01-01', '2026-01-02', '2026-01-03'],
        abweichungen: {},
      },
      new Date('2026-01-01T10:00:00Z'),
      new Date('2026-01-01T11:00:00Z'),
      { zeitzone: 'UTC' },
    )
    expect(vorkommen).toEqual([])
  })
})

// ── Für das Formular ──────────────────────────────────────────────────────

describe('Formularhilfen', () => {
  it('baut und zerlegt dieselbe Regel', () => {
    const serie = serieBauen('WEEKLY', { intervall: 2, wochentage: ['MO', 'TH'] })
    expect(serie.rrule).toBe('FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TH')
    expect(regelZerlegen(serie)).toEqual({
      takt: 'WEEKLY',
      intervall: 2,
      wochentage: ['MO', 'TH'],
      bis: null,
      anzahl: null,
    })
  })

  it('behält Ausnahmen, wenn nur der Takt geändert wird', () => {
    // Wer den Takt korrigiert, will nicht auch abgesagte Einzeltermine
    // zurückholen.
    const vorher: Serie = { rrule: 'FREQ=YEARLY', ausnahmen: ['2027-03-14'], abweichungen: {} }
    const nachher = serieBauen('MONTHLY', {}, vorher)
    expect(nachher.rrule).toBe('FREQ=MONTHLY')
    expect(nachher.ausnahmen).toEqual(['2027-03-14'])
  })

  it('löst die Serie auf, wenn kein Takt gewählt ist', () => {
    expect(serieBauen(null).rrule).toBeNull()
  })

  const kurz: [string | null, string][] = [
    [null, 'einmalig'],
    ['FREQ=YEARLY', 'jährlich, ohne Ende'],
    ['FREQ=MONTHLY;COUNT=12', 'monatlich, 12 Termine'],
    ['FREQ=WEEKLY;BYDAY=MO,TH', 'wöchentlich (Mo, Do), ohne Ende'],
    ['FREQ=WEEKLY;INTERVAL=2', 'alle 2 Wochen, ohne Ende'],
    ['FREQ=DAILY;UNTIL=20260110', 'täglich bis 10.01.2026'],
  ]
  for (const [rrule, erwartet] of kurz) {
    it(`Kurzform: ${rrule ?? 'ohne Regel'} → ${erwartet}`, () => {
      // Muss Zeichen für Zeichen mit `kalender_serie.kurzform` übereinstimmen:
      // dieselbe Zeile steht in der Vorschlagskarte der KI und im Formular.
      expect(kurzform({ rrule, ausnahmen: [], abweichungen: {} })).toBe(erwartet)
    })
  }

  it('nennt Ausnahmen und Abweichungen wie das Backend', () => {
    expect(
      kurzform({
        rrule: 'FREQ=YEARLY',
        ausnahmen: ['2027-03-14'],
        abweichungen: { '2028-03-14': { start: '2028-03-15T09:00:00Z' } },
      }),
    ).toBe('jährlich, ohne Ende, 1 ausgenommen, 1 verschoben')
  })
})
