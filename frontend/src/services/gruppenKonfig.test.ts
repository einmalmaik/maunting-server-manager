// @vitest-environment node
/**
 * Der Gruppenzustand: was durchkommt und was nicht.
 *
 * Node-Umgebung, nicht jsdom: die Unterschrift läuft mit echten ECDSA-P-256-
 * Schlüsseln aus der WebCrypto von Node. Genau sie ist hier der Gegenstand —
 * eine Rechtetabelle, die jedes Mitglied schreiben kann, wäre keine.
 *
 * Nachgebildet sind zwei Dinge, die anderswo schon geprüft sind: die
 * Gruppenverschlüsselung (`gruppenSchluessel.test.ts`) und die Ablage im
 * Backend (`backend/tests/test_group_config.py`). Der Ersatz für die Ablage
 * hält sich dabei an dieselbe Regel wie der echte Server — genau eine Revision
 * weiter, sonst 409 —, weil sonst die halbe Aussage dieser Datei verloren geht.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { erzeugeSignaturPaar, type SignaturPaar } from './absenderSignatur'

interface TestGeraet {
  kennung: string
  konto: number
  paar: SignaturPaar
}

let ich: TestGeraet
const verzeichnis = new Map<string, string>()
/** Auf `false` gesetzt, wenn das Gerät gerade nicht unterschreiben kann. */
let kannUnterschreiben = true

vi.mock('./e2eeGeraet', () => ({
  eigenesGeraet: async () => {
    if (!kannUnterschreiben) throw new Error('verschlossen')
    return {
      kennung: ich.kennung,
      paar: { publicKeyJwk: '{}', privateKeyJwk: '{}' },
      signaturPaar: ich.paar,
    }
  },
  signaturSchluesselVon: async (konto: number, geraet: string) =>
    verzeichnis.get(`${konto}:${geraet}`) ?? null,
}))

// Die Gruppenverschlüsselung als Attrappe: Umschlag drumherum, sonst nichts.
// `schluesselFehlt` bildet den einen Fall nach, den der Aufrufer anders
// behandeln muss als einen Bruch — ein Gerät, das den Schlüssel noch nicht hat.
let schluesselFehlt = false
vi.mock('./gruppenSchluessel', () => ({
  verschluesseleFuerGruppe: async (_kontext: unknown, klartext: string) =>
    `sv-e2ee-group-v1:testkey.${btoa(unescape(encodeURIComponent(klartext)))}`,
  entschluesseleGruppenUmschlag: async (_groupId: number, umschlag: string) => {
    if (schluesselFehlt) return { art: 'kein-schluessel', keyId: 'testkey' }
    const rumpf = umschlag.split('.').slice(1).join('.')
    if (!rumpf) return { art: 'unbekannt' }
    return { art: 'klartext', text: decodeURIComponent(escape(atob(rumpf))) }
  },
}))

// Die Ablage: ein Block, eine Revision, und dieselbe Regel wie im Backend.
let abgelegt: { blob: string; revision: number } | null = null
vi.mock('@/api/social', () => ({
  getGroupConfig: async () =>
    abgelegt ? { group_id: 1, blob: abgelegt.blob, revision: abgelegt.revision, updated_at: '' } : null,
  putGroupConfig: async (_groupId: number, blob: string, erwartet: number) => {
    const aktuell = abgelegt?.revision ?? 0
    if (erwartet !== aktuell) {
      const fehler = new Error('Konflikt') as Error & { status: number }
      fehler.status = 409
      throw fehler
    }
    abgelegt = { blob, revision: aktuell + 1 }
    return { group_id: 1, blob, revision: abgelegt.revision, updated_at: '' }
  },
}))

import {
  aendereGruppenzustand,
  KONFIG_FORMAT,
  ladeGruppenzustand,
  leererGruppenzustand,
  rechteAusZustand,
  schreibeGruppenzustand,
  type Gruppenzustand,
} from './gruppenKonfig'
import { signiereNutzlast } from './nutzlastSignatur'
import type { GruppenKontext } from './gruppenSchluessel'

const ANNA = 101
const BERT = 102
const MAILBOX = 'm'.repeat(32)

const KONTEXT: GruppenKontext = {
  groupId: 1,
  blindMailboxId: MAILBOX,
  eigeneId: ANNA,
  mitglieder: [ANNA, BERT],
}

/** Anna darf verwalten, Bert nicht. */
const NUR_ANNA = (konto: number) => konto === ANNA

async function neuesGeraet(konto: number, kennung: string): Promise<TestGeraet> {
  const paar = await erzeugeSignaturPaar()
  verzeichnis.set(`${konto}:${kennung}`, paar.publicKeyJwk)
  return { kennung, konto, paar }
}

function zustand(rollen: Gruppenzustand['rollen'], zuordnung: Record<string, number[]> = {}): Gruppenzustand {
  return { v: KONFIG_FORMAT, rollen, zuordnung }
}

const AUFSICHT = {
  id: 'custom_1',
  name: 'Aufsicht',
  beschreibung: 'Räumt auf',
  rechte: ['delete_messages'],
}

/** Legt einen Block direkt in die Ablage — ohne den Weg über das Modul. */
function legeAb(nutzlast: Record<string, unknown>, revision = 1): void {
  const klartext = JSON.stringify(nutzlast)
  abgelegt = {
    blob: `sv-e2ee-group-v1:testkey.${btoa(unescape(encodeURIComponent(klartext)))}`,
    revision,
  }
}

describe('gruppenKonfig', () => {
  beforeEach(async () => {
    verzeichnis.clear()
    abgelegt = null
    schluesselFehlt = false
    kannUnterschreiben = true
    ich = await neuesGeraet(ANNA, 'annas-laptop')
  })

  // ── Der gewöhnliche Weg ──────────────────────────────────────────────────

  it('gibt einen geschriebenen Zustand unverändert zurück', async () => {
    const geschrieben = await schreibeGruppenzustand(KONTEXT, zustand([AUFSICHT]), 0)
    expect(geschrieben).toEqual({ art: 'gespeichert', revision: 1 })

    const gelesen = await ladeGruppenzustand(KONTEXT, NUR_ANNA)

    expect(gelesen.art).toBe('zustand')
    if (gelesen.art !== 'zustand') return
    expect(gelesen.zustand.rollen).toEqual([AUFSICHT])
    expect(gelesen.revision).toBe(1)
    expect(gelesen.vonKonto).toBe(ANNA)
  })

  it('meldet eine Gruppe ohne Zustand als leer', async () => {
    expect((await ladeGruppenzustand(KONTEXT, NUR_ANNA)).art).toBe('leer')
  })

  it('zählt die Revision bei jedem Schreiben weiter', async () => {
    await schreibeGruppenzustand(KONTEXT, zustand([]), 0)
    const zweiter = await schreibeGruppenzustand(KONTEXT, zustand([AUFSICHT]), 1)

    expect(zweiter).toEqual({ art: 'gespeichert', revision: 2 })
  })

  // ── Die Beglaubigung ─────────────────────────────────────────────────────

  it('verwirft einen unsignierten Block', async () => {
    // Der eigentliche Angriff: jedes Mitglied hält den Gruppenschlüssel und
    // könnte ohne diese Prüfung eine Rechtetabelle hinlegen.
    legeAb({ v: KONFIG_FORMAT, rollen: [AUFSICHT], zuordnung: { custom_1: [BERT] } })

    const gelesen = await ladeGruppenzustand(KONTEXT, NUR_ANNA)

    expect(gelesen.art).toBe('unbefugt')
  })

  it('verwirft einen Block mit gefälschter Unterschrift', async () => {
    const echt = await signiereNutzlast(MAILBOX, ANNA, {
      v: KONFIG_FORMAT,
      rollen: [AUFSICHT],
      zuordnung: {},
    })
    // Inhalt nachträglich ändern, Unterschrift stehen lassen.
    legeAb({ ...echt, rollen: [{ ...AUFSICHT, rechte: ['manage_roles'] }] })

    const gelesen = await ladeGruppenzustand(KONTEXT, NUR_ANNA)

    expect(gelesen.art).toBe('unbefugt')
  })

  it('verwirft einen echt signierten Block von jemandem ohne Befugnis', async () => {
    // Berts Unterschrift ist gültig — er durfte nur nicht.
    ich = await neuesGeraet(BERT, 'berts-handy')
    const vonBert = await signiereNutzlast(MAILBOX, BERT, {
      v: KONFIG_FORMAT,
      rollen: [AUFSICHT],
      zuordnung: { custom_1: [BERT] },
    })
    legeAb(vonBert)

    const gelesen = await ladeGruppenzustand(KONTEXT, NUR_ANNA)

    expect(gelesen.art).toBe('unbefugt')
    if (gelesen.art !== 'unbefugt') return
    expect(gelesen.behauptet).toBe(BERT)
  })

  it('schreibt nichts, wenn dieses Gerät nicht unterschreiben kann', async () => {
    kannUnterschreiben = false

    const ergebnis = await schreibeGruppenzustand(KONTEXT, zustand([AUFSICHT]), 0)

    expect(ergebnis).toEqual({ art: 'nicht-unterschreibbar' })
    expect(abgelegt).toBeNull()
  })

  // ── Lesbarkeit ───────────────────────────────────────────────────────────

  it('meldet einen fehlenden Schlüssel als solchen, nicht als Bruch', async () => {
    await schreibeGruppenzustand(KONTEXT, zustand([AUFSICHT]), 0)
    schluesselFehlt = true

    const gelesen = await ladeGruppenzustand(KONTEXT, NUR_ANNA)

    expect(gelesen.art).toBe('kein-schluessel')
  })

  it('fasst eine unbekannte Formatnummer nicht an', async () => {
    // Von einem neueren Gerät. Würde es hier angenommen, schriebe das ältere
    // Gerät beim nächsten Speichern alles zurück, was es nicht verstanden hat.
    legeAb(await signiereNutzlast(MAILBOX, ANNA, { v: 99, rollen: [], zuordnung: {} }))

    const gelesen = await ladeGruppenzustand(KONTEXT, NUR_ANNA)

    expect(gelesen.art).toBe('unlesbar')
  })

  it('verwirft eine Zuordnung, die keine Kontenliste ist', async () => {
    legeAb(
      await signiereNutzlast(MAILBOX, ANNA, {
        v: KONFIG_FORMAT,
        rollen: [AUFSICHT],
        zuordnung: { custom_1: ['Bert'] },
      }),
    )

    expect((await ladeGruppenzustand(KONTEXT, NUR_ANNA)).art).toBe('unlesbar')
  })

  // ── Nebenläufigkeit ──────────────────────────────────────────────────────

  it('meldet einen Konflikt, statt den neueren Stand zu überschreiben', async () => {
    await schreibeGruppenzustand(KONTEXT, zustand([]), 0)
    await schreibeGruppenzustand(KONTEXT, zustand([AUFSICHT]), 1)

    const spaet = await schreibeGruppenzustand(KONTEXT, zustand([]), 1)

    expect(spaet).toEqual({ art: 'konflikt' })
    const gelesen = await ladeGruppenzustand(KONTEXT, NUR_ANNA)
    expect(gelesen.art === 'zustand' && gelesen.zustand.rollen).toEqual([AUFSICHT])
  })

  it('rechnet die Änderung nach einem Konflikt auf den frischen Stand', async () => {
    await schreibeGruppenzustand(KONTEXT, zustand([AUFSICHT]), 0)

    // Ein zweites Gerät schiebt sich zwischen Lesen und Schreiben. Die Änderung
    // darf dessen Rolle nicht wegnehmen — sie ist eine Ergänzung, kein Ersatz.
    let dazwischen = false
    const ergebnis = await aendereGruppenzustand(KONTEXT, NUR_ANNA, (vorher) => {
      if (!dazwischen) {
        dazwischen = true
        abgelegt = { blob: abgelegt!.blob, revision: abgelegt!.revision + 1 }
      }
      return {
        ...vorher,
        rollen: [...vorher.rollen, { id: 'custom_2', name: 'Nacht', beschreibung: '', rechte: [] }],
      }
    })

    expect(ergebnis).toEqual({ art: 'gespeichert', revision: 3 })
    const gelesen = await ladeGruppenzustand(KONTEXT, NUR_ANNA)
    expect(gelesen.art === 'zustand' && gelesen.zustand.rollen.map((r) => r.id)).toEqual([
      'custom_1',
      'custom_2',
    ])
  })

  it('schreibt nicht, wenn der bisherige Stand nicht lesbar ist', async () => {
    legeAb({ v: KONFIG_FORMAT, rollen: [], zuordnung: {} })
    const vorher = abgelegt

    const ergebnis = await aendereGruppenzustand(KONTEXT, NUR_ANNA, (z) => z)

    expect(ergebnis.art).toBe('nicht-lesbar')
    expect(abgelegt).toEqual(vorher)
  })

  // ── Auswertung ───────────────────────────────────────────────────────────

  it('gibt nur die Rechte der getragenen Rollen heraus', () => {
    const z = zustand(
      [AUFSICHT, { id: 'custom_2', name: 'Nacht', beschreibung: '', rechte: ['kick_members'] }],
      { custom_1: [BERT], custom_2: [ANNA] },
    )

    expect([...rechteAusZustand(z, BERT)]).toEqual(['delete_messages'])
    expect([...rechteAusZustand(z, ANNA)]).toEqual(['kick_members'])
  })

  it('gibt einem Konto ohne Rolle nichts', () => {
    expect(rechteAusZustand(zustand([AUFSICHT], { custom_1: [BERT] }), ANNA).size).toBe(0)
  })

  it('beginnt leer', () => {
    expect(leererGruppenzustand()).toEqual({
      v: KONFIG_FORMAT,
      name: null,
      beschreibung: null,
      logo: null,
      rollen: [],
      zuordnung: {},
    })
  })

  // ── Name, Beschreibung und Logo ──────────────────────────────────────────
  //
  // Seit Stufe 6 stehen sie in diesem Block, weil `chat_groups.name` geräumt
  // ist. Sie reisen damit denselben Weg wie die Rollen — aber sie werden
  // anders gelesen: nachsichtig statt streng. Ein unbrauchbares Logo kostet
  // ein Bild, eine unbrauchbare Rechtetabelle kostet die Gruppe.

  it('trägt Name, Beschreibung und Logo durch', async () => {
    const LOGO = 'data:image/png;base64,iVBORw0KGgo='
    await schreibeGruppenzustand(
      KONTEXT,
      { ...zustand([AUFSICHT]), name: 'Küchenplanung', beschreibung: 'Wer bringt was', logo: LOGO },
      0,
    )

    const gelesen = await ladeGruppenzustand(KONTEXT, NUR_ANNA)

    expect(gelesen.art).toBe('zustand')
    if (gelesen.art !== 'zustand') return
    expect(gelesen.zustand.name).toBe('Küchenplanung')
    expect(gelesen.zustand.beschreibung).toBe('Wer bringt was')
    expect(gelesen.zustand.logo).toBe(LOGO)
    // Und die Rollen daneben bleiben, was sie waren.
    expect(gelesen.zustand.rollen).toEqual([AUFSICHT])
  })

  it('wirft einen Block nicht weg, nur weil der Name unbrauchbar ist', async () => {
    /*
     * Der Unterschied zu den Rollen, und er ist Absicht. Ein Block mit
     * kaputter Rechtetabelle ist unbrauchbar und wird verworfen — sonst
     * entschiede ein Angreifer per Formfehler über die Rechte. Ein kaputter
     * Name ist eine fehlende Überschrift. Den ganzen Block deswegen
     * wegzuwerfen, nähme der Gruppe ihre Rollen mit.
     */
    const beglaubigt = await signiereNutzlast(MAILBOX, ANNA, {
      v: KONFIG_FORMAT,
      name: 42,
      beschreibung: { boeses: 'objekt' },
      logo: 'data:text/html,<script>',
      rollen: [AUFSICHT],
      zuordnung: { custom_1: [BERT] },
    })
    legeAb(beglaubigt)

    const gelesen = await ladeGruppenzustand(KONTEXT, NUR_ANNA)

    expect(gelesen.art).toBe('zustand')
    if (gelesen.art !== 'zustand') return
    expect(gelesen.zustand.name).toBeNull()
    expect(gelesen.zustand.beschreibung).toBeNull()
    expect(gelesen.zustand.logo).toBeNull()
    expect(gelesen.zustand.rollen).toEqual([AUFSICHT])
  })

  it('kürzt einen überlangen Namen, statt ihn zu übernehmen', async () => {
    const beglaubigt = await signiereNutzlast(MAILBOX, ANNA, {
      v: KONFIG_FORMAT,
      name: 'x'.repeat(500),
      beschreibung: 'y'.repeat(1000),
      rollen: [],
      zuordnung: {},
    })
    legeAb(beglaubigt)

    const gelesen = await ladeGruppenzustand(KONTEXT, NUR_ANNA)

    expect(gelesen.art).toBe('zustand')
    if (gelesen.art !== 'zustand') return
    expect(gelesen.zustand.name).toHaveLength(64)
    expect(gelesen.zustand.beschreibung).toHaveLength(256)
  })
})
