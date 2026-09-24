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

import { generateAesGcmKey } from '@msdis/shield/aead'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setzeInhaltsSchluessel, setzeSiegelAktiv } from './lokaleVersiegelung'

/** Ein localStorage im Arbeitsspeicher — der Siegelschalter liegt dort. */
function installiereLocalStorage() {
  const daten = new Map<string, string>()
  ;(globalThis as any).localStorage = {
    getItem: (k: string) => daten.get(k) ?? null,
    setItem: (k: string, v: string) => daten.set(k, v),
    removeItem: (k: string) => daten.delete(k),
    clear: () => daten.clear(),
  }
}

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
    /**
     * `oncomplete` kommt per `setTimeout`, die Anfragen per `queueMicrotask`.
     * Damit gilt hier dieselbe Reihenfolge wie in einer echten IndexedDB: erst
     * laufen alle Anfragen samt der Anfragen, die deren Rückrufe noch stellen,
     * dann schließt die Transaktion.
     *
     * Ohne das Ereignis hing `uebernimmAltbestand`: es wartet darauf, dass der
     * Anspruch auf den Altbestand wirklich geschrieben ist, und nicht nur
     * darauf, dass ein `put` angenommen wurde.
     */
    transaction: (name: string) => {
      const tx: any = { oncomplete: null, onerror: null, onabort: null }
      tx.objectStore = () => store(name)
      setTimeout(() => tx.oncomplete?.(), 0)
      return tx
    },
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

const {
  clearGeraeteMemory,
  eigenesGeraet,
  geraeteVon,
  geraetVeroeffentlichen,
  vergessenGeraete,
  verlangeGeraeteVon,
  E2eeKeinGeraetError,
  freigabeDaten,
  onSchluesselWarnung,
  vertrauteGeraete,
  pruefeGeraeteBeleg,
  sicherheitsnummer,
  verzeichnisVon,
} = await import('./e2eeGeraet')
const { erzeugeSignaturPaar, signiere } = await import('./absenderSignatur')

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

  describe('bei gesperrtem Messenger', () => {
    afterEach(() => {
      setzeSiegelAktiv(false)
      setzeInhaltsSchluessel(null)
    })

    it('erzeugt keinen frischen Ausweis, sondern weigert sich', async () => {
      // Der gefährlichste Fall dieser Datei. Gesperrt gibt die Ablage nichts
      // heraus — und ein `lies`, das nichts findet, heisst hier sonst „dieses
      // Gerät hat noch keine Identität". Ohne Schranke liefe der Weg auf den
      // Neuanlage-Zweig zu und überschriebe die bestehende Identität mitsamt
      // allen Sitzungen.
      installiereLocalStorage()
      setzeAngemeldetesKonto(10)
      setzeInhaltsSchluessel(await generateAesGcmKey())
      setzeSiegelAktiv(true)

      platte.clear()
      const echtes = await eigenesGeraet()
      const zeilenVorher = new Map(platte)

      clearGeraeteMemory()
      setzeInhaltsSchluessel(null)

      await expect(eigenesGeraet()).rejects.toThrow(/gesperrt/)
      expect([...platte.entries()]).toEqual([...zeilenVorher.entries()])

      // Nach dem Entsperren steht dieselbe Identität wieder da, nicht eine neue.
      setzeInhaltsSchluessel(null)
      expect(echtes.kennung).toMatch(/^[0-9a-f]{32}$/)
    })
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

  describe('Vertraute Geräte: der Server hat nicht das letzte Wort', () => {
    const geraet = (id: string, pk: string, sig = '', extra: Record<string, unknown> = {}) => ({
      device_id: id,
      public_key: pk,
      signing_public_key: sig,
      label: '',
      ...extra,
    })

    const mitWarnungen = async (lauf: (w: any[]) => Promise<void>) => {
      const warnungen: any[] = []
      const abbestellen = onSchluesselWarnung((w) => warnungen.push(w))
      try {
        await lauf(warnungen)
      } finally {
        abbestellen()
      }
    }

    it('glaubt beim Erstkontakt allem und warnt nicht', async () => {
      await mitWarnungen(async (warnungen) => {
        const liste = [geraet('a-1', 'pk-a')]
        expect(await vertrauteGeraete(4001, liste)).toEqual(liste)
        expect(warnungen).toEqual([])
      })
    })

    it('gibt einem Gerät, das der Server einträgt, nichts', async () => {
      // Genau der Fall, gegen den `is_approved` nicht hilft: der Server trägt
      // selbst ein Gerät ein und meldet es als freigegeben.
      await mitWarnungen(async (warnungen) => {
        await vertrauteGeraete(4002, [geraet('echt-1', 'pk-echt')])
        const untergeschoben = geraet('fremd-1', 'pk-fremd', '', { is_approved: true })
        const ergebnis = await vertrauteGeraete(4002, [geraet('echt-1', 'pk-echt'), untergeschoben])
        expect(ergebnis.map((g) => g.device_id)).toEqual(['echt-1'])
        expect(warnungen).toEqual([{ userId: 4002, typ: 'unbestaetigt', geraete: ['fremd-1'] }])
      })
    })

    it('gibt einem bekannten Gerät mit ausgetauschtem Schlüssel nichts', async () => {
      await mitWarnungen(async (warnungen) => {
        await vertrauteGeraete(4003, [geraet('tel', 'pk-tel'), geraet('lap', 'pk-lap')])
        const ergebnis = await vertrauteGeraete(4003, [geraet('tel', 'pk-tel'), geraet('lap', 'pk-dieb')])
        expect(ergebnis.map((g) => g.device_id)).toEqual(['tel'])
        expect(warnungen[0]).toMatchObject({ typ: 'unbestaetigt', geraete: ['lap'] })
      })
    })

    it('nimmt ein Gerät auf, das ein bekanntes über genau diese Schlüssel freigegeben hat', async () => {
      const tel = await erzeugeSignaturPaar()
      const neu = await erzeugeSignaturPaar()
      await vertrauteGeraete(4004, [geraet('tel', 'pk-tel', tel.publicKeyJwk)])
      const sig = await signiere(
        await freigabeDaten(4004, 'neu', 'pk-neu', neu.publicKeyJwk),
        tel.privateKeyJwk,
      )
      await mitWarnungen(async (warnungen) => {
        const ergebnis = await vertrauteGeraete(4004, [
          geraet('tel', 'pk-tel', tel.publicKeyJwk),
          geraet('neu', 'pk-neu', neu.publicKeyJwk, { approved_by: 'tel', approval_signature: sig }),
        ])
        expect(ergebnis.map((g) => g.device_id)).toEqual(['tel', 'neu'])
        expect(warnungen).toEqual([{ userId: 4004, typ: 'neues_geraet', geraete: ['neu'] }])
      })
      // Dieselbe Unterschrift deckt keinen anderen Schlüssel unter derselben Kennung.
      const ergebnis = await vertrauteGeraete(4004, [
        geraet('tel', 'pk-tel', tel.publicKeyJwk),
        geraet('neu', 'pk-anders', neu.publicKeyJwk, { approved_by: 'tel', approval_signature: sig }),
      ])
      expect(ergebnis.map((g) => g.device_id)).toEqual(['tel'])
    })

    it('glaubt einer Freigabe durch ein Gerät, das inzwischen entfernt ist', async () => {
      // Altes Telefon gibt das neue frei und wird danach entfernt. Wer in der
      // Zwischenzeit nicht nachgesehen hat, muss das neue trotzdem annehmen.
      const alt = await erzeugeSignaturPaar()
      const neu = await erzeugeSignaturPaar()
      await vertrauteGeraete(4005, [geraet('alt', 'pk-alt', alt.publicKeyJwk), geraet('pc', 'pk-pc')])
      const sig = await signiere(
        await freigabeDaten(4005, 'neu', 'pk-neu', neu.publicKeyJwk),
        alt.privateKeyJwk,
      )
      const ergebnis = await vertrauteGeraete(4005, [
        geraet('pc', 'pk-pc'),
        geraet('neu', 'pk-neu', neu.publicKeyJwk, { approved_by: 'alt', approval_signature: sig }),
      ])
      expect(ergebnis.map((g) => g.device_id)).toEqual(['pc', 'neu'])
    })

    it('beginnt mit Warnung neu, wenn keines der bekannten Geräte mehr da ist', async () => {
      await mitWarnungen(async (warnungen) => {
        await vertrauteGeraete(4006, [geraet('alt-1', 'pk-1'), geraet('alt-2', 'pk-2')])
        const neu = [geraet('frisch-1', 'pk-f')]
        expect(await vertrauteGeraete(4006, neu)).toEqual(neu)
        expect(warnungen).toEqual([{ userId: 4006, typ: 'konto_neustart', geraete: ['frisch-1'] }])
        // Ab jetzt ist der neue Bestand der bekannte.
        expect(await vertrauteGeraete(4006, neu)).toEqual(neu)
        expect(warnungen).toHaveLength(1)
      })
    })

    it('liefert aus dem Sendeweg nur die geprüften Geräte', async () => {
      fremdeGeraete.liste = [geraet('echt-1', 'pk-echt')] as any
      await geraeteVon(4007)
      vergessenGeraete(4007)
      fremdeGeraete.liste = [geraet('echt-1', 'pk-echt'), geraet('fremd-1', 'pk-fremd')] as any
      expect((await verlangeGeraeteVon(4007)).map((g) => g.device_id)).toEqual(['echt-1'])
    })
  })

  describe('Belege über Schlüsselumschläge', () => {
    const DATEN = 'msm:test:ein-schluessel-fuer-geraet-b'

    async function verzeichnisMit(
      ...eintraege: {
        device_id: string
        signing_public_key: string
        approved_by?: string
        approval_signature?: string
      }[]
    ) {
      fremdeGeraete.liste = eintraege.map((e) => ({ ...e, public_key: `pub-${e.device_id}` })) as any
    }

    it('erkennt die Unterschrift des Geräts, das der Umschlag nennt', async () => {
      const paar = await erzeugeSignaturPaar()
      await verzeichnisMit({ device_id: 'geraet-a', signing_public_key: paar.publicKeyJwk })

      const sig = await signiere(DATEN, paar.privateKeyJwk)
      expect(await pruefeGeraeteBeleg(50, 'geraet-a', DATEN, sig)).toBe('echt')
    })

    it('weist eine Unterschrift ab, die zu einem anderen Gerät gehört', async () => {
      // Der Fall, gegen den das Ganze steht: ein Kontakt unterschreibt mit
      // seinem eigenen Schlüssel und nennt das Gerät eines anderen.
      const echt = await erzeugeSignaturPaar()
      const fremd = await erzeugeSignaturPaar()
      await verzeichnisMit({ device_id: 'geraet-a', signing_public_key: echt.publicKeyJwk })

      const sig = await signiere(DATEN, fremd.privateKeyJwk)
      expect(await pruefeGeraeteBeleg(51, 'geraet-a', DATEN, sig)).toBe('falsch')
      // Und derselbe Beleg über andere Daten ebenso.
      const echteSig = await signiere(DATEN, echt.privateKeyJwk)
      expect(await pruefeGeraeteBeleg(51, 'geraet-a', `${DATEN}-verbogen`, echteSig)).toBe('falsch')
    })

    it('lässt die Unterschrift nicht einfach weglassen', async () => {
      // Die Downgrade-Schranke: wer unterschreiben kann, muss es.
      const paar = await erzeugeSignaturPaar()
      await verzeichnisMit({ device_id: 'geraet-a', signing_public_key: paar.publicKeyJwk })
      expect(await pruefeGeraeteBeleg(52, 'geraet-a', DATEN, undefined)).toBe('falsch')
      expect(await pruefeGeraeteBeleg(52, 'geraet-a', DATEN, '')).toBe('falsch')
    })

    it('nimmt Altbestand, solange das Konto nirgends unterschreibt', async () => {
      await verzeichnisMit({ device_id: 'geraet-a', signing_public_key: '' })
      expect(await pruefeGeraeteBeleg(53, 'geraet-a', DATEN, undefined)).toBe('altbestand')
    })

    it('hält ein unbekanntes Gerät für unbekannt, nicht für widerlegt', async () => {
      await verzeichnisMit({ device_id: 'geraet-a', signing_public_key: '' })
      const paar = await erzeugeSignaturPaar()
      const sig = await signiere(DATEN, paar.privateKeyJwk)
      expect(await pruefeGeraeteBeleg(54, 'geraet-x', DATEN, sig)).toBe('unbekannt')
      // Auch ein Gerät, dessen Signaturschlüssel das Verzeichnis noch nicht
      // führt, ist nicht widerlegt — es hat sich womöglich gerade erst gemeldet.
      expect(await pruefeGeraeteBeleg(54, 'geraet-a', DATEN, sig)).toBe('unbekannt')
    })

    it('sieht vor einem Nein frisch nach, aber höchstens alle halbe Minute', async () => {
      const paar = await erzeugeSignaturPaar()
      await verzeichnisMit({ device_id: 'geraet-a', signing_public_key: paar.publicKeyJwk })
      const sig = await signiere(DATEN, paar.privateKeyJwk)

      // Die zehn Minuten alte Liste kennt das neue Gerät noch nicht.
      await verlangeGeraeteVon(55)
      const echtesJetzt = Date.now
      try {
        Date.now = () => echtesJetzt() + 60_000
        // Das neue Gerät kommt mit der Freigabe durch das bekannte — ohne sie
        // bekäme es nichts (`vertrauteGeraete`).
        const freigabe = await signiere(
          await freigabeDaten(55, 'geraet-neu', 'pub-geraet-neu', paar.publicKeyJwk),
          paar.privateKeyJwk,
        )
        await verzeichnisMit(
          { device_id: 'geraet-a', signing_public_key: paar.publicKeyJwk },
          {
            device_id: 'geraet-neu',
            signing_public_key: paar.publicKeyJwk,
            approved_by: 'geraet-a',
            approval_signature: freigabe,
          },
        )
        const vorher = fremdeGeraete.rufe
        expect(await pruefeGeraeteBeleg(55, 'geraet-neu', DATEN, sig)).toBe('echt')
        expect(fremdeGeraete.rufe).toBe(vorher + 1)

        // Wer mit erfundenen Geräten um sich wirft, bekommt keinen Abruf je
        // Umschlag: die Liste ist jetzt frisch genug.
        expect(await pruefeGeraeteBeleg(55, 'erfunden-1', DATEN, sig)).toBe('unbekannt')
        expect(await pruefeGeraeteBeleg(55, 'erfunden-2', DATEN, sig)).toBe('unbekannt')
        expect(fremdeGeraete.rufe).toBe(vorher + 1)
      } finally {
        Date.now = echtesJetzt
      }
    })

    it('glaubt die Nachsicht für Altbestand keiner zehn Minuten alten Liste', async () => {
      // Durchsicht vom 23.09.: `altbestand` kam ohne frischen Blick aus dem
      // Zwischenspeicher. Hatte das Konto eben seinen ersten Signaturschlüssel
      // bekommen, ging bis zu zehn Minuten lang jede unsignierte Übergabe
      // durch — die Downgrade-Schranke hatte ein Fenster.
      await verzeichnisMit({ device_id: 'geraet-a', signing_public_key: '' })
      await verlangeGeraeteVon(57)
      const paar = await erzeugeSignaturPaar()
      const echtesJetzt = Date.now
      try {
        Date.now = () => echtesJetzt() + 60_000
        await verzeichnisMit({ device_id: 'geraet-a', signing_public_key: paar.publicKeyJwk })
        expect(await pruefeGeraeteBeleg(57, 'geraet-a', DATEN, undefined)).toBe('falsch')
      } finally {
        Date.now = echtesJetzt
      }
    })

    it('sagt „offen", wenn das Verzeichnis nicht antwortet', async () => {
      // Kein Nein: ein Netzfehler darf keinen echten Aufbau für immer abweisen.
      fremdeGeraete.fehler = new Error('Netzwerk weg')
      try {
        expect(await pruefeGeraeteBeleg(56, 'geraet-a', DATEN, 'c2lnbmF0dXI=')).toBe('offen')
        // Und `verzeichnisVon` wirft, statt „niemand da" zu sagen.
        await expect(verzeichnisVon(56)).rejects.toThrow('Netzwerk weg')
      } finally {
        fremdeGeraete.fehler = null
      }
    })
  })

  describe('Sicherheitsnummer', () => {
    it('sind zwanzig Ziffern in vier Gruppen, und für jeden Schlüssel andere', async () => {
      const a = await sicherheitsnummer('{"kty":"RSA","n":"AAAA","e":"AQAB"}')
      const b = await sicherheitsnummer('{"kty":"RSA","n":"AAAB","e":"AQAB"}')
      expect(a).toMatch(/^\d{5} \d{5} \d{5} \d{5}$/)
      expect(b).not.toBe(a)
    })

    it('hängt nicht an der Reihenfolge der Felder', async () => {
      // Panel und App rechnen dieselbe Nummer aus zwei verschiedenen
      // Zeichenketten: die App aus ihrem eigenen Schlüssel, das Panel aus dem,
      // was der Server zurückgibt.
      expect(await sicherheitsnummer('{"e":"AQAB","n":"xyz","kty":"RSA","ext":true}')).toBe(
        await sicherheitsnummer('{"kty":"RSA","n":"xyz","e":"AQAB"}'),
      )
    })
  })
})
