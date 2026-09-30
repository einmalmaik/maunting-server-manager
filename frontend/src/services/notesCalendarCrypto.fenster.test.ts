/**
 * Zwei Fenster, ein Schlüssel: die Desktop-App hat Hauptfenster und Overlay,
 * beide mit eigenem Arbeitsspeicher und derselben Ablage.
 *
 * Bis 5.0.4 hielt jedes Fenster den Notizschlüssel, den es zuerst gelesen
 * hatte. Bekam das Hauptfenster den des Kontos, schrieb das Overlay weiter mit
 * dem alten — und was es schrieb, war auf den anderen Geräten Blob, bis das
 * Hauptfenster es irgendwann neu verschlüsselte.
 */
import { beforeEach, describe, expect, it } from 'vitest'

import {
  clearNotesKeyCache,
  decryptNoteTitle,
  encryptNoteTitle,
  getUserNotesKey,
} from './notesCalendarCrypto'
import { importAesGcmRawKey } from '@msdis/shield/aead'

const KONTO = 5
const ABLAGE = `msm_e2ee_notes_key_${KONTO}`

function zufall(): string {
  return btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32))))
}

async function schluessel(roh: string): Promise<CryptoKey> {
  return importAesGcmRawKey(Uint8Array.from(atob(roh), (c) => c.charCodeAt(0)), ['encrypt', 'decrypt'])
}

describe('Notizschlüssel über Fenster hinweg', () => {
  beforeEach(() => {
    clearNotesKeyCache()
    localStorage.clear()
  })

  it('schreibt nach einem Wechsel im anderen Fenster mit dem neuen Schlüssel', async () => {
    const alt = zufall()
    const neu = zufall()
    localStorage.setItem(ABLAGE, alt)
    await getUserNotesKey(KONTO) // dieses Fenster hat ihn jetzt im Speicher

    // Das andere Fenster übernimmt den des Kontos. Hier kommt davon nur das
    // `storage`-Ereignis an.
    localStorage.setItem(ABLAGE, neu)
    window.dispatchEvent(new StorageEvent('storage', { key: ABLAGE, newValue: neu, oldValue: alt }))

    const ct = await encryptNoteTitle('aus dem Overlay', 'uid-1', undefined, KONTO)
    await expect(decryptNoteTitle(ct, 'uid-1', await schluessel(neu), KONTO)).resolves.toBe('aus dem Overlay')
  })

  it('ein fremder Ablageschlüssel lässt den Speicher in Ruhe', async () => {
    const roh = zufall()
    localStorage.setItem(ABLAGE, roh)
    const vorher = await getUserNotesKey(KONTO)
    window.dispatchEvent(new StorageEvent('storage', { key: 'irgendwas', newValue: 'x' }))
    expect(await getUserNotesKey(KONTO)).toBe(vorher)
  })
})
