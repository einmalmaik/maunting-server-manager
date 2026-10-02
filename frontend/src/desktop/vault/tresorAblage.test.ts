/**
 * Die Tresor-Ablage zieht vom localStorage nach IndexedDB, je Konto.
 *
 * Geprüft wird an einer echten IndexedDB-Nachbildung (fake-indexeddb), weil
 * die Zusagen an deren Transaktionen hängen: Marke und Daten in einer
 * Transaktion, Schreibvorgänge in der Reihenfolge des Speichers.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { IDBFactory } from 'fake-indexeddb'
import 'fake-indexeddb/auto'
import i18n from '@/i18n'
import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'
import { useToastStore } from '@/stores/toastStore'
import {
  ALT_BLOBS,
  ALT_REVISION,
  ALT_WARTESCHLANGE,
  ablageInIndexedDb,
  ablageLaden,
  ablageLoeschen,
  ablageSchliessen,
  ablageUmziehen,
  blobsLesen,
  blobsSchreiben,
  revisionLesen,
  revisionSchreiben,
  warteschlangeLesen,
  warteschlangeSchreiben,
  type StoredEncryptedEntry,
} from './tresorAblage'

const BUCKET = 'c'.repeat(64)

function eintrag(id: string, ciphertext = `sv-vault-v1:${id}`): StoredEncryptedEntry {
  return { id, ciphertext, revision: 1, is_deleted: false, stand: 1 }
}

function altAblegen(blobs: StoredEncryptedEntry[], queue: StoredEncryptedEntry[], rev: number) {
  localStorage.setItem(`${ALT_BLOBS}${BUCKET}`, JSON.stringify(blobs))
  if (queue.length > 0) localStorage.setItem(`${ALT_WARTESCHLANGE}${BUCKET}`, JSON.stringify(queue))
  localStorage.setItem(`${ALT_REVISION}${BUCKET}`, String(rev))
}

/** Liest die Marke am Speicher vorbei direkt aus der Datenbank des Kontos. */
async function markeInDb(konto: number): Promise<unknown> {
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const req = indexedDB.open(`msm_tresor:konto:${konto}`)
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
  try {
    return await new Promise((resolve, reject) => {
      const req = db.transaction('stand', 'readonly').objectStore('stand').get(BUCKET)
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => reject(req.error)
    })
  } finally {
    db.close()
  }
}

/** Wartet, bis alle angestoßenen Schreibtransaktionen durch sind. */
async function schreibenAbwarten() {
  await new Promise((r) => setTimeout(r, 20))
}

describe('Tresor-Ablage in IndexedDB', () => {
  beforeEach(() => {
    ablageSchliessen()
    localStorage.clear()
    globalThis.indexedDB = new IDBFactory()
    setzeAngemeldetesKonto(1)
  })

  afterEach(() => {
    ablageSchliessen()
    setzeAngemeldetesKonto(null)
    vi.restoreAllMocks()
  })

  it('zieht ohne ungesendete Änderungen sofort um und leert den localStorage', async () => {
    altAblegen([eintrag('a'), eintrag('b')], [], 7)

    await ablageLaden(BUCKET)

    expect(ablageInIndexedDb(BUCKET)).toBe(true)
    expect(localStorage.getItem(`${ALT_BLOBS}${BUCKET}`)).toBeNull()
    expect(localStorage.getItem(`${ALT_REVISION}${BUCKET}`)).toBeNull()
    expect(await markeInDb(1)).toEqual({ bucket: BUCKET, rev: 7, umgezogen: true })

    // Nach einem Neustart kommt alles aus IndexedDB.
    ablageSchliessen()
    await ablageLaden(BUCKET)
    expect(blobsLesen(BUCKET).map((e) => e.id).sort()).toEqual(['a', 'b'])
    expect(revisionLesen(BUCKET)).toBe(7)
  })

  it('sagt, wenn das Gerät nicht speichern kann', async () => {
    // Bis 02.10.2026 stand ein voller Gerätespeicher nur in der Konsole, und
    // offline Gespeichertes war nach einem Neustart still weg.
    await ablageLaden(BUCKET)
    expect(ablageInIndexedDb(BUCKET)).toBe(true)
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    vi.spyOn(IDBDatabase.prototype, 'transaction').mockImplementation(() => {
      throw new DOMException('voll', 'QuotaExceededError')
    })
    warteschlangeSchreiben(BUCKET, [eintrag('a')])
    expect(useToastStore.getState().toasts.map((t) => t.message)).toContain(i18n.t('mss.vault.ablageVoll'))
  })

  it('wartet mit dem Umzug, bis die Warteschlange leer ist', async () => {
    altAblegen([eintrag('a')], [eintrag('a', 'neu')], 3)

    await ablageLaden(BUCKET)

    expect(ablageInIndexedDb(BUCKET)).toBe(false)
    expect(warteschlangeLesen(BUCKET)).toHaveLength(1)
    // Eine ältere App-Version fände ihre Warteschlange noch.
    expect(localStorage.getItem(`${ALT_WARTESCHLANGE}${BUCKET}`)).not.toBeNull()

    warteschlangeSchreiben(BUCKET, [])
    await ablageUmziehen()

    expect(ablageInIndexedDb(BUCKET)).toBe(true)
    expect(localStorage.getItem(`${ALT_BLOBS}${BUCKET}`)).toBeNull()
  })

  it('schreibt Änderungen in der Reihenfolge des Speichers, ohne dazwischen zu warten', async () => {
    await ablageLaden(BUCKET)
    expect(ablageInIndexedDb(BUCKET)).toBe(true)

    // Wie der Sync: drei Schreibvorgänge in einem Zug, kein `await`.
    blobsSchreiben(BUCKET, [eintrag('a'), eintrag('b')])
    warteschlangeSchreiben(BUCKET, [eintrag('b'), eintrag('a')])
    blobsSchreiben(BUCKET, [eintrag('b', 'b2')])
    warteschlangeSchreiben(BUCKET, [eintrag('b'), eintrag('a'), eintrag('c')])
    warteschlangeSchreiben(BUCKET, [eintrag('a'), eintrag('c')])
    revisionSchreiben(BUCKET, 12)
    await schreibenAbwarten()

    ablageSchliessen()
    await ablageLaden(BUCKET)
    expect(blobsLesen(BUCKET)).toEqual([eintrag('b', 'b2')])
    expect(warteschlangeLesen(BUCKET).map((e) => e.id)).toEqual(['a', 'c'])
    expect(revisionLesen(BUCKET)).toBe(12)
  })

  it('hält die Reihenfolge der Warteschlange: eine geänderte Fassung rückt ans Ende', async () => {
    await ablageLaden(BUCKET)
    warteschlangeSchreiben(BUCKET, [eintrag('a'), eintrag('b')])
    warteschlangeSchreiben(BUCKET, [eintrag('b'), eintrag('a', 'a2')])
    await schreibenAbwarten()

    ablageSchliessen()
    await ablageLaden(BUCKET)
    expect(warteschlangeLesen(BUCKET).map((e) => e.ciphertext)).toEqual(['sv-vault-v1:b', 'a2'])
  })

  it('zeigt einem anderen Konto nichts von der Ablage des ersten', async () => {
    await ablageLaden(BUCKET)
    blobsSchreiben(BUCKET, [eintrag('geheim')])
    warteschlangeSchreiben(BUCKET, [eintrag('geheim')])
    await schreibenAbwarten()

    // Kontowechsel ohne Abmelden, derselbe Bucket (dasselbe Master-Passwort).
    setzeAngemeldetesKonto(2)
    await ablageLaden(BUCKET)

    expect(blobsLesen(BUCKET)).toEqual([])
    expect(warteschlangeLesen(BUCKET)).toEqual([])
    expect(revisionLesen(BUCKET)).toBe(0)
  })

  it('setzt die Marke nur zusammen mit den Daten: scheitert der Umzug, bleibt alles im localStorage', async () => {
    altAblegen([eintrag('a'), eintrag('b')], [], 5)
    const echtesPut = IDBObjectStore.prototype.put
    let aufrufe = 0
    vi.spyOn(IDBObjectStore.prototype, 'put').mockImplementation(function (this: IDBObjectStore, ...args) {
      // Der zweite Eintrag passt nicht mehr auf die Platte.
      if (++aufrufe === 2) throw new DOMException('voll', 'QuotaExceededError')
      return echtesPut.apply(this, args)
    })
    vi.spyOn(console, 'warn').mockImplementation(() => {})

    await ablageLaden(BUCKET)

    expect(ablageInIndexedDb(BUCKET)).toBe(false)
    expect(JSON.parse(localStorage.getItem(`${ALT_BLOBS}${BUCKET}`)!)).toHaveLength(2)
    expect(localStorage.getItem(`${ALT_REVISION}${BUCKET}`)).toBe('5')
    await schreibenAbwarten()
    expect(await markeInDb(1)).toBeUndefined()

    // Der nächste Start findet keine Marke und keine halben Daten, sondern
    // zieht noch einmal vollständig um.
    vi.restoreAllMocks()
    ablageSchliessen()
    await ablageLaden(BUCKET)
    expect(ablageInIndexedDb(BUCKET)).toBe(true)
    expect(blobsLesen(BUCKET).map((e) => e.id).sort()).toEqual(['a', 'b'])
    expect(revisionLesen(BUCKET)).toBe(5)
  })

  it('übernimmt, was nach dem Umzug wieder im localStorage landete, samt Warteschlange', async () => {
    await ablageLaden(BUCKET)
    blobsSchreiben(BUCKET, [eintrag('a'), eintrag('b')])
    warteschlangeSchreiben(BUCKET, [eintrag('b')])
    revisionSchreiben(BUCKET, 20)
    await schreibenAbwarten()
    ablageSchliessen()

    // Eine ältere App-Version schreibt wieder in den localStorage.
    altAblegen([eintrag('a', 'a-alt-app')], [eintrag('d')], 9)

    await ablageLaden(BUCKET)

    expect(ablageInIndexedDb(BUCKET)).toBe(true)
    const blobs = new Map(blobsLesen(BUCKET).map((e) => [e.id, e.ciphertext]))
    expect(blobs.get('a')).toBe('a-alt-app')
    expect(blobs.get('b')).toBe('sv-vault-v1:b')
    expect(warteschlangeLesen(BUCKET).map((e) => e.id)).toEqual(['b', 'd'])
    // Der kleinere Stand: der nächste Sync holt nach, was dazwischen lag.
    expect(revisionLesen(BUCKET)).toBe(9)
    expect(localStorage.getItem(`${ALT_WARTESCHLANGE}${BUCKET}`)).toBeNull()
  })

  it('bleibt ohne angemeldetes Konto beim localStorage', async () => {
    setzeAngemeldetesKonto(null)
    altAblegen([eintrag('a')], [], 2)

    await ablageLaden(BUCKET)
    blobsSchreiben(BUCKET, [eintrag('a'), eintrag('b')])

    expect(ablageInIndexedDb(BUCKET)).toBe(false)
    expect(JSON.parse(localStorage.getItem(`${ALT_BLOBS}${BUCKET}`)!)).toHaveLength(2)
  })

  it('löscht beim Zurücksetzen die Ablage des Kontos', async () => {
    await ablageLaden(BUCKET)
    blobsSchreiben(BUCKET, [eintrag('a')])
    await schreibenAbwarten()

    await ablageLoeschen()
    await ablageLaden(BUCKET)

    expect(blobsLesen(BUCKET)).toEqual([])
  })
})
