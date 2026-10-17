import { describe, expect, it } from 'vitest'

import {
  aktuellesWechselBild,
  BILD_HINTERGRUENDE,
  FARB_HINTERGRUENDE,
  HINTERGRUND_OPTIONEN,
  hintergrundStil,
} from './hintergruende'

describe('Hintergruende Logik', () => {
  it('haelt genau 5 KI-Standardhintergruende vor', () => {
    expect(BILD_HINTERGRUENDE).toHaveLength(5)
    expect(BILD_HINTERGRUENDE.map((b) => b.id)).toEqual(['erde', 'sonnensystem', 'bergwald', 'ozean', 'feld'])
    for (const bild of BILD_HINTERGRUENDE) {
      expect(bild.pfad).toMatch(/^\/hintergruende\/[a-z]+\.webp$/)
    }
  })

  it('wechselt das Hintergrundbild alle 30 Minuten deterministisch durch', () => {
    const basis = 0
    const halbeStunde = 30 * 60 * 1000

    expect(aktuellesWechselBild(basis).id).toBe(BILD_HINTERGRUENDE[0].id)
    expect(aktuellesWechselBild(basis + halbeStunde).id).toBe(BILD_HINTERGRUENDE[1].id)
    expect(aktuellesWechselBild(basis + 2 * halbeStunde).id).toBe(BILD_HINTERGRUENDE[2].id)
    expect(aktuellesWechselBild(basis + 3 * halbeStunde).id).toBe(BILD_HINTERGRUENDE[3].id)
    expect(aktuellesWechselBild(basis + 4 * halbeStunde).id).toBe(BILD_HINTERGRUENDE[4].id)
    // Nach 5 Intervallen wieder am Anfang
    expect(aktuellesWechselBild(basis + 5 * halbeStunde).id).toBe(BILD_HINTERGRUENDE[0].id)
  })

  it('erzeugt passenden Hintergrundstil fuer Farben, feste Bilder und Wechselmodus', () => {
    // Farbe
    expect(hintergrundStil('schlicht', null)).toEqual({})
    expect(hintergrundStil('nacht', null)).toEqual({ background: FARB_HINTERGRUENDE.nacht })

    // Festes Bild
    const erdeStil = hintergrundStil('erde', null)
    expect(erdeStil.backgroundImage).toContain('/hintergruende/erde.webp')

    // Wechselndes Bild
    const wechselStil = hintergrundStil('wechsel', null, 0)
    expect(wechselStil.backgroundImage).toContain(BILD_HINTERGRUENDE[0].pfad)

    // Eigenes Bild
    const eigenStil = hintergrundStil('eigen', 'data:image/webp;base64,abc')
    expect(eigenStil.backgroundImage).toBe('url("data:image/webp;base64,abc")')
  })

  it('bietet alle Hintergrundoptionen in definierter Reihenfolge an', () => {
    expect(HINTERGRUND_OPTIONEN).toContain('wechsel')
    expect(HINTERGRUND_OPTIONEN).toContain('erde')
    expect(HINTERGRUND_OPTIONEN).toContain('schlicht')
  })
})
