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

const { hochgeladen, fremdeGeraete, uebernommen, umbenennen } = vi.hoisted(() => ({
  hochgeladen: [] as { deviceId: string; publicKey: string; label: string }[],
  fremdeGeraete: {
    liste: [] as { device_id: string; public_key: string }[],
    rufe: 0,
    fehler: null as Error | null,
  },
  uebernommen: [] as string[],
  umbenennen: { fehler: null as Error | null },
}))

vi.mock('./ratchetSpeicher', () => ({
  uebernehmeAltbestand: vi.fn(async (kennung: string) => {
    uebernommen.push(kennung)
    if (umbenennen.fehler) throw umbenennen.fehler
    return 0
  }),
}))

vi.mock('@/api/social', () => ({
  putEigenesGeraet: vi.fn(async (req: { deviceId: string; publicKey: string; label: string }) => {
    hochgeladen.push(req)
    return { device_id: req.deviceId, public_key: req.publicKey, label: req.label }
  }),
  getE2eeGeraete: vi.fn(async () => {
    fremdeGeraete.rufe += 1
    if (fremdeGeraete.fehler) throw fremdeGeraete.fehler
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
    delete: (schluessel: string) => {
      platte.delete(`${name}:${schluessel}`)
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

const { setzeAngemeldetesKonto } = await import('@/lib/angemeldetesKonto')

const { clearGeraeteMemory, eigenesGeraet, geraeteVon, geraetVeroeffentlichen, vergessenGeraete, verlangeGeraeteVon, E2eeKeinGeraetError } =
  await import('./e2eeGeraet')

describe('e2eeGeraet', () => {
  beforeEach(() => {
    installiereIdb(platte)
    clearGeraeteMemory()
    hochgeladen.length = 0
    fremdeGeraete.liste = []
    fremdeGeraete.rufe = 0
    setzeAngemeldetesKonto(10)
    uebernommen.length = 0
    umbenennen.fehler = null
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

  describe('Ein Schlüssel je Konto', () => {
    it('gibt zwei Konten in einem Browser verschiedene Schlüssel', async () => {
      // Bis 09/2026 lag hier ein Paar unter dem festen Namen `self`. Wer sich
      // nach jemand anderem in denselben Browser einloggte, bekam dessen
      // privaten Schlüssel — und der öffnet, weil die blinde Mailbox jedem
      // Angemeldeten offensteht, die Post beider Konten.
      platte.clear()
      const anna = await eigenesGeraet()

      clearGeraeteMemory()
      setzeAngemeldetesKonto(11)
      const bert = await eigenesGeraet()

      expect(bert.kennung).not.toBe(anna.kennung)
      expect(bert.paar.privateKeyJwk).not.toBe(anna.paar.privateKeyJwk)
      expect(bert.paar.publicKeyJwk).not.toBe(anna.paar.publicKeyJwk)

      // Und beide finden beim nächsten Start ihr eigenes wieder.
      clearGeraeteMemory()
      setzeAngemeldetesKonto(10)
      expect((await eigenesGeraet()).kennung).toBe(anna.kennung)
    }, 60_000)

    it('hängt den alten self-Eintrag an das erste Konto und räumt ihn weg', async () => {
      // Ohne diese Übernahme verlöre jede bestehende Installation beim Update
      // ihren Schlüssel und damit jede laufende Sitzung.
      platte.clear()
      platte.set('devices:self', {
        id: 'self',
        kennung: 'altbestand0000',
        publicKeyJwk: '{"kty":"RSA","n":"alt"}',
        privateKeyJwk: '{"kty":"RSA","d":"alt"}',
      })

      const uebernahme = await eigenesGeraet()
      expect(uebernahme.kennung).toBe('altbestand0000')
      expect(platte.has('devices:self')).toBe(false)
      expect(platte.get('devices:konto:10')?.kennung).toBe('altbestand0000')

      // Die Ratchet-Sitzungen hängen an diesem Schlüssel und tragen seit
      // 09/2026 die eigene Gerätekennung im Namen. Bleiben sie unter dem alten
      // Namen liegen, meldet der Messenger jedem Gesprächspartner einen Bruch.
      expect(uebernommen).toEqual(['altbestand0000'])

      // Das zweite Konto erbt nichts, sondern legt frisch an.
      clearGeraeteMemory()
      setzeAngemeldetesKonto(11)
      const zweites = await eigenesGeraet()
      expect(zweites.kennung).not.toBe('altbestand0000')
    }, 60_000)

    it('versucht das Umbenennen der Sitzungen erneut, wenn es scheitert', async () => {
      // Am laufenden System schiefgegangen: der Schlüssel war umgehängt, das
      // Umbenennen scheiterte, `self` war weg — und es gab keinen zweiten
      // Versuch mehr. Der Browser stand mit einem gültigen Schlüssel und lauter
      // unauffindbaren Sitzungen da.
      platte.clear()
      platte.set('devices:self', {
        id: 'self',
        kennung: 'altbestand0000',
        publicKeyJwk: '{"kty":"RSA","n":"alt"}',
        privateKeyJwk: '{"kty":"RSA","d":"alt"}',
      })
      umbenennen.fehler = new Error('Ablage weg')

      await eigenesGeraet()
      expect(uebernommen).toEqual(['altbestand0000'])
      expect(platte.get('devices:konto:10')?.sitzungenUebernommen).toBe(false)

      // Nächster Start, diesmal geht es durch.
      umbenennen.fehler = null
      clearGeraeteMemory()
      await eigenesGeraet()
      expect(uebernommen).toEqual(['altbestand0000', 'altbestand0000'])
      expect(platte.get('devices:konto:10')?.sitzungenUebernommen).toBe(true)

      // Und danach nicht noch einmal.
      clearGeraeteMemory()
      await eigenesGeraet()
      expect(uebernommen).toHaveLength(2)
    }, 60_000)

    it('lässt ein frisch erzeugtes Gerät die Sitzungen nicht anfassen', async () => {
      // Sonst zöge das **zweite** Konto in diesem Browser die verwaisten
      // Sitzungen des ersten an sich.
      platte.clear()
      await eigenesGeraet()
      expect(uebernommen).toEqual([])
      expect(platte.get('devices:konto:10')?.sitzungenUebernommen).toBe(true)
    }, 60_000)

    it('verweigert die Auskunft ohne angemeldetes Konto', async () => {
      // Ein stiller Rückfall auf einen kontoübergreifenden Schlüssel wäre genau
      // der Zustand, der hier behoben wird.
      platte.clear()
      setzeAngemeldetesKonto(null)
      await expect(eigenesGeraet()).rejects.toThrow(/Kein angemeldetes Konto/)
    })
  })

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

    it('hält einen misslungenen Abruf nicht für „niemand angemeldet"', async () => {
      // Der Sendepfad baut auf dieser Antwort einen Satz über die Gegenstelle:
      // „ist mit keinem Gerät angemeldet". Ohne Antwort weiss das niemand. Bis
      // 09/2026 lieferte der Abruf bei jedem Fehler eine leere Liste, und der
      // Benutzer las eine geprüfte Aussage, die nie geprüft worden war.
      fremdeGeraete.liste = []
      fremdeGeraete.fehler = new Error('Netzwerk weg')
      try {
        const geworfen = await verlangeGeraeteVon(1234).catch((e: unknown) => e)
        expect(geworfen).toBeInstanceOf(Error)
        expect(geworfen).not.toBeInstanceOf(E2eeKeinGeraetError)

        // Wer nur anzeigt, kommt weiterhin mit einer leeren Liste zurecht.
        expect(await geraeteVon(1234)).toEqual([])
      } finally {
        fremdeGeraete.fehler = null
      }
    })

    it('fällt bei einem Ausfall auf den abgelaufenen Eintrag zurück', async () => {
      // Die Geräteliste ändert sich selten, der Abruf scheitert oft nur kurz.
      // Ein veralteter Eintrag trägt das Gespräch weiter; gar keiner bricht es ab.
      fremdeGeraete.liste = [{ device_id: 'fremd-b', public_key: 'pub-b' }]
      expect(await verlangeGeraeteVon(4321)).toHaveLength(1)

      const echtesJetzt = Date.now
      Date.now = () => echtesJetzt() + 3_600_000
      fremdeGeraete.fehler = new Error('Netzwerk weg')
      try {
        const geraete = await verlangeGeraeteVon(4321)
        expect(geraete.map((g) => g.device_id)).toEqual(['fremd-b'])
      } finally {
        fremdeGeraete.fehler = null
        Date.now = echtesJetzt
      }
    })
  })
})
