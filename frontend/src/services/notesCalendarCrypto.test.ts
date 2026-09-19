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
  generateClientEntityId,
} from './notesCalendarCrypto'

describe('notesCalendarCrypto E2EE', () => {
  beforeEach(() => {
    clearNotesKeyCache()
    if (typeof localStorage !== 'undefined') {
      localStorage.clear()
    }
  })

  it('generates standard RFC4122 v4 UUIDs for client entities', () => {
    const id1 = generateClientEntityId()
    const id2 = generateClientEntityId()
    expect(id1).not.toBe(id2)
    const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
    expect(uuidRegex.test(id1)).toBe(true)
    expect(uuidRegex.test(id2)).toBe(true)
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

  it('enables seamless multi-device E2EE key handover and bidirectional note/calendar decryption', async () => {
    const userId = 99
    // Device A creates key and encrypts note & calendar event
    const devAKey = await getOrCreateUserNotesKey(userId)
    const noteUid = generateClientEntityId()
    const eventUid = generateClientEntityId()

    const encTitleA = await encryptNoteTitle('Geheime Notiz von Gerät A', noteUid, devAKey, userId)
    const encContentA = await encryptNoteContent('Details zu Gerät A', noteUid, devAKey, userId)
    const encCalA = await encryptCalendarField('Wartung von Gerät A', eventUid, 'title', devAKey, userId)

    // Device A exports key for device pairing handover
    const exportedKey = exportUserNotesKey(userId)
    expect(exportedKey).toBeTruthy()

    // Device B receives handover packet and sets the key
    clearNotesKeyCache()
    const devBKey = await setUserNotesKey(userId, exportedKey!)

    // Device B decrypts note and calendar event created by Device A
    const decTitleB = await decryptNoteTitle(encTitleA, noteUid, devBKey, userId)
    const decContentB = await decryptNoteContent(encContentA, noteUid, devBKey, userId)
    const decCalB = await decryptCalendarField(encCalA, eventUid, 'title', devBKey, userId)

    expect(decTitleB).toBe('Geheime Notiz von Gerät A')
    expect(decContentB).toBe('Details zu Gerät A')
    expect(decCalB).toBe('Wartung von Gerät A')

    // Device B now creates an update, and Device A can decrypt it with the shared key
    const encTitleB = await encryptNoteTitle('Antwort von Gerät B', noteUid, devBKey, userId)
    const decTitleA = await decryptNoteTitle(encTitleB, noteUid, devAKey, userId)
    expect(decTitleA).toBe('Antwort von Gerät B')
  })

  it('strictly validates AAD integrity: mismatched entity UID throws decryption error', async () => {
    const userId = 55
    const noteUid1 = generateClientEntityId()
    const noteUid2 = generateClientEntityId()

    const encTitle = await encryptNoteTitle('Einkauf', noteUid1, undefined, userId)

    // Attempting to decrypt with a different entity UID must be rejected by AES-GCM auth tag
    await expect(decryptNoteTitle(encTitle, noteUid2, undefined, userId)).rejects.toThrow()
  })
})
