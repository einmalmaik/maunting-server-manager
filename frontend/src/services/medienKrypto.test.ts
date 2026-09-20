// @vitest-environment node
/**
 * Das eine Medienverfahren, geprüft am ganzen Weg: verschlüsseln, transportieren,
 * öffnen — und an allem, was dabei schiefgehen soll.
 *
 * Node-Umgebung, nicht jsdom: DIS prüft Eingaben mit `instanceof Uint8Array`.
 */

import { describe, expect, it } from 'vitest'

import {
  ANHANG_PREFIX,
  entschluesselePaket,
  maxAnhangBytes,
  maxKlartextBytes,
  neueFileId,
  verschluesselePaket,
  type MedienBindung,
} from './medienKrypto'

/** Muss `chat_media_validator.MAX_MEDIA_BYTES` entsprechen. */
const BLOB_DECKEL = 60 * 1024 * 1024

const ALICE = 7
const MAILBOX = 'b'.repeat(32)

function bindung(ueberschreiben: Partial<MedienBindung> = {}): MedienBindung {
  return { absenderId: ALICE, blindMailboxId: MAILBOX, fileId: 'anhang-1', ...ueberschreiben }
}

const BILD =
  'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='

/** Packt den Blob aus, ohne ihn zu öffnen — so sieht der Server ihn. */
function alsPaket(blob: string): { v: number; manifest: string; chunks: string[] } {
  return JSON.parse(Buffer.from(blob.slice(ANHANG_PREFIX.length), 'base64').toString('utf-8'))
}

describe('medienKrypto', () => {
  it('gibt bitgenau zurück, was hineinging', async () => {
    const { blob, paketSchluessel } = await verschluesselePaket(BILD, bindung(), {
      name: 'urlaub.png',
      mimeType: 'image/png',
    })

    expect(blob.startsWith(ANHANG_PREFIX)).toBe(true)
    expect(await entschluesselePaket(blob, paketSchluessel, bindung())).toBe(BILD)
  })

  it('trägt weder Inhalt noch Dateinamen offen im Blob', async () => {
    const geheim = 'data:text/plain;base64,' + Buffer.from('Kontonummer DE12 3456').toString('base64')
    const { blob } = await verschluesselePaket(geheim, bindung(), {
      name: 'Gehaltsabrechnung.pdf',
      mimeType: 'application/pdf',
    })

    // Der Blob als Ganzes und sein ausgepacktes Gerüst: nirgends ein Hinweis
    // auf Inhalt, Namen oder Typ. Das Manifest trägt beides und ist deshalb
    // selbst verschlüsselt.
    const roh = Buffer.from(blob.slice(ANHANG_PREFIX.length), 'base64').toString('utf-8')
    for (const spur of ['Gehaltsabrechnung', 'application/pdf', 'Kontonummer', 'DE12']) {
      expect(blob).not.toContain(spur)
      expect(roh).not.toContain(spur)
    }
    expect(alsPaket(blob).manifest).not.toContain('Gehalt')
  })

  it('braucht genau seinen Paketschlüssel', async () => {
    const { blob } = await verschluesselePaket(BILD, bindung(), { name: 'a.png', mimeType: 'image/png' })
    const fremder = (await verschluesselePaket('data:text/plain,x', bindung(), {
      name: 'b.txt',
      mimeType: 'text/plain',
    })).paketSchluessel

    await expect(entschluesselePaket(blob, fremder, bindung())).rejects.toThrow()
  })

  describe('Bindung', () => {
    it('lässt sich nicht in eine andere Mailbox umhängen', async () => {
      const { blob, paketSchluessel } = await verschluesselePaket(BILD, bindung(), {
        name: 'a.png',
        mimeType: 'image/png',
      })
      await expect(
        entschluesselePaket(blob, paketSchluessel, bindung({ blindMailboxId: 'c'.repeat(32) })),
      ).rejects.toThrow()
    })

    it('lässt sich nicht einem anderen Absender zuschreiben', async () => {
      const { blob, paketSchluessel } = await verschluesselePaket(BILD, bindung(), {
        name: 'a.png',
        mimeType: 'image/png',
      })
      await expect(
        entschluesselePaket(blob, paketSchluessel, bindung({ absenderId: ALICE + 1 })),
      ).rejects.toThrow()
    })

    it('lässt sich nicht als anderer Anhang ausgeben', async () => {
      const { blob, paketSchluessel } = await verschluesselePaket(BILD, bindung(), {
        name: 'a.png',
        mimeType: 'image/png',
      })
      await expect(
        entschluesselePaket(blob, paketSchluessel, bindung({ fileId: 'anhang-2' })),
      ).rejects.toThrow()
    })
  })

  describe('Manipulation', () => {
    /** Ein Anhang über mehrere Stücke — sonst gibt es nichts zu vertauschen. */
    async function mehrstueckig() {
      const gross = 'data:application/octet-stream;base64,' + 'QUJD'.repeat(3_000_000)
      const paket = await verschluesselePaket(gross, bindung(), {
        name: 'gross.bin',
        mimeType: 'application/octet-stream',
      })
      return { ...paket, klartext: gross }
    }

    function neuVerpacken(inhalt: { v: number; manifest: string; chunks: string[] }): string {
      return ANHANG_PREFIX + Buffer.from(JSON.stringify(inhalt), 'utf-8').toString('base64')
    }

    it('erkennt ein gekipptes Bit', async () => {
      const { blob, paketSchluessel } = await verschluesselePaket(BILD, bindung(), {
        name: 'a.png',
        mimeType: 'image/png',
      })
      const paket = alsPaket(blob)
      const bytes = Buffer.from(paket.chunks[0], 'base64')
      bytes[bytes.length - 1] ^= 0x01
      paket.chunks[0] = bytes.toString('base64')

      await expect(
        entschluesselePaket(neuVerpacken(paket), paketSchluessel, bindung()),
      ).rejects.toThrow()
    })

    it('erkennt vertauschte Stücke', async () => {
      const { blob, paketSchluessel } = await mehrstueckig()
      const paket = alsPaket(blob)
      expect(paket.chunks.length).toBeGreaterThan(1)

      const getauscht = { ...paket, chunks: [...paket.chunks] }
      ;[getauscht.chunks[0], getauscht.chunks[1]] = [getauscht.chunks[1], getauscht.chunks[0]]

      await expect(
        entschluesselePaket(neuVerpacken(getauscht), paketSchluessel, bindung()),
      ).rejects.toThrow()
    }, 60_000)

    it('erkennt ein fehlendes Stück', async () => {
      const { blob, paketSchluessel } = await mehrstueckig()
      const paket = alsPaket(blob)
      const gekuerzt = { ...paket, chunks: paket.chunks.slice(0, -1) }

      await expect(
        entschluesselePaket(neuVerpacken(gekuerzt), paketSchluessel, bindung()),
      ).rejects.toThrow()
    }, 60_000)

    it('weist einen fremden Umschlag ab', async () => {
      const { paketSchluessel } = await verschluesselePaket(BILD, bindung(), {
        name: 'a.png',
        mimeType: 'image/png',
      })
      await expect(
        entschluesselePaket('sv-blob-v1:AAAA', paketSchluessel, bindung()),
      ).rejects.toThrow('Kein Anhang dieses Verfahrens')
    })

    it('weist einen beschädigten Blob ab, statt ihn halb zu öffnen', async () => {
      const { paketSchluessel } = await verschluesselePaket(BILD, bindung(), {
        name: 'a.png',
        mimeType: 'image/png',
      })
      await expect(
        entschluesselePaket(`${ANHANG_PREFIX}nicht-base64!!`, paketSchluessel, bindung()),
      ).rejects.toThrow('beschädigt')
    })
  })

  it('gibt jedem Anhang eine eigene Kennung', () => {
    const kennungen = new Set(Array.from({ length: 50 }, () => neueFileId()))
    expect(kennungen.size).toBe(50)
  })

  it('bleibt mit der Obergrenze unter dem Deckel des Backends', async () => {
    // Die Rückrechnung muss stimmen, sonst läuft ein Upload in einen 413,
    // nachdem der Benutzer minutenlang verschlüsselt hat.
    //
    // Gemessen wird an einer Probe fester Größe und dem Aufschlag, den sie
    // zeigt, nicht am vollen Deckel: der liegt seit 09/2026 bei 60 MB, und
    // 34 MB Klartext zu verschlüsseln dauert im Test Minuten, ohne mehr zu
    // beweisen als diese Hochrechnung.
    const grenze = maxKlartextBytes()
    expect(grenze).toBeGreaterThan(5 * 1024 * 1024)

    const probe = 'd'.repeat(1024 * 1024)
    const { blob } = await verschluesselePaket(probe, bindung(), {
      name: 'rand.bin',
      mimeType: null,
    })

    const aufschlag = blob.length / probe.length
    expect(Math.ceil(grenze * aufschlag)).toBeLessThanOrEqual(BLOB_DECKEL)
  }, 120_000)

  it('rechnet aus dem Deckel die rohe Dateigröße zurück', () => {
    // Eine data-URL kostet noch einmal ein Drittel. Dateiauswahl, Videonotiz
    // und die Bitrate der Aufnahme messen alle roh und gegen diese Zahl.
    expect(maxAnhangBytes()).toBe(Math.floor(maxKlartextBytes() * 0.75))
    expect(maxAnhangBytes()).toBeLessThan(maxKlartextBytes())
  })
})
