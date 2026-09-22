import { describe, it, expect, beforeEach, vi } from 'vitest'
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
  hasUserNotesKey,
  getUserNotesKey,
  syncNotesKeyToPairedDevices,
  requestNotesKeyFromPairedDevices,
  processNotesKeyControlEnvelope,
  checkAndReceiveDeviceNotesKey,
  checkAndRespondToDeviceKeyRequests,
  altschluessel,
  altschluesselUebernehmen,
} from './notesCalendarCrypto'
import * as socialApi from '@/api/social'
import * as e2eeGeraet from './e2eeGeraet'
import { encryptE2eeHybrid, generateLocalE2eeKeyPair } from './e2eeCrypto'

vi.mock('@/api/social', () => ({
  relayE2eeEnvelope: vi.fn().mockResolvedValue({ id: 999, blind_mailbox_id: 'box-1' }),
  fetchE2eeEnvelopes: vi.fn().mockResolvedValue([]),
}))

vi.mock('./e2eeGeraet', () => ({
  eigenesGeraet: vi.fn(),
  geraeteVon: vi.fn(),
}))

describe('notesCalendarCrypto E2EE', () => {
  let selfPair: any
  let otherPair: any
  let reqPair: any

  beforeAll(async () => {
    selfPair = await generateLocalE2eeKeyPair()
    otherPair = await generateLocalE2eeKeyPair()
    reqPair = await generateLocalE2eeKeyPair()
  }, 45000)

  beforeEach(() => {
    vi.clearAllMocks()
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

  it('hasUserNotesKey checks key presence accurately', async () => {
    expect(hasUserNotesKey(101)).toBe(false)
    const raw32 = new Uint8Array(32).fill(7)
    const rawB64 = btoa(String.fromCharCode(...raw32))
    await setUserNotesKey(101, rawB64)
    expect(hasUserNotesKey(101)).toBe(true)

    clearNotesKeyCache()
    if (typeof localStorage !== 'undefined') localStorage.clear()
    expect(hasUserNotesKey(101)).toBe(false)
  })

  it('syncNotesKeyToPairedDevices transmits encrypted notes_key_sync to other paired devices', async () => {
    const userId = 102
    const raw32 = new Uint8Array(32).fill(42)
    const rawB64 = btoa(String.fromCharCode(...raw32))
    await setUserNotesKey(userId, rawB64)

    vi.mocked(e2eeGeraet.eigenesGeraet).mockResolvedValue({
      kennung: 'dev-self-102',
      paar: selfPair,
    })
    vi.mocked(e2eeGeraet.geraeteVon).mockResolvedValue([
      { device_id: 'dev-self-102', public_key: selfPair.publicKeyJwk, label: 'Main PC' },
      { device_id: 'dev-other-102', public_key: otherPair.publicKeyJwk, label: 'Secondary Mobile' },
    ])

    const count = await syncNotesKeyToPairedDevices(userId)
    expect(count).toBe(1)
    expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
      expect.objectContaining({
        recipient_id: userId,
        is_control: true,
        control_type: 'notes_key_sync',
      })
    )
  })

  it('processNotesKeyControlEnvelope decrypts notes_key_sync, sets key, and triggers msm:notes-key-updated', async () => {
    const userId = 103
    vi.mocked(e2eeGeraet.eigenesGeraet).mockResolvedValue({
      kennung: 'dev-self-103',
      paar: selfPair,
    })

    const raw32 = new Uint8Array(32).fill(99)
    const rawB64 = btoa(String.fromCharCode(...raw32))
    const payload = JSON.stringify({
      type: 'notes_key_sync',
      version: 1,
      userId,
      notesKey: rawB64,
      targetDeviceId: 'dev-self-103',
      senderDeviceId: 'dev-paired-103',
      timestamp: Date.now(),
    })
    const env = await encryptE2eeHybrid(payload, selfPair.publicKeyJwk)

    const eventSpy = vi.fn()
    window.addEventListener('msm:notes-key-updated', eventSpy)

    const processed = await processNotesKeyControlEnvelope(env, userId)
    expect(processed).toBe(true)
    expect(hasUserNotesKey(userId)).toBe(true)
    expect(exportUserNotesKey(userId)).toBe(rawB64)
    expect(eventSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        detail: { userId, rawKey: rawB64 },
      })
    )
    window.removeEventListener('msm:notes-key-updated', eventSpy)
  })

  it('processNotesKeyControlEnvelope answers notes_key_request by sending notes_key_sync to requester', async () => {
    const userId = 104
    const raw32 = new Uint8Array(32).fill(88)
    const rawB64 = btoa(String.fromCharCode(...raw32))
    await setUserNotesKey(userId, rawB64)

    vi.mocked(e2eeGeraet.eigenesGeraet).mockResolvedValue({
      kennung: 'dev-self-104',
      paar: selfPair,
    })

    const reqPayload = JSON.stringify({
      type: 'notes_key_request',
      version: 1,
      userId,
      requesterDeviceId: 'dev-requester-104',
      requesterPublicKey: reqPair.publicKeyJwk,
      timestamp: Date.now(),
    })
    const env = await encryptE2eeHybrid(reqPayload, selfPair.publicKeyJwk)

    const processed = await processNotesKeyControlEnvelope(env, userId)
    expect(processed).toBe(true)
    expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
      expect.objectContaining({
        recipient_id: userId,
        is_control: true,
        control_type: 'notes_key_sync',
      })
    )
  })

  it('checkAndReceiveDeviceNotesKey queries device mailbox and receives key', async () => {
    const userId = 105
    vi.mocked(e2eeGeraet.eigenesGeraet).mockResolvedValue({
      kennung: 'dev-self-105',
      paar: selfPair,
    })

    const raw32 = new Uint8Array(32).fill(77)
    const rawB64 = btoa(String.fromCharCode(...raw32))
    const payload = JSON.stringify({
      type: 'notes_key_sync',
      version: 1,
      userId,
      notesKey: rawB64,
      targetDeviceId: 'dev-self-105',
      senderDeviceId: 'dev-paired-105',
      timestamp: Date.now(),
    })
    const env = await encryptE2eeHybrid(payload, selfPair.publicKeyJwk)

    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValueOnce([
      {
        id: 501,
        blind_mailbox_id: 'mailbox-105',
        ciphertext_envelope: env,
        created_at: new Date().toISOString(),
      },
    ])

    const received = await checkAndReceiveDeviceNotesKey(userId)
    expect(received).toBe(true)
    expect(hasUserNotesKey(userId)).toBe(true)
    expect(exportUserNotesKey(userId)).toBe(rawB64)
  })

  it('getUserNotesKey returns null without generating random key when key is absent', async () => {
    const userId = 106
    expect(hasUserNotesKey(userId)).toBe(false)
    const key = await getUserNotesKey(userId)
    expect(key).toBeNull()
    expect(hasUserNotesKey(userId)).toBe(false)
    expect(exportUserNotesKey(userId)).toBeNull()
  })

  it('processNotesKeyControlEnvelope protects existing key from being overwritten by divergent key', async () => {
    const userId = 107
    const original32 = new Uint8Array(32).fill(11)
    const originalB64 = btoa(String.fromCharCode(...original32))
    await setUserNotesKey(userId, originalB64)

    vi.mocked(e2eeGeraet.eigenesGeraet).mockResolvedValue({
      kennung: 'dev-self-107',
      paar: selfPair,
    })

    const attacker32 = new Uint8Array(32).fill(99)
    const attackerB64 = btoa(String.fromCharCode(...attacker32))
    const payload = JSON.stringify({
      type: 'notes_key_sync',
      version: 1,
      userId,
      notesKey: attackerB64,
      targetDeviceId: 'dev-self-107',
      senderDeviceId: 'dev-rogue-107',
      timestamp: Date.now(),
    })
    const env = await encryptE2eeHybrid(payload, selfPair.publicKeyJwk)

    await processNotesKeyControlEnvelope(env, userId)
    // Key must still be the original key
    expect(exportUserNotesKey(userId)).toBe(originalB64)
  })

  it('checkAndRespondToDeviceKeyRequests answers pending request envelopes in mailbox', async () => {
    const userId = 108
    const raw32 = new Uint8Array(32).fill(55)
    const rawB64 = btoa(String.fromCharCode(...raw32))
    await setUserNotesKey(userId, rawB64)

    vi.mocked(e2eeGeraet.eigenesGeraet).mockResolvedValue({
      kennung: 'dev-self-108',
      paar: selfPair,
    })

    const reqPayload = JSON.stringify({
      type: 'notes_key_request',
      version: 1,
      userId,
      requesterDeviceId: 'dev-requester-108',
      requesterPublicKey: reqPair.publicKeyJwk,
      timestamp: Date.now(),
    })
    const env = await encryptE2eeHybrid(reqPayload, selfPair.publicKeyJwk)

    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValueOnce([
      {
        id: 701,
        blind_mailbox_id: 'mailbox-108',
        ciphertext_envelope: env,
        created_at: new Date().toISOString(),
      },
    ])

    const count = await checkAndRespondToDeviceKeyRequests(userId)
    expect(count).toBe(1)
    expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
      expect.objectContaining({
        recipient_id: userId,
        is_control: true,
        control_type: 'notes_key_sync',
      })
    )
  })

  // ── Altbestand unter der Kennung 1 (Schreibfehler bis 22.09.2026) ──

  describe('Altschlüssel der Kennung 1', () => {
    it('gibt nichts heraus, wenn nichts abgelegt ist', async () => {
      expect(await altschluessel()).toBeNull()
    })

    it('reicht den abgelegten Altschlüssel heraus, ohne einen zu erzeugen', async () => {
      await getOrCreateUserNotesKey(1)
      const alt = await altschluessel()
      expect(alt).not.toBeNull()
      // Rein lesend: für Kennung 7 darf dabei nichts entstanden sein.
      expect(hasUserNotesKey(7)).toBe(false)
    })

    it('übernimmt ihn auf die echte Kennung und meldet das Ereignis', async () => {
      await getOrCreateUserNotesKey(1)
      const alt = exportUserNotesKey(1)

      let gemeldet: number | null = null
      const horcher = (e: any) => {
        gemeldet = e.detail?.userId ?? null
      }
      window.addEventListener('msm:notes-key-updated', horcher)

      const uebernommen = await altschluesselUebernehmen(18)
      window.removeEventListener('msm:notes-key-updated', horcher)

      expect(uebernommen).toBe(true)
      expect(exportUserNotesKey(18)).toBe(alt)
      expect(gemeldet).toBe(18)
    })

    it('überschreibt niemals einen vorhandenen Schlüssel', async () => {
      await getOrCreateUserNotesKey(1)
      await getOrCreateUserNotesKey(18)
      const eigener = exportUserNotesKey(18)

      expect(await altschluesselUebernehmen(18)).toBe(false)
      expect(exportUserNotesKey(18)).toBe(eigener)
    })

    it('übernimmt nichts auf die Kennung 1 selbst', async () => {
      await getOrCreateUserNotesKey(1)
      expect(await altschluesselUebernehmen(1)).toBe(false)
    })

    it('greift den Altbestand beim Erzeugen NICHT — das waere eine Uebernahme ohne Beleg', async () => {
      // Auf einem geteilten Geraet gehoert der Schluessel unter der Kennung 1
      // dem Konto 1. Wuerde `getOrCreateUserNotesKey` ihn einfach nehmen,
      // bekaeme das zweite Konto beim ersten Speichern den Schluessel des
      // ersten. Der Rueckgriff auf den Altbestand gehoert deshalb ausschliess-
      // lich in den Lesepfad, wo eine geoeffnete eigene Zeile ihn belegt.
      await getOrCreateUserNotesKey(1)
      const alt = exportUserNotesKey(1)

      clearNotesKeyCache()
      await getOrCreateUserNotesKey(18)

      expect(exportUserNotesKey(18)).toBeTruthy()
      expect(exportUserNotesKey(18)).not.toBe(alt)
    })

    it('laesst die Zusage der Primitiven unberuehrt: Kennung 2 oeffnet nichts von Kennung 1', async () => {
      // Dieselbe Zusage wie oben, hier aber ausdruecklich *mit* vorhandenem
      // Altbestand: der Rueckgriff darauf gehoert in die Synchronisierungs-
      // schicht, die nur eigene Zeilen sieht — nie in die Primitive.
      const noteUid = 'altbestand-zusage-1'
      const encTitle = await encryptNoteTitle('Streng vertraulich', noteUid, undefined, 1)
      await getOrCreateUserNotesKey(2)

      await expect(decryptNoteTitle(encTitle, noteUid, undefined, 2)).rejects.toThrow()
    })
  })
})
