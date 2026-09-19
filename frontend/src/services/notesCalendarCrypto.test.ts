import { describe, it, expect, beforeEach } from 'vitest'
import {
  NOTE_CIPHERTEXT_PREFIX,
  CALENDAR_CIPHERTEXT_PREFIX,
  encryptNoteTitle,
  encryptNoteContent,
  decryptNoteTitle,
  decryptNoteContent,
  encryptCalendarField,
  decryptCalendarField,
  getOrCreateUserNotesKey,
  setUserNotesKey,
  exportUserNotesKey,
  clearNotesKeyCache,
} from './notesCalendarCrypto'

describe('notesCalendarCrypto E2EE', () => {
  beforeEach(() => {
    clearNotesKeyCache()
    if (typeof localStorage !== 'undefined') {
      localStorage.clear()
    }
  })

  it('generates and persists user notes key locally without server involvement', async () => {
    const key1 = await getOrCreateUserNotesKey(42)
    expect(key1).toBeDefined()
    const exported = exportUserNotesKey(42)
    expect(exported).toBeTruthy()
    expect(typeof exported).toBe('string')

    // Re-retrieving yields the same key
    const key2 = await getOrCreateUserNotesKey(42)
    expect(key2).toBe(key1)
  })

  it('encrypts and decrypts note title and content with sv-note-v1: prefix', async () => {
    const noteUid = 'test-note-uid-123'
    const plainTitle = 'Einkaufsliste fürs Wochenende'
    const plainContent = '- [ ] Butter\n- [ ] Hafermilch\n- [ ] Kaffee'

    const encTitle = await encryptNoteTitle(plainTitle, noteUid, undefined, 42)
    const encContent = await encryptNoteContent(plainContent, noteUid, undefined, 42)

    // Verify prefix
    expect(encTitle.startsWith(NOTE_CIPHERTEXT_PREFIX)).toBe(true)
    expect(encContent.startsWith(NOTE_CIPHERTEXT_PREFIX)).toBe(true)

    // Verify plaintext is NOT present in ciphertext
    expect(encTitle).not.toContain(plainTitle)
    expect(encContent).not.toContain('Hafermilch')

    // Decrypt
    const decTitle = await decryptNoteTitle(encTitle, noteUid, undefined, 42)
    const decContent = await decryptNoteContent(encContent, noteUid, undefined, 42)

    expect(decTitle).toBe(plainTitle)
    expect(decContent).toBe(plainContent)
  })

  it('encrypts and decrypts calendar fields with sv-cal-v1: prefix', async () => {
    const eventUid = 'evt-abc-999'
    const plainTitle = 'Strategie-Meeting'
    const plainDesc = 'Besprechung der neuen E2EE Architektur'
    const plainLoc = 'Konferenzraum B'

    const encTitle = await encryptCalendarField(plainTitle, eventUid, 'title', undefined, 42)
    const encDesc = await encryptCalendarField(plainDesc, eventUid, 'description', undefined, 42)
    const encLoc = await encryptCalendarField(plainLoc, eventUid, 'location', undefined, 42)

    expect(encTitle.startsWith(CALENDAR_CIPHERTEXT_PREFIX)).toBe(true)
    expect(encDesc.startsWith(CALENDAR_CIPHERTEXT_PREFIX)).toBe(true)
    expect(encLoc.startsWith(CALENDAR_CIPHERTEXT_PREFIX)).toBe(true)

    expect(encTitle).not.toContain(plainTitle)
    expect(encDesc).not.toContain(plainDesc)
    expect(encLoc).not.toContain(plainLoc)

    const decTitle = await decryptCalendarField(encTitle, eventUid, 'title', undefined, 42)
    const decDesc = await decryptCalendarField(encDesc, eventUid, 'description', undefined, 42)
    const decLoc = await decryptCalendarField(encLoc, eventUid, 'location', undefined, 42)

    expect(decTitle).toBe(plainTitle)
    expect(decDesc).toBe(plainDesc)
    expect(decLoc).toBe(plainLoc)
  })

  it('supports seamless legacy plaintext passthrough', async () => {
    const legacyTitle = 'Alte Notiz vor E2EE'
    const legacyContent = 'Inhalt ohne Verschlüsselung'

    const decTitle = await decryptNoteTitle(legacyTitle, 'uid-legacy', undefined, 42)
    const decContent = await decryptNoteContent(legacyContent, 'uid-legacy', undefined, 42)

    expect(decTitle).toBe(legacyTitle)
    expect(decContent).toBe(legacyContent)
  })

  it('fails decryption if encrypted with a different user key', async () => {
    const noteUid = 'private-note-777'
    const plainTitle = 'Streng vertraulich'

    // User 1 encrypts
    const encTitle = await encryptNoteTitle(plainTitle, noteUid, undefined, 1)

    // User 2 attempts to decrypt
    await expect(decryptNoteTitle(encTitle, noteUid, undefined, 2)).rejects.toThrow()
  })
})
