/**
 * Die Zusicherung hinter dem Quittungsstand.
 *
 * Der Stand existiert nur aus einem Grund: die Mailbox fasst hundert Umschläge,
 * und eine doppelt geschickte Quittung verdrängt daraus eine echte Nachricht.
 * Geprüft wird deshalb genau das — dass derselbe Stand kein zweites Mal
 * quittiert wird, auch nicht über einen Chatwechsel hinweg, und dass er nie
 * zurückfällt.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'

import { merkePartnerQuittung, merkeQuittung, partnerstand, quittungsstand } from './quittungsstand'

const MID = 'mb-1'
const ANDERE = 'mb-2'

beforeEach(() => {
  localStorage.clear()
})

describe('Quittungsstand', () => {
  it('beginnt bei null', () => {
    expect(quittungsstand(MID)).toEqual({ zugestellt: 0, gelesen: 0 })
  })

  it('hält den Stand über einen Chatwechsel hinweg', () => {
    merkeQuittung(MID, 'zugestellt', 42)
    // Zwischendurch ein anderer Chat — im Messenger setzt das die Zähler im
    // Speicher zurück. Die Ablage darf davon nichts mitbekommen.
    expect(quittungsstand(ANDERE).zugestellt).toBe(0)
    expect(quittungsstand(MID).zugestellt).toBe(42)
  })

  it('trennt Zustellung und Lesung', () => {
    merkeQuittung(MID, 'zugestellt', 42)
    expect(quittungsstand(MID)).toEqual({ zugestellt: 42, gelesen: 0 })
    merkeQuittung(MID, 'gelesen', 40)
    expect(quittungsstand(MID)).toEqual({ zugestellt: 42, gelesen: 40 })
  })

  it('fällt nicht zurück', () => {
    // Ein später eintreffender Abruf kann einen kleineren Höchstwert
    // mitbringen. Würde der Stand darauf zurückfallen, ginge dieselbe Quittung
    // ein zweites Mal raus.
    merkeQuittung(MID, 'zugestellt', 42)
    merkeQuittung(MID, 'zugestellt', 7)
    expect(quittungsstand(MID).zugestellt).toBe(42)
  })

  it('ignoriert leere Kennungen und unbrauchbare Zahlen', () => {
    merkeQuittung('', 'zugestellt', 42)
    merkeQuittung(MID, 'zugestellt', 0)
    merkeQuittung(MID, 'zugestellt', Number.NaN)
    merkeQuittung(MID, 'zugestellt', -3)
    merkeQuittung(MID, 'zugestellt', 7.5)
    merkeQuittung(MID, 'zugestellt', Number.POSITIVE_INFINITY)
    expect(quittungsstand(MID).zugestellt).toBe(0)
    expect(quittungsstand('')).toEqual({ zugestellt: 0, gelesen: 0 })
  })

  it('lässt sich nicht mit einer Riesenzahl zuriegeln', () => {
    // Gefunden beim Beschiessen der Ablage: `1e308` ist endlich und wurde
    // übernommen. Danach lag jede echte Kennung darunter — für diese Mailbox
    // wäre nie wieder eine Quittung rausgegangen.
    localStorage.setItem(
      'msm:chat_quittungsstand',
      JSON.stringify({ [MID]: { zugestellt: 1e308, gelesen: 1e308 } }),
    )
    expect(quittungsstand(MID)).toEqual({ zugestellt: 0, gelesen: 0 })
    merkeQuittung(MID, 'zugestellt', 42)
    expect(quittungsstand(MID).zugestellt).toBe(42)
  })

  it('überlebt kaputten Inhalt in der Ablage', () => {
    localStorage.setItem('msm:chat_quittungsstand', '{kein json')
    expect(quittungsstand(MID)).toEqual({ zugestellt: 0, gelesen: 0 })
    merkeQuittung(MID, 'gelesen', 5)
    expect(quittungsstand(MID).gelesen).toBe(5)
  })

  it('merkt sich, was der Partner gelesen hat', () => {
    // Seit der Abruf nur noch Neues liest, liegen die Quittungen nach dem
    // ersten Lesen hinter dem Stand. Ohne diesen Merker fielen die Häkchen an
    // den eigenen Nachrichten bei jedem Chatwechsel auf „gesendet" zurück,
    // obwohl der Partner gelesen hatte.
    expect(partnerstand(MID)).toEqual({ zugestellt: 0, gelesen: 0 })
    merkePartnerQuittung(MID, 'zugestellt', 358)
    merkePartnerQuittung(MID, 'gelesen', 358)
    expect(partnerstand(MID)).toEqual({ zugestellt: 358, gelesen: 358 })
    // Ein anderes Gespräch bleibt davon unberührt.
    expect(partnerstand(ANDERE)).toEqual({ zugestellt: 0, gelesen: 0 })
  })

  it('hält den Partnerstand vom eigenen getrennt', () => {
    // Beide nennen Kennungen, aber aus verschiedenen Richtungen: die eigenen
    // sind die fremden nicht. Ein gemeinsamer Eintrag hielte eine Zahl für die
    // andere.
    merkeQuittung(MID, 'gelesen', 12)
    merkePartnerQuittung(MID, 'gelesen', 400)
    expect(quittungsstand(MID).gelesen).toBe(12)
    expect(partnerstand(MID).gelesen).toBe(400)
  })

  it('lässt auch den Partnerstand nicht zurückfallen', () => {
    merkePartnerQuittung(MID, 'gelesen', 400)
    merkePartnerQuittung(MID, 'gelesen', 120)
    expect(partnerstand(MID).gelesen).toBe(400)
  })

  it('bleibt stehen, wenn die Ablage nicht schreibt', () => {
    const schreiben = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('voll')
    })
    try {
      expect(() => merkeQuittung(MID, 'zugestellt', 9)).not.toThrow()
    } finally {
      schreiben.mockRestore()
    }
  })

  it('liegt nicht unter dem Präfix, das der Messenger wegräumt', () => {
    merkeQuittung(MID, 'zugestellt', 9)
    const schluessel = Object.keys(localStorage)
    expect(schluessel).toContain('msm:chat_quittungsstand')
    expect(schluessel.some((k) => k.startsWith('msm_chat_'))).toBe(false)
  })
})
