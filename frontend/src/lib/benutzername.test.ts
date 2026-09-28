import { describe, expect, it } from 'vitest'

import { benutzernameFehler, benutzernameVorschlag } from './benutzername'

/**
 * Dieselbe Regel wie `backend/schemas/user.py`. Die Fälle hier stehen dort
 * in `tests/test_benutzername.py::TestRegeln` genauso.
 */
describe('benutzernameFehler', () => {
  it.each(['max', 'Max_Mustermann', 'a.b-c', '_x_', 'x'.repeat(32)])('erlaubt %s', (name) => {
    expect(benutzernameFehler(name)).toBeNull()
  })

  it.each(['ab', 'x'.repeat(33), 'max@web.de', 'mit leer', '.max', 'max-', 'Ümlaut'])(
    'lehnt %s ab',
    (name) => {
      expect(benutzernameFehler(name)).toEqual({ schluessel: 'benutzername.fehlerForm' })
    },
  )

  it('sperrt die Erwähnungswörter ohne Rücksicht auf Groß/klein', () => {
    expect(benutzernameFehler('Everyone')).toEqual({
      schluessel: 'benutzername.fehlerReserviert',
      name: 'Everyone',
    })
    expect(benutzernameFehler('alle')?.schluessel).toBe('benutzername.fehlerReserviert')
  })
})

describe('benutzernameVorschlag', () => {
  it('nimmt den vorläufigen Namen, aber keinen Platzhalter', () => {
    expect(benutzernameVorschlag('Alice.GH')).toBe('Alice.GH')
    expect(benutzernameVorschlag('user_0a1b2c3d')).toBe('')
    expect(benutzernameVorschlag(undefined)).toBe('')
  })

  it('schlägt keinen Namen vor, der alt aus der E-Mail gebildet wurde', () => {
    expect(benutzernameVorschlag('mauntingstudiosgmailcom', 'mauntingstudios@gmail.com')).toBe('')
    expect(benutzernameVorschlag('mauntingstudiosgmailcom_2', 'mauntingstudios@gmail.com')).toBe('')
    expect(benutzernameVorschlag('max', 'max@firma.de')).toBe('')
    expect(benutzernameVorschlag('Alice.GH', 'alice@x.de')).toBe('Alice.GH')
  })
})
