/**
 * @vitest-environment node
 *
 * Das Siegel über den lokalen Ablagen. Was hier schiefgeht, merkt niemand: ein
 * Datensatz, der versehentlich im Klartext landet, sieht von außen genauso aus
 * wie einer, der zu ist. Deshalb prüfen diese Tests nicht nur, dass Lesen und
 * Schreiben zusammenpassen, sondern auch, dass im Geheimtext wirklich nichts
 * mehr steht.
 */

import { generateAesGcmKey } from '@msdis/shield/aead'
import { beforeEach, describe, expect, it } from 'vitest'

import { setzeAngemeldetesKonto } from '@/lib/angemeldetesKonto'

import {
  MessengerVerschlossenError,
  entsiegleZeile,
  entsiegleZeilen,
  istOffen,
  setzeInhaltsSchluessel,
  setzeSiegelAktiv,
  siegelAktiv,
  versiegleZeile,
} from './lokaleVersiegelung'

const KLARFELDER = ['blindMailboxId', 'id'] as const
const AAD = 'msm-nachricht:mailbox-a:7'

function nachricht() {
  return {
    blindMailboxId: 'mailbox-a',
    id: 7,
    text: 'Treffen wir uns um acht?',
    senderName: 'Jules',
    isSelf: false,
  }
}

/** Ein localStorage, das nur im Arbeitsspeicher lebt. */
function installiereAblage() {
  const daten = new Map<string, string>()
  ;(globalThis as any).localStorage = {
    getItem: (k: string) => daten.get(k) ?? null,
    setItem: (k: string, v: string) => daten.set(k, v),
    removeItem: (k: string) => daten.delete(k),
    clear: () => daten.clear(),
  }
}

describe('lokaleVersiegelung', () => {
  beforeEach(() => {
    installiereAblage()
    setzeAngemeldetesKonto(1)
    setzeSiegelAktiv(false)
    setzeInhaltsSchluessel(null)
  })

  describe('ohne PIN', () => {
    it('lässt den Datensatz unverändert durch', async () => {
      const zeile = await versiegleZeile(nachricht(), KLARFELDER, AAD)
      expect(zeile).toEqual(nachricht())
      expect(zeile.blob).toBeUndefined()
      expect(await entsiegleZeile(zeile, AAD)).toEqual(nachricht())
    })

    it('meldet sich als offen', () => {
      expect(siegelAktiv()).toBe(false)
      expect(istOffen()).toBe(true)
    })
  })

  describe('mit PIN', () => {
    beforeEach(async () => {
      setzeSiegelAktiv(true)
      setzeInhaltsSchluessel(await generateAesGcmKey())
    })

    it('lässt nur die Schlüsselfelder stehen und verpackt den Rest', async () => {
      const zeile = await versiegleZeile(nachricht(), KLARFELDER, AAD)

      expect(zeile.blindMailboxId).toBe('mailbox-a')
      expect(zeile.id).toBe(7)
      expect(zeile.v).toBe(1)
      expect(typeof zeile.blob).toBe('string')

      // Weder Text noch Absender dürfen die Zeile verlassen haben.
      expect(zeile.text).toBeUndefined()
      expect(zeile.senderName).toBeUndefined()
      const alsText = JSON.stringify(zeile)
      expect(alsText).not.toContain('Treffen wir uns')
      expect(alsText).not.toContain('Jules')
    })

    it('gibt beim Öffnen genau das zurück, was hineinging', async () => {
      const zeile = await versiegleZeile(nachricht(), KLARFELDER, AAD)
      expect(await entsiegleZeile(zeile, AAD)).toEqual(nachricht())
    })

    it('öffnet eine Zeile nicht an einem fremden Platz', async () => {
      // Zwei Zeilen zu vertauschen, ergibt keine gültige Zeile mehr, sondern
      // gar keine. Das ist der Zweck der Bindung.
      const zeile = await versiegleZeile(nachricht(), KLARFELDER, AAD)
      expect(await entsiegleZeile(zeile, 'msm-nachricht:mailbox-b:7')).toBeNull()
    })

    it('öffnet eine Zeile nicht mit einem fremden Schlüssel', async () => {
      const zeile = await versiegleZeile(nachricht(), KLARFELDER, AAD)
      setzeInhaltsSchluessel(await generateAesGcmKey())
      expect(await entsiegleZeile(zeile, AAD)).toBeNull()
    })

    it('liest alte Zeilen im Klartext weiter', async () => {
      // Der Zustand mitten in der Umstellung: halb versiegelt, halb nicht.
      // Beides muss lesbar bleiben, sonst wäre ein Abbruch ein Datenverlust.
      expect(await entsiegleZeile(nachricht(), AAD)).toEqual(nachricht())
    })

    it('lässt beim Öffnen mehrerer Zeilen die unlesbaren aus', async () => {
      const gut = await versiegleZeile(nachricht(), KLARFELDER, AAD)
      const kaputt = { blindMailboxId: 'mailbox-a', id: 8, v: 1, blob: 'kein gueltiger Geheimtext' }
      const offen = await entsiegleZeilen([gut, kaputt], (z) =>
        z.id === 7 ? AAD : 'msm-nachricht:mailbox-a:8',
      )
      expect(offen).toHaveLength(1)
      expect(offen[0]).toEqual(nachricht())
    })
  })

  describe('gesperrt', () => {
    beforeEach(() => {
      setzeSiegelAktiv(true)
      setzeInhaltsSchluessel(null)
    })

    it('meldet sich als zu', () => {
      expect(istOffen()).toBe(false)
    })

    it('schreibt nichts, sondern wirft', async () => {
      // Der wichtigste Test der Datei. Ein Schreibweg, der hier still
      // Klartext ablegte, sähe von außen aus wie Schutz und wäre keiner.
      await expect(versiegleZeile(nachricht(), KLARFELDER, AAD)).rejects.toBeInstanceOf(
        MessengerVerschlossenError,
      )
    })

    it('gibt versiegelte Zeilen nicht heraus', async () => {
      setzeInhaltsSchluessel(await generateAesGcmKey())
      const zeile = await versiegleZeile(nachricht(), KLARFELDER, AAD)
      setzeInhaltsSchluessel(null)

      expect(await entsiegleZeile(zeile, AAD)).toBeNull()
    })
  })

  describe('Kontotrennung', () => {
    it('hält den Schalter je Konto getrennt', () => {
      setzeAngemeldetesKonto(1)
      setzeSiegelAktiv(true)
      expect(siegelAktiv()).toBe(true)

      // Zweites Konto im selben Browser: dessen Messenger ist nicht versiegelt,
      // nur weil das erste einen PIN hat.
      setzeAngemeldetesKonto(2)
      expect(siegelAktiv()).toBe(false)

      setzeAngemeldetesKonto(1)
      expect(siegelAktiv()).toBe(true)
    })

    it('behauptet ohne angemeldetes Konto nichts', () => {
      setzeAngemeldetesKonto(null)
      expect(siegelAktiv()).toBe(false)
    })
  })
})
