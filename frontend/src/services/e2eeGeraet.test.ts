// @vitest-environment node
/**
 * Die Identität dieses Geräts.
 *
 * Zwei Zusagen tragen hier alles: sie überlebt einen Neustart, und ihr
 * privater Teil verlässt das Gerät nie. Bricht die erste, verliert das Gerät
 * bei jedem Start seinen Gesprächsfaden — es meldet sich mit einem neuen
 * Schlüssel, die Gegenstellen bauen neue Sitzungen auf, und alles davor bleibt
 * unlesbar. Bricht die zweite, ist die ganze Umstellung von 09/2026 umsonst.
 *
 * Node-Umgebung: DIS prüft Eingaben mit `instanceof Uint8Array`.
 * IndexedDB gibt es dort nicht, also steht sie hier von Hand — dieselbe Form
 * wie in `messengerLocalStore.test.ts`, und keine neue Abhängigkeit dafür.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

const { hochgeladen, fremdeGeraete } = vi.hoisted(() => ({
  hochgeladen: [] as { deviceId: string; publicKey: string; label: string }[],
  fremdeGeraete: { liste: [] as { device_id: string; public_key: string }[], rufe: 0 },
}))

vi.mock('@/api/social', () => ({
  putEigenesGeraet: vi.fn(async (req: { deviceId: string; publicKey: string; label: string }) => {
    hochgeladen.push(req)
    return { device_id: req.deviceId, public_key: req.publicKey, label: req.label }
  }),
  getE2eeGeraete: vi.fn(async () => {
    fremdeGeraete.rufe += 1
    return fremdeGeraete.liste
  }),
}))

/**
 * Eine IndexedDB im Arbeitsspeicher. Trägt genau, was `e2eeGeraet` benutzt:
 * einen Store mit `keyPath: 'id'`, `get` und `put`.
 */
function installiereIdb(platte: Map<string, any>) {
  const store = (name: string) => ({
    get: (schluessel: string) => {
      const req: any = { onsuccess: null, onerror: null, result: platte.get(`${name}:${schluessel}`) }
      queueMicrotask(() => req.onsuccess?.())
      return req
    },
    put: (wert: any) => {
      platte.set(`${name}:${wert.id}`, wert)
      const req: any = { onsuccess: null, onerror: null }
      queueMicrotask(() => req.onsuccess?.())
      return req
    },
  })

  const db = {
    objectStoreNames: { contains: () => true },
    createObjectStore: () => {},
    transaction: (name: string) => ({ objectStore: () => store(name) }),
  }

  ;(globalThis as any).indexedDB = {
    open: () => {
      const req: any = { onsuccess: null, onerror: null, onupgradeneeded: null, result: db }
      queueMicrotask(() => {
        req.onupgradeneeded?.()
        req.onsuccess?.()
      })
      return req
    },
  }
}

/** Die Platte überlebt, der Arbeitsspeicher nicht — genau ein Neustart. */
const platte = new Map<string, any>()

const { clearGeraeteMemory, eigenesGeraet, geraeteVon, geraetVeroeffentlichen, vergessenGeraete, verlangeGeraeteVon, E2eeKeinGeraetError } =
  await import('./e2eeGeraet')

describe('e2eeGeraet', () => {
  beforeEach(() => {
    installiereIdb(platte)
    clearGeraeteMemory()
    hochgeladen.length = 0
    fremdeGeraete.liste = []
    fremdeGeraete.rufe = 0
  })

  it('legt beim ersten Mal Kennung und Paar an', async () => {
    platte.clear()
    const geraet = await eigenesGeraet()

    // 16 Zufallsbytes hex, bedeutungsfrei: die Kennung steht im Klartext in
    // jedem Umschlag.
    expect(geraet.kennung).toMatch(/^[0-9a-f]{32}$/)
    expect(geraet.paar.publicKeyJwk).toContain('"kty":"RSA"')
    expect(geraet.paar.privateKeyJwk).toContain('"d":')
  }, 60_000)

  it('behält Kennung und Paar über einen Neustart', async () => {
    platte.clear()
    const vorher = await eigenesGeraet()

    // Neustart: der Arbeitsspeicher ist weg, die IndexedDB bleibt.
    clearGeraeteMemory()
    const nachher = await eigenesGeraet()

    expect(nachher.kennung).toBe(vorher.kennung)
    expect(nachher.paar.privateKeyJwk).toBe(vorher.paar.privateKeyJwk)
    expect(nachher.paar.publicKeyJwk).toBe(vorher.paar.publicKeyJwk)
  }, 60_000)

  it('erzeugt auch bei gleichzeitigen Aufrufen nur ein Paar', async () => {
    // Ohne die Klammer um den Aufbau erzeugten zwei parallele Aufrufe zwei
    // Paare, und das zweite überschriebe das erste — mitsamt allen Sitzungen,
    // die schon gegen das erste laufen.
    platte.clear()
    const [a, b, c] = await Promise.all([eigenesGeraet(), eigenesGeraet(), eigenesGeraet()])

    expect(a.kennung).toBe(b.kennung)
    expect(b.kennung).toBe(c.kennung)
    expect(a.paar.privateKeyJwk).toBe(c.paar.privateKeyJwk)
  }, 60_000)

  it('lädt nur den öffentlichen Teil hoch', async () => {
    // Der private Teil verlässt das Gerät nie. Es gibt keinen Mechanismus mehr,
    // der ihn irgendwohin verpacken könnte — und dieser Test hält fest, dass
    // auch keiner zurückkommt.
    platte.clear()
    const geraet = await geraetVeroeffentlichen('Arbeitsrechner')

    expect(hochgeladen).toHaveLength(1)
    expect(hochgeladen[0].publicKey).toBe(geraet.paar.publicKeyJwk)

    // Gesucht wird nach dem **Wert** des privaten Exponenten, nicht nach dem
    // Feldnamen: der Rumpf wird beim Senden noch einmal serialisiert, und in
    // `\"d\":` fände sich `"d":` nicht wieder. Eine Prüfung, die nur den Namen
    // sucht, ginge also auch dann durch, wenn der Schlüssel mitreiste.
    const privat = JSON.parse(geraet.paar.privateKeyJwk)
    expect(privat.d).toBeTruthy()
    const nutzlast = JSON.stringify(hochgeladen)
    expect(nutzlast).not.toContain(privat.d)
    for (const teil of [privat.p, privat.q, privat.dp, privat.dq, privat.qi]) {
      if (teil) expect(nutzlast).not.toContain(teil)
    }
  }, 60_000)

  it('meldet sich nicht zweimal in derselben Sitzung', async () => {
    platte.clear()
    await geraetVeroeffentlichen()
    await geraetVeroeffentlichen()
    expect(hochgeladen).toHaveLength(1)
  }, 60_000)

  describe('Gegenstellen', () => {
    it('merkt sich die Geräteliste und vergisst sie auf Zuruf', async () => {
      fremdeGeraete.liste = [{ device_id: 'fremd-a', public_key: 'pub-a' }]

      expect(await geraeteVon(99)).toHaveLength(1)
      expect(await geraeteVon(99)).toHaveLength(1)
      expect(fremdeGeraete.rufe).toBe(1)

      // Ohne das Vergessen verschlüsselte ein lange offener Tab weiter gegen
      // ein Gerät, das es nicht mehr gibt.
      vergessenGeraete(99)
      await geraeteVon(99)
      expect(fremdeGeraete.rufe).toBe(2)
    })

    it('sendet lieber nicht, als gegen niemanden zu verschlüsseln', async () => {
      // Früher stand hier ein symmetrischer Notweg, dessen Schlüssel sich
      // allein aus den Benutzerkennungen ergab — für den Server also nachbaubar.
      fremdeGeraete.liste = []
      await expect(verlangeGeraeteVon(99)).rejects.toThrow(E2eeKeinGeraetError)
    })
  })
})
