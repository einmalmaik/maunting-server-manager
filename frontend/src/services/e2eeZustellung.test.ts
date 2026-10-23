/**
 * Der Client der gerätebezogenen Zustellung.
 *
 * Zwei Dinge werden hier geprüft:
 *
 * 1. **Die Zeichenketten, die unterschrieben werden.** Sie müssen mit denen
 *    des Servers zeichengleich sein — sonst hält der Server jeden Beleg fuer
 *    falsch. Der Server bildet sie in
 *    `backend/services/social/zustellung.py`; hier stehen sie als Text, damit
 *    eine Aenderung an einer Seite die andere sichtbar bricht.
 * 2. **Die Adresse und die Aufrufe.** Die Adresse entsteht aus dem geheimen
 *    Schluesselpaar; der Abruf und das Quittung tragen den Beleg als Kopf.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/api/client', () => ({ api: vi.fn() }))
vi.mock('./absenderSignatur', () => ({ signiere: vi.fn(async (daten: string) => `sig:${daten}`) }))
vi.mock('./mailboxNachweis', () => ({ nachweisKopf: () => ({ 'X-Mailbox-Token': 'nachweis' }) }))

const GERAET = {
  kennung: 'geraet-1',
  paar: { publicKeyJwk: '{"kty":"RSA"}', privateKeyJwk: '{"kty":"RSA","d":"geheim"}' },
  signaturPaar: { publicKeyJwk: '{"kty":"EC"}', privateKeyJwk: '{"kty":"EC","d":"signatur"}' },
}

vi.mock('./e2eeGeraet', () => ({ eigenesGeraet: vi.fn(async () => GERAET) }))

import { api } from '@/api/client'
import { signiere } from './absenderSignatur'
import {
  ackBeweisDaten,
  eigeneInboxAdresse,
  holeZustellungen,
  leseBeweisDaten,
  quittiereZustellungen,
  relayBeweisDaten,
  sendeZustellungen,
} from './e2eeZustellung'

describe('e2eeZustellung', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('bindet den Beleg an Geraet, Mailbox und jeden Eintrag', () => {
    // Die Reihenfolge im Paket darf nichts bedeuten: der Server sortiert
    // dieselben Zeichenketten.
    const a = relayBeweisDaten('g1', 'mb1', [
      { delivery_id: 'd2', payload_id: 'p2', inbox_hash: 'i2', ciphertext_sha256: 'h2' },
      { delivery_id: 'd1', payload_id: 'p1', inbox_hash: 'i1', ciphertext_sha256: 'h1' },
    ])
    const b = relayBeweisDaten('g1', 'mb1', [
      { delivery_id: 'd1', payload_id: 'p1', inbox_hash: 'i1', ciphertext_sha256: 'h1' },
      { delivery_id: 'd2', payload_id: 'p2', inbox_hash: 'i2', ciphertext_sha256: 'h2' },
    ])
    expect(a).toBe(b)
    expect(a).toBe('msm:device-delivery-v1:g1:mb1:d1:p1:i1:h1|d2:p2:i2:h2')
  })

  it('bindet den Lesebeleg an die Adresse und den Stand', () => {
    expect(leseBeweisDaten('abc', 7)).toBe('msm:device-delivery-read-v1:abc:7')
  })

  it('bindet den Quittbeleg an die Adresse und die Kennungen', () => {
    expect(ackBeweisDaten('abc', ['z', 'a'])).toBe('msm:device-delivery-ack-v1:abc:a,z')
  })

  it('leitet die Zustelladresse aus dem geheimen Schluesselpaar ab', async () => {
    const adresse = await eigeneInboxAdresse()
    // 64 Hex-Zeichen: ein SHA-256 ueber das Paar.
    expect(adresse).toMatch(/^[0-9a-f]{64}$/)
    // Und sie ist stabil: dasselbe Paar, dieselbe Adresse.
    expect(await eigeneInboxAdresse()).toBe(adresse)
  })

  it('schickt einen Stapel mit Beleg und Kontext', async () => {
    vi.mocked(api).mockResolvedValue({ angenommen: [], abgelehnt: [] } as never)
    const ergebnis = await sendeZustellungen(
      'mailbox-1',
      [{ id: 'p1', ciphertext: 'ct', ciphertext_sha256: 'h1' }],
      [{ delivery_id: 'd1', payload_id: 'p1', inbox_hash: 'i1' }],
    )
    expect(ergebnis).toEqual({ angenommen: [], abgelehnt: [] })
    const [pfad, optionen] = vi.mocked(api).mock.calls[0]
    expect(pfad).toBe('/social/e2ee/deliveries')
    expect(optionen?.method).toBe('POST')
    const koerper = JSON.parse(String(optionen?.body))
    expect(koerper.mailbox_id).toBe('mailbox-1')
    expect(koerper.geraet_id).toBe('geraet-1')
    expect(koerper.eintraege).toEqual([
      { delivery_id: 'd1', payload_id: 'p1', inbox_hash: 'i1' },
    ])
    // Der Beleg ist ueber genau diesen Stapel gebildet.
    expect(koerper.signatur).toBe(
      'sig:msm:device-delivery-v1:geraet-1:mailbox-1:d1:p1:i1:h1',
    )
    expect(signiere).toHaveBeenCalledTimes(1)
  })

  it('holt die offenen Auftraege mit dem Beleg als Kopf', async () => {
    vi.mocked(api).mockResolvedValue({ eintraege: [], naechste_seq: 0 } as never)
    await holeZustellungen('inbox-1', 12)
    const [pfad, optionen] = vi.mocked(api).mock.calls[0]
    expect(String(pfad)).toContain('/social/e2ee/deliveries?')
    expect(String(pfad)).toContain('inbox_hash=inbox-1')
    expect(String(pfad)).toContain('seit_seq=12')
    expect((optionen?.headers as Record<string, string>)['X-Geraete-Beleg']).toBe(
      'sig:msm:device-delivery-read-v1:inbox-1:12',
    )
  })

  it('quittiert mit Beleg und Kennungen', async () => {
    vi.mocked(api).mockResolvedValue({ quittiert: 2 } as never)
    const ergebnis = await quittiereZustellungen('inbox-1', ['d1', 'd2'])
    expect(ergebnis).toEqual({ quittiert: 2 })
    const [pfad, optionen] = vi.mocked(api).mock.calls[0]
    expect(pfad).toBe('/social/e2ee/deliveries/ack')
    const koerper = JSON.parse(String(optionen?.body))
    expect(koerper.inbox_hash).toBe('inbox-1')
    expect(koerper.geraet_id).toBe('geraet-1')
    expect(koerper.delivery_ids).toEqual(['d1', 'd2'])
    expect(koerper.signatur).toBe('sig:msm:device-delivery-ack-v1:inbox-1:d1,d2')
  })
})
