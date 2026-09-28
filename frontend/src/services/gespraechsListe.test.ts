// @vitest-environment node
/**
 * Mit wem man schreibt — und wo das liegen darf.
 *
 * Seit Stufe 6b nennt `direct_chats` keine Menschen mehr, und
 * `GET /social/direct-chats` ist entfernt. Die Liste führt dieses Gerät. Diese
 * Datei hält fest, dass sie dabei nicht im Klartext auf der Platte landet —
 * sonst wäre die Metadatenzeile nur umgezogen, nicht verschwunden.
 *
 * Node-Umgebung: gebraucht wird WebCrypto und ein `localStorage`, kein DOM.
 */

import { generateAesGcmKey } from '@msdis/shield/aead'
import { beforeEach, describe, expect, it, vi } from 'vitest'

/** Ein `localStorage` im Arbeitsspeicher. Die Ablage dieser Datei liegt dort. */
const platte = new Map<string, string>()
;(globalThis as any).localStorage = {
  get length() {
    return platte.size
  },
  key: (i: number) => [...platte.keys()][i] ?? null,
  getItem: (k: string) => platte.get(k) ?? null,
  setItem: (k: string, v: string) => void platte.set(k, v),
  removeItem: (k: string) => void platte.delete(k),
  clear: () => platte.clear(),
}

/** Siehe `frischImportieren` in `gruppenName.test.ts` — dieselbe Begründung. */
async function frischImportieren(schluessel: CryptoKey | null = null) {
  vi.resetModules()
  const konto = await import('@/lib/angemeldetesKonto')
  konto.setzeAngemeldetesKonto(10)
  const siegel = await import('./lokaleVersiegelung')
  siegel.setzeInhaltsSchluessel(schluessel)
  const modul = await import('./gespraechsListe')
  return { ...modul, ...siegel }
}

async function mitSiegel() {
  const schluessel = await generateAesGcmKey()
  const modul = await frischImportieren(schluessel)
  modul.setzeSiegelAktiv(true)
  return { ...modul, schluessel }
}

const MAILBOX = 'a'.repeat(64)

describe('Die örtliche Gesprächsliste', () => {
  beforeEach(() => {
    platte.clear()
  })

  it('merkt sich ein Gespräch und gibt es in der Serverform zurück', async () => {
    const { gespraechsListe, merkeGespraech } = await frischImportieren()

    await merkeGespraech(42, {
      username: 'Nachbarin',
      avatarUrl: '/bild.png',
      blindMailboxId: MAILBOX,
    })

    const liste = await gespraechsListe()
    expect(liste).toHaveLength(1)
    expect(liste[0].other_user_id).toBe(42)
    expect(liste[0].other_username).toBe('Nachbarin')
    expect(liste[0].blind_mailbox_id).toBe(MAILBOX)
  })

  it('schreibt den Namen nicht im Klartext auf die Platte', async () => {
    // Die eigentliche Zusage. Ein Gesprächspartner in einem offenen
    // localStorage-Eintrag wäre genau die Zeile, die Stufe 6b aus der
    // Datenbank entfernt hat, nur auf einer anderen Platte.
    const { merkeGespraech } = await mitSiegel()

    await merkeGespraech(42, { username: 'Suchtberatung Nord' })

    const roh = platte.get('msm:gespraeche')
    expect(roh).toBeTruthy()
    expect(roh).not.toContain('Suchtberatung')
    expect(roh).not.toContain('Nord')
  })

  it('überlebt einen Neustart', async () => {
    const erst = await mitSiegel()
    await erst.merkeGespraech(42, { username: 'Nachbarin', blindMailboxId: MAILBOX })

    const zweit = await frischImportieren(erst.schluessel)
    const liste = await zweit.gespraechsListe()
    expect(liste.map((g) => g.other_username)).toEqual(['Nachbarin'])
  })

  it('gibt bei verschlossenem Messenger nichts heraus — und danach wieder alles', async () => {
    const erst = await mitSiegel()
    await erst.merkeGespraech(42, { username: 'Nachbarin' })

    const zweit = await frischImportieren(null)
    expect(await zweit.gespraechsListe()).toEqual([])

    zweit.setzeInhaltsSchluessel(erst.schluessel)
    expect((await zweit.gespraechsListe()).map((g) => g.other_username)).toEqual(['Nachbarin'])
  })

  it('öffnet eine Zeile nicht unter einer fremden Kennung', async () => {
    // Die Bindung ist die AAD `msm-gespraech:<id>`. Ohne sie liesse sich der
    // Name eines Menschen unter der Kennung eines anderen unterschieben — und
    // man schriebe an den Falschen.
    const erst = await mitSiegel()
    await erst.merkeGespraech(42, { username: 'Nachbarin' })

    const vertauscht = JSON.parse(platte.get('msm:gespraeche')!)
    platte.set('msm:gespraeche', JSON.stringify({ '43': vertauscht['42'] }))

    const zweit = await frischImportieren(erst.schluessel)
    expect(await zweit.gespraechsListe()).toEqual([])
  })

  it('ordnet die jüngste Unterhaltung nach oben', async () => {
    const { gespraechsListe, merkeGespraech } = await frischImportieren()
    vi.useFakeTimers()
    try {
      vi.setSystemTime(new Date('2026-09-01T10:00:00Z'))
      await merkeGespraech(1, { username: 'Alt' })
      vi.setSystemTime(new Date('2026-09-23T10:00:00Z'))
      await merkeGespraech(2, { username: 'Neu' })
    } finally {
      vi.useRealTimers()
    }

    expect((await gespraechsListe()).map((g) => g.other_username)).toEqual(['Neu', 'Alt'])
  })

  describe('Das namenlose Gespräch', () => {
    /*
     * Ein zugestelltes Chatgeheimnis bringt eine Konto-Id und keinen Namen —
     * ein Anzeigename im Steuerumschlag wäre ein Feld, das der Absender frei
     * wählt, und damit der Weg, sich in einer fremden Kontaktliste als jemand
     * anderes auszugeben.
     *
     * Die Zeile muss trotzdem zählen: über sie wird die Mailbox abonniert.
     * Angezeigt werden darf sie nicht.
     */

    it('zählt fürs Abo, erscheint aber nicht in der Liste', async () => {
      const { gespraechsListe, gespraechsPartner, merkeGespraech } = await frischImportieren()

      await merkeGespraech(42, {})

      expect(await gespraechsPartner()).toEqual([42])
      expect(await gespraechsListe()).toEqual([])
    })

    it('bekommt seinen Namen aus der Kontaktliste', async () => {
      const { fuelleNamenNach, gespraechsListe, merkeGespraech } = await frischImportieren()
      await merkeGespraech(42, {})

      const geaendert = await fuelleNamenNach([
        { userId: 42, username: 'Nachbarin', avatarUrl: '/bild.png' },
      ])

      expect(geaendert).toBe(true)
      const liste = await gespraechsListe()
      expect(liste.map((g) => g.other_username)).toEqual(['Nachbarin'])
      expect(liste[0].other_avatar_url).toBe('/bild.png')
    })

    it('überschreibt einen vorhandenen Namen nicht', async () => {
      // Sonst wäre die Kontaktliste des Servers das letzte Wort über einen
      // Namen, den dieses Gerät beim Öffnen des Gesprächs selbst gesehen hat.
      const { fuelleNamenNach, gespraechsListe, merkeGespraech } = await frischImportieren()
      await merkeGespraech(42, { username: 'So heisst sie' })

      const geaendert = await fuelleNamenNach([{ userId: 42, username: 'Anders' }])

      expect(geaendert).toBe(false)
      expect((await gespraechsListe())[0].other_username).toBe('So heisst sie')
    })

    it('erfindet kein Gespräch, das es nicht gibt', async () => {
      const { gespraechsPartner, fuelleNamenNach } = await frischImportieren()

      expect(await fuelleNamenNach([{ userId: 99, username: 'Fremd' }])).toBe(false)
      expect(await gespraechsPartner()).toEqual([])
    })
  })

  it('vergisst ein Gespräch und beim Abmelden alle', async () => {
    const modul = await mitSiegel()
    await modul.merkeGespraech(42, { username: 'Nachbarin' })
    await modul.merkeGespraech(43, { username: 'Kollege' })

    await modul.vergissGespraech(42)
    expect((await modul.gespraechsListe()).map((g) => g.other_user_id)).toEqual([43])
    expect(Object.keys(JSON.parse(platte.get('msm:gespraeche')!))).toEqual(['43'])

    modul.leereGespraeche()
    expect(platte.get('msm:gespraeche')).toBeUndefined()
    expect(await modul.gespraechsListe()).toEqual([])
  })

  it('wirft die ältesten weg, wenn der Deckel erreicht ist', async () => {
    // Eine Ablage, die nie kleiner wird, sprengt irgendwann `localStorage` —
    // und dann fällt sie ganz aus statt nur zu schrumpfen.
    const { gespraechsPartner, merkeGespraech } = await frischImportieren()
    vi.useFakeTimers()
    try {
      for (let i = 1; i <= 505; i++) {
        vi.setSystemTime(new Date(1_700_000_000_000 + i * 1000))
        await merkeGespraech(i, { username: `Nummer ${i}` })
      }
    } finally {
      vi.useRealTimers()
    }

    const partner = await gespraechsPartner()
    expect(partner).toHaveLength(500)
    // Die fünf ältesten sind gefallen, die jüngsten stehen.
    expect(partner).not.toContain(1)
    expect(partner).not.toContain(5)
    expect(partner).toContain(6)
    expect(partner).toContain(505)
  })

  it('nimmt keine Zeile ohne Namen und ohne Kennung als Anzeige', async () => {
    const { gespraechsListe, merkeGespraech } = await frischImportieren()
    await merkeGespraech(42, { username: '   ' })
    expect(await gespraechsListe()).toEqual([])
  })

  it('weist eine krumme Mailbox-Kennung ab', async () => {
    // Sie geht in ein Abo und von dort in eine Serveranfrage. Eine Kennung,
    // die keine ist, wäre dort eine 422 — und `melde()` verschluckt die.
    const { gespraechsListe, merkeGespraech } = await frischImportieren()
    await merkeGespraech(42, { username: 'Nachbarin', blindMailboxId: 'keine-kennung' })
    expect((await gespraechsListe())[0].blind_mailbox_id).toBe('')
  })
})
