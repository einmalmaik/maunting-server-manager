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
  bestaetigeGeraetFuerNotizenSchluessel,
  istGeraetBestaetigtFuerNotizenSchluessel,
} from './notesCalendarCrypto'
import * as socialApi from '@/api/social'
import * as e2eeGeraet from './e2eeGeraet'
import { erzeugeSignaturPaar, signiere, type SignaturPaar } from './absenderSignatur'
import {
  decryptE2eeHybrid,
  deriveUserDeviceMailboxId,
  encryptE2eeHybrid,
  generateLocalE2eeKeyPair,
} from './e2eeCrypto'

const { verzeichnis, netz } = vi.hoisted(() => ({
  /** Was der Server auf die Frage nach den Geräten eines Kontos antwortet. */
  verzeichnis: new Map<
    number,
    { device_id: string; public_key: string; signing_public_key: string; label: string }[]
  >(),
  /** Das Verzeichnis ist nicht zu erreichen, solange das hier gesetzt ist. */
  netz: { aus: false },
}))

vi.mock('@/api/social', () => ({
  relayE2eeEnvelope: vi.fn().mockResolvedValue({ id: 999, blind_mailbox_id: 'box-1' }),
  fetchE2eeEnvelopes: vi.fn().mockResolvedValue([]),
  getE2eeGeraete: vi.fn(async (uid: number) => {
    if (netz.aus) throw new Error('Netzwerk weg')
    return [...(verzeichnis.get(uid) ?? [])]
  }),
}))

// Erfunden ist nur, welches Gerät gerade „dieses" ist und an welche Geräte der
// Versand geht. Ob eine Übergabe von einem eigenen Gerät stammt, prüft das
// echte `e2eeGeraet` — mit echten ECDSA-Paaren, gegen das Verzeichnis oben.
vi.mock('./e2eeGeraet', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./e2eeGeraet')>()),
  eigenesGeraet: vi.fn(),
  geraeteVon: vi.fn(),
}))

describe('notesCalendarCrypto E2EE', () => {
  let selfPair: any
  let otherPair: any
  let reqPair: any
  let selfSig: SignaturPaar
  let otherSig: SignaturPaar
  let reqSig: SignaturPaar

  beforeAll(async () => {
    selfPair = await generateLocalE2eeKeyPair()
    otherPair = await generateLocalE2eeKeyPair()
    reqPair = await generateLocalE2eeKeyPair()
    selfSig = await erzeugeSignaturPaar()
    otherSig = await erzeugeSignaturPaar()
    reqSig = await erzeugeSignaturPaar()
  }, 45000)

  beforeEach(() => {
    vi.clearAllMocks()
    clearNotesKeyCache()
    e2eeGeraet.clearGeraeteMemory()
    verzeichnis.clear()
    netz.aus = false
    if (typeof localStorage !== 'undefined') {
      localStorage.clear()
    }
  })

  /** Trägt ein Gerät ins Verzeichnis eines Kontos ein. Ohne `signatur` ein Gerät aus der Zeit davor. */
  function eintragen(
    konto: number,
    kennung: string,
    paar: { publicKeyJwk: string },
    signatur?: SignaturPaar,
  ) {
    const liste = (verzeichnis.get(konto) ?? []).filter((g) => g.device_id !== kennung)
    liste.push({
      device_id: kennung,
      public_key: paar.publicKeyJwk,
      signing_public_key: signatur?.publicKeyJwk ?? '',
      label: kennung,
    })
    verzeichnis.set(konto, liste)
  }

  /**
   * Trägt ein Gerät ein, das ein vorhandenes freigegeben hat. Ohne diese
   * Unterschrift gäbe ihm kein Client etwas, der das Konto schon kennt
   * (`vertrauteGeraete`).
   */
  async function eintragenFreigegeben(
    konto: number,
    kennung: string,
    paar: { publicKeyJwk: string },
    signatur: SignaturPaar,
    von: string,
    vonSignatur: SignaturPaar,
  ) {
    eintragen(konto, kennung, paar, signatur)
    const eintrag = verzeichnis.get(konto)!.find((g) => g.device_id === kennung)!
    eintrag.approved_by = von
    eintrag.approval_signature = await signiere(
      await e2eeGeraet.freigabeDaten(konto, kennung, paar.publicKeyJwk, signatur.publicKeyJwk),
      vonSignatur.privateKeyJwk,
    )
  }

  /** Ab jetzt ist „dieses Gerät" das genannte. */
  function binIch(kennung: string, paar: any, signaturPaar: SignaturPaar) {
    vi.mocked(e2eeGeraet.eigenesGeraet).mockResolvedValue({ kennung, paar, signaturPaar } as any)
  }

  /** Was bisher an das Relais ging, in der Reihenfolge. */
  function versandt(): { ciphertext_envelope: string; control_type?: string }[] {
    return vi.mocked(socialApi.relayE2eeEnvelope).mock.calls.map((aufruf) => aufruf[0] as any)
  }

  async function oeffnet(umschlag: string, paar: { privateKeyJwk: string }): Promise<string | null> {
    try {
      return await decryptE2eeHybrid(umschlag, paar.privateKeyJwk)
    } catch {
      return null
    }
  }

  /** Vergisst jeden Notizschlüssel: das nächste Gerät fängt ohne an. */
  function leererSchluesselspeicher() {
    clearNotesKeyCache()
    if (typeof localStorage !== 'undefined') localStorage.clear()
  }

  /**
   * Eine echte Übergabe von `dev-other-<konto>` an `dev-self-<konto>`: durch den
   * Versandweg gebaut, nicht von Hand. Danach ist „dieses Gerät" das
   * Zielgerät, und es hat noch keinen Schlüssel.
   */
  async function uebergabeVomZweitgeraet(konto: number, schluessel: string): Promise<string> {
    eintragen(konto, `dev-self-${konto}`, selfPair, selfSig)
    eintragen(konto, `dev-other-${konto}`, otherPair, otherSig)
    await setUserNotesKey(konto, schluessel)
    binIch(`dev-other-${konto}`, otherPair, otherSig)
    vi.mocked(e2eeGeraet.geraeteVon).mockResolvedValue(verzeichnis.get(konto) as any)
    expect(await syncNotesKeyToPairedDevices(konto)).toBe(1)
    const [umschlag] = versandt().slice(-1)

    leererSchluesselspeicher()
    binIch(`dev-self-${konto}`, selfPair, selfSig)
    vi.mocked(socialApi.relayE2eeEnvelope).mockClear()
    return umschlag.ciphertext_envelope
  }

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

    binIch('dev-self-102', selfPair, selfSig)
    vi.mocked(e2eeGeraet.geraeteVon).mockResolvedValue([
      { device_id: 'dev-self-102', public_key: selfPair.publicKeyJwk, signing_public_key: '', label: 'Main PC' },
      { device_id: 'dev-other-102', public_key: otherPair.publicKeyJwk, signing_public_key: '', label: 'Secondary Mobile' },
    ])

    const count = await syncNotesKeyToPairedDevices(userId)
    expect(count).toBe(1)
    expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
      expect.objectContaining({
        // Die Mailbox ist die Adresse: seit Stufe 4 nimmt das Relais keine
        // Empfaengerkennung mehr entgegen.
        blind_mailbox_id: await deriveUserDeviceMailboxId(userId),
        is_control: true,
        control_type: 'notes_key_sync',
      })
    )
  })

  it('processNotesKeyControlEnvelope decrypts notes_key_sync, sets key, and triggers msm:notes-key-updated', async () => {
    const userId = 103
    const raw32 = new Uint8Array(32).fill(99)
    const rawB64 = btoa(String.fromCharCode(...raw32))
    const env = await uebergabeVomZweitgeraet(userId, rawB64)

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

    binIch('dev-self-104', selfPair, selfSig)
    eintragen(userId, 'dev-self-104', selfPair, selfSig)
    eintragen(userId, 'dev-requester-104', reqPair, reqSig)

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
        // Die Mailbox ist die Adresse: seit Stufe 4 nimmt das Relais keine
        // Empfaengerkennung mehr entgegen.
        blind_mailbox_id: await deriveUserDeviceMailboxId(userId),
        is_control: true,
        control_type: 'notes_key_sync',
      })
    )
    const antwort = await oeffnet(versandt()[0].ciphertext_envelope, reqPair)
    expect(JSON.parse(antwort!).notesKey).toBe(rawB64)
  })

  it('checkAndReceiveDeviceNotesKey queries device mailbox and receives key', async () => {
    const userId = 105
    const raw32 = new Uint8Array(32).fill(77)
    const rawB64 = btoa(String.fromCharCode(...raw32))
    const env = await uebergabeVomZweitgeraet(userId, rawB64)

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

    binIch('dev-self-107', selfPair, selfSig)

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

    binIch('dev-self-108', selfPair, selfSig)
    eintragen(userId, 'dev-self-108', selfPair, selfSig)
    eintragen(userId, 'dev-requester-108', reqPair, reqSig)

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
        // Die Mailbox ist die Adresse: seit Stufe 4 nimmt das Relais keine
        // Empfaengerkennung mehr entgegen.
        blind_mailbox_id: await deriveUserDeviceMailboxId(userId),
        is_control: true,
        control_type: 'notes_key_sync',
      })
    )
  })

  // ── Übergabe nur unter eigenen Geräten (bis 09/2026 offen) ──
  //
  // In die eigene Geräte-Mailbox darf jeder Freund, jedes Gruppenmitglied und
  // jedes Gegenüber eines Direktchats Steuerumschläge legen. Ein Umschlag, der
  // sich öffnen lässt, beweist deshalb nur, *für* wen er ist — nie, von wem.

  describe('Übergabe nur unter eigenen Geräten', () => {
    const KONTO = 201
    const FREMDES_KONTO = 999
    const schluessel = (fuellung: number) =>
      btoa(String.fromCharCode(...new Uint8Array(32).fill(fuellung)))

    /** Ein Umschlag, wie ihn ein Fremder in die Geräte-Mailbox legt. */
    async function untergeschoben(inhalt: Record<string, unknown>): Promise<string> {
      return encryptE2eeHybrid(JSON.stringify(inhalt), selfPair.publicKeyJwk)
    }

    beforeEach(() => {
      eintragen(KONTO, 'dev-self-201', selfPair, selfSig)
      eintragen(KONTO, 'dev-other-201', otherPair, otherSig)
      // Der Fragende hat ein eigenes Konto und ein echtes Gerät darin.
      eintragen(FREMDES_KONTO, 'dev-fremd', reqPair, reqSig)
      binIch('dev-self-201', selfPair, selfSig)
    })

    it('versiegelt die Antwort gegen das Verzeichnis, nicht gegen den Schlüssel aus der Anfrage', async () => {
      await setUserNotesKey(KONTO, schluessel(1))

      // Die Anfrage nennt ein eigenes Gerät, bringt aber den Schlüssel des Fragenden mit.
      await processNotesKeyControlEnvelope(
        await untergeschoben({
          type: 'notes_key_request',
          version: 1,
          userId: KONTO,
          requesterDeviceId: 'dev-other-201',
          requesterPublicKey: reqPair.publicKeyJwk,
          timestamp: Date.now(),
        }),
        KONTO,
      )

      const antworten = versandt()
      expect(antworten).toHaveLength(1)
      expect(await oeffnet(antworten[0].ciphertext_envelope, reqPair)).toBeNull()
      const fuerMich = await oeffnet(antworten[0].ciphertext_envelope, otherPair)
      expect(JSON.parse(fuerMich!).notesKey).toBe(schluessel(1))
    })

    it('antwortet keinem Gerät, das nicht zum eigenen Konto gehört', async () => {
      await setUserNotesKey(KONTO, schluessel(2))

      const beantwortet = await processNotesKeyControlEnvelope(
        await untergeschoben({
          type: 'notes_key_request',
          version: 1,
          userId: KONTO,
          requesterDeviceId: 'dev-fremd',
          requesterPublicKey: reqPair.publicKeyJwk,
          timestamp: Date.now(),
        }),
        KONTO,
      )

      expect(beantwortet).toBe(false)
      expect(versandt()).toHaveLength(0)
    })

    it('gibt den Schlüssel eines anderen Kontos auf diesem Gerät nicht heraus', async () => {
      // Ein geteiltes Gerät: dort liegt auch der Schlüssel eines zweiten Kontos.
      await setUserNotesKey(KONTO, schluessel(3))
      await setUserNotesKey(FREMDES_KONTO, schluessel(4))
      eintragen(FREMDES_KONTO, 'dev-other-201', otherPair, otherSig)

      const beantwortet = await processNotesKeyControlEnvelope(
        await untergeschoben({
          type: 'notes_key_request',
          version: 1,
          userId: FREMDES_KONTO,
          requesterDeviceId: 'dev-other-201',
          requesterPublicKey: otherPair.publicKeyJwk,
          timestamp: Date.now(),
        }),
        KONTO,
      )

      expect(beantwortet).toBe(false)
      expect(versandt()).toHaveLength(0)
    })

    it('übernimmt keinen Schlüssel ohne Unterschrift, wenn das Konto unterschreibt', async () => {
      await processNotesKeyControlEnvelope(
        await untergeschoben({
          type: 'notes_key_sync',
          version: 1,
          userId: KONTO,
          notesKey: schluessel(5),
          targetDeviceId: 'dev-self-201',
          senderDeviceId: 'dev-other-201',
          timestamp: Date.now(),
        }),
        KONTO,
      )

      expect(hasUserNotesKey(KONTO)).toBe(false)
    })

    it('übernimmt keinen Schlüssel, dessen Unterschrift nicht zum genannten Gerät passt', async () => {
      await processNotesKeyControlEnvelope(
        await untergeschoben({
          type: 'notes_key_sync',
          version: 1,
          userId: KONTO,
          notesKey: schluessel(6),
          targetDeviceId: 'dev-self-201',
          senderDeviceId: 'dev-other-201',
          timestamp: Date.now(),
          sig: await signiere('ein beliebiger Text', reqSig.privateKeyJwk),
        }),
        KONTO,
      )

      expect(hasUserNotesKey(KONTO)).toBe(false)
    })

    it('legt keinen Schlüssel unter einem anderen Konto ab', async () => {
      // Auch mit echter Unterschrift eines eigenen Geräts: die Kontokennung im
      // Umschlag bestimmt nicht, wessen Schlüssel das wird.
      const umschlag = await uebergabeVomZweitgeraet(KONTO, schluessel(7))
      const roh = JSON.parse((await oeffnet(umschlag, selfPair))!)

      await processNotesKeyControlEnvelope(
        await untergeschoben({ ...roh, userId: FREMDES_KONTO }),
        KONTO,
      )

      expect(hasUserNotesKey(FREMDES_KONTO)).toBe(false)
      expect(hasUserNotesKey(KONTO)).toBe(false)
    })

    it('nimmt eine echte Übergabe eines eigenen Geräts an', async () => {
      const umschlag = await uebergabeVomZweitgeraet(KONTO, schluessel(8))

      expect(await processNotesKeyControlEnvelope(umschlag, KONTO)).toBe(true)
      expect(exportUserNotesKey(KONTO)).toBe(schluessel(8))
    })

    it('beantwortet eine Anfrage so, dass der Fragende die Antwort annimmt', async () => {
      // Der ganze Weg: Anfrage vom neuen Gerät, Antwort vom alten, Übernahme.
      binIch('dev-other-201', otherPair, otherSig)
      vi.mocked(e2eeGeraet.geraeteVon).mockResolvedValue(verzeichnis.get(KONTO) as any)
      expect(await requestNotesKeyFromPairedDevices(KONTO)).toBe(true)
      const [anfrage] = versandt()

      await setUserNotesKey(KONTO, schluessel(9))
      binIch('dev-self-201', selfPair, selfSig)
      vi.mocked(socialApi.relayE2eeEnvelope).mockClear()
      expect(await processNotesKeyControlEnvelope(anfrage.ciphertext_envelope, KONTO)).toBe(true)
      const [antwort] = versandt()

      leererSchluesselspeicher()
      binIch('dev-other-201', otherPair, otherSig)
      expect(await processNotesKeyControlEnvelope(antwort.ciphertext_envelope, KONTO)).toBe(true)
      expect(exportUserNotesKey(KONTO)).toBe(schluessel(9))
    })

    it('baut Umschlagkennungen, die das Relais annimmt', async () => {
      // Das Relais nimmt höchstens 64 Zeichen (`client_uuid` in
      // `backend/schemas/social.py`). Mit zwei vollen Gerätekennungen zu je 32
      // Zeichen wurden es 91 — jede Anfrage und jede Übergabe scheiterte mit
      // 422, und der Schlüssel wanderte nie. Die Tests davor hatten kurze
      // Kennungen und ein Relais, das alles annimmt.
      const hier = 'd'.repeat(32)
      const dort = 'e'.repeat(32)
      eintragen(KONTO, hier, selfPair, selfSig)
      eintragen(KONTO, dort, otherPair, otherSig)
      vi.mocked(e2eeGeraet.geraeteVon).mockResolvedValue(verzeichnis.get(KONTO) as any)

      binIch(dort, otherPair, otherSig)
      expect(await requestNotesKeyFromPairedDevices(KONTO)).toBe(true)
      const anfragen = versandt()

      await setUserNotesKey(KONTO, schluessel(11))
      binIch(hier, selfPair, selfSig)
      for (const anfrage of anfragen) {
        await processNotesKeyControlEnvelope(anfrage.ciphertext_envelope, KONTO)
      }
      expect(await syncNotesKeyToPairedDevices(KONTO)).toBeGreaterThan(0)

      const kennungen = vi
        .mocked(socialApi.relayE2eeEnvelope)
        .mock.calls.map(([auftrag]) => String((auftrag as any).client_uuid))
      expect(kennungen.length).toBeGreaterThan(anfragen.length)
      for (const kennung of kennungen) expect(kennung.length).toBeLessThanOrEqual(64)
    })

    it('entscheidet nichts, solange das Verzeichnis nicht antwortet', async () => {
      const umschlag = await uebergabeVomZweitgeraet(KONTO, schluessel(10))

      netz.aus = true
      await processNotesKeyControlEnvelope(umschlag, KONTO)
      expect(hasUserNotesKey(KONTO)).toBe(false)

      // Kein Nein, das hängen bleibt: derselbe Umschlag geht beim nächsten Mal durch.
      netz.aus = false
      expect(await processNotesKeyControlEnvelope(umschlag, KONTO)).toBe(true)
      expect(exportUserNotesKey(KONTO)).toBe(schluessel(10))
    })

    it('nimmt Altbestand ohne Unterschrift an, solange niemand im Konto unterschreiben kann', async () => {
      verzeichnis.clear()
      eintragen(KONTO, 'dev-self-201', selfPair)
      eintragen(KONTO, 'dev-other-201', otherPair)

      await processNotesKeyControlEnvelope(
        await untergeschoben({
          type: 'notes_key_sync',
          version: 1,
          userId: KONTO,
          notesKey: schluessel(11),
          targetDeviceId: 'dev-self-201',
          senderDeviceId: 'dev-other-201',
          timestamp: Date.now(),
        }),
        KONTO,
      )

      expect(exportUserNotesKey(KONTO)).toBe(schluessel(11))
    })
  })

  // ── Abgleich ohne Flut (bis 09/2026 unbemerkt) ──
  //
  // Solange das Relais jede Anfrage und jede Übergabe wegen der zu langen
  // Umschlagkennung abwies, fiel nicht auf, wie oft sie gesendet wurden. In der
  // ersten Laufzeitprobe danach waren es über sechzig Umschläge in der Minute:
  // jeder Abruf beantwortete jede Anfrage in der Mailbox aufs Neue, und jedes
  // Laden von Notizen oder Kalender schickte den Schlüssel noch einmal an jedes
  // eigene Gerät. Das Relais zählt je Adresse — danach bekamen auch echte
  // Nachrichten ein 429.

  describe('Abgleich ohne Flut', () => {
    const KONTO = 202
    const schluessel = (fuellung: number) =>
      btoa(String.fromCharCode(...new Uint8Array(32).fill(fuellung)))

    beforeEach(() => {
      eintragen(KONTO, 'dev-self-202', selfPair, selfSig)
      eintragen(KONTO, 'dev-other-202', otherPair, otherSig)
      binIch('dev-self-202', selfPair, selfSig)
      vi.mocked(e2eeGeraet.geraeteVon).mockImplementation(
        async (konto: number) => [...(verzeichnis.get(konto) ?? [])] as any,
      )
    })

    afterEach(() => {
      vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([])
    })

    /** Die Geräte-Mailbox, wie sie jeder Abruf bis zum Ende des Tests liefert. */
    function postfach(umschlaege: { id: number; ciphertext_envelope: string }[]) {
      vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue(
        umschlaege.map((u) => ({
          ...u,
          blind_mailbox_id: 'geraete-202',
          created_at: new Date().toISOString(),
        })) as any,
      )
    }

    /** Die Anfrage des anderen Geräts, versiegelt für dieses. */
    function anfrageVomAnderen(): Promise<string> {
      return encryptE2eeHybrid(
        JSON.stringify({
          type: 'notes_key_request',
          version: 1,
          userId: KONTO,
          requesterDeviceId: 'dev-other-202',
          timestamp: Date.now(),
        }),
        selfPair.publicKeyJwk,
      )
    }

    /** Ein Neuladen: was das Modul im Speicher hält, ist weg — die Ablage bleibt. */
    function neuLaden() {
      clearNotesKeyCache()
      e2eeGeraet.clearGeraeteMemory()
    }

    it('beantwortet dieselbe Anfrage nur einmal — auch nach dem Neuladen', async () => {
      await setUserNotesKey(KONTO, schluessel(20))
      postfach([{ id: 801, ciphertext_envelope: await anfrageVomAnderen() }])

      expect(await checkAndRespondToDeviceKeyRequests(KONTO)).toBe(1)
      expect(await checkAndRespondToDeviceKeyRequests(KONTO)).toBe(0)
      neuLaden()
      expect(await checkAndRespondToDeviceKeyRequests(KONTO)).toBe(0)
      expect(versandt()).toHaveLength(1)
    })

    it('hält eine gescheiterte Antwort nicht für erledigt', async () => {
      // Genau der Fall, in dem das Relais 429 sagt: die Anfrage bleibt offen
      // und wird beim nächsten Abruf beantwortet — dann aber nur einmal.
      await setUserNotesKey(KONTO, schluessel(21))
      postfach([{ id: 802, ciphertext_envelope: await anfrageVomAnderen() }])
      vi.mocked(socialApi.relayE2eeEnvelope).mockRejectedValueOnce(new Error('429 Too Many Requests'))

      expect(await checkAndRespondToDeviceKeyRequests(KONTO)).toBe(0)
      expect(await checkAndRespondToDeviceKeyRequests(KONTO)).toBe(1)
      expect(await checkAndRespondToDeviceKeyRequests(KONTO)).toBe(0)
    })

    it('gibt den Schlüssel jedem Gerät einmal — und einen neuen wieder allen', async () => {
      await setUserNotesKey(KONTO, schluessel(22))

      expect(await syncNotesKeyToPairedDevices(KONTO)).toBe(1)
      expect(await syncNotesKeyToPairedDevices(KONTO)).toBe(0)
      neuLaden()
      expect(await syncNotesKeyToPairedDevices(KONTO)).toBe(0)

      // Ein Gerät, das neu dazukommt, bekommt ihn — nur dieses.
      eintragen(KONTO, 'dev-third-202', reqPair, reqSig)
      expect(await syncNotesKeyToPairedDevices(KONTO)).toBe(1)

      await setUserNotesKey(KONTO, schluessel(23))
      expect(await syncNotesKeyToPairedDevices(KONTO)).toBe(2)
      expect(versandt()).toHaveLength(4)
    })

    it('beantwortet die Anfrage eines Geräts, das die eben geholte Liste noch nicht führte', async () => {
      // Laufzeitprobe 23.09.: das neue Gerät fragte eine Sekunde nach seiner
      // Anmeldung, und die Liste der anderen war jünger als `FRISCH_MS` — der
      // frische Abruf holte also nichts nach. Dieses „nicht gefunden" als
      // endgültig zu vermerken hieß: diese Anfrage nie mehr beantworten.
      await setUserNotesKey(KONTO, schluessel(28))
      const anfrage = await encryptE2eeHybrid(
        JSON.stringify({
          type: 'notes_key_request',
          version: 1,
          userId: KONTO,
          requesterDeviceId: 'dev-new-202',
          timestamp: Date.now(),
        }),
        selfPair.publicKeyJwk,
      )
      postfach([{ id: 804, ciphertext_envelope: anfrage }])

      expect(await checkAndRespondToDeviceKeyRequests(KONTO)).toBe(0)
      expect(versandt()).toHaveLength(0)

      await eintragenFreigegeben(KONTO, 'dev-new-202', reqPair, reqSig, 'dev-self-202', selfSig)
      e2eeGeraet.clearGeraeteMemory()
      expect(await checkAndRespondToDeviceKeyRequests(KONTO)).toBe(1)
      expect(await oeffnet(versandt()[0].ciphertext_envelope, reqPair)).not.toBeNull()
    })

    it('beantwortet ein fragendes Gerät höchstens einmal je Pause, wie viele Anfragen auch kommen', async () => {
      // Durchsicht vom 23.09.: in die Geräte-Mailbox darf jeder Freund und
      // jedes Gruppenmitglied einwerfen. Jede Anfrage, die ein echtes eigenes
      // Gerät nennt, bekam ihre eigene Antwort — hundert gefälschte in der
      // Minute, und das Relais wies danach auch die echten Nachrichten mit 429
      // ab. Verraten hätte die Antwort nichts; sie ist gegen das Verzeichnis
      // versiegelt. Es ging um die Last.
      await setUserNotesKey(KONTO, schluessel(30))
      postfach([
        { id: 811, ciphertext_envelope: await anfrageVomAnderen() },
        { id: 812, ciphertext_envelope: await anfrageVomAnderen() },
        { id: 813, ciphertext_envelope: await anfrageVomAnderen() },
      ])
      expect(await checkAndRespondToDeviceKeyRequests(KONTO)).toBe(1)
      expect(versandt()).toHaveLength(1)

      const spaeter = Date.now() + 11 * 60 * 1000
      const uhr = vi.spyOn(Date, 'now').mockReturnValue(spaeter)
      try {
        postfach([{ id: 814, ciphertext_envelope: await anfrageVomAnderen() }])
        expect(await checkAndRespondToDeviceKeyRequests(KONTO)).toBe(1)
      } finally {
        uhr.mockRestore()
      }
      expect(versandt()).toHaveLength(2)
    })

    it('liest nach, wenn während eines Durchgangs eine Anfrage eintrifft', async () => {
      // Durchsicht vom 23.09.: wer sich einem laufenden Durchgang anschloss,
      // bekam dessen Ergebnis — auch wenn seine Anfrage erst nach dessen Abruf
      // eingetroffen war. Sie blieb liegen bis zum nächsten Anlass.
      await setUserNotesKey(KONTO, schluessel(31))
      let freigeben!: () => void
      const halt = new Promise<void>((weiter) => (freigeben = weiter))
      postfach([{ id: 815, ciphertext_envelope: await anfrageVomAnderen() }])
      vi.mocked(socialApi.fetchE2eeEnvelopes).mockImplementationOnce(async () => {
        await halt
        return []
      })

      const erster = checkAndRespondToDeviceKeyRequests(KONTO)
      await vi.waitFor(() => expect(socialApi.fetchE2eeEnvelopes).toHaveBeenCalledTimes(1))
      const zweiter = checkAndRespondToDeviceKeyRequests(KONTO)
      freigeben()
      await Promise.all([erster, zweiter])

      expect(versandt()).toHaveLength(1)
    })

    it('hält die Pause nicht, wenn keine Anfrage hinausging', async () => {
      // Durchsicht vom 23.09.: scheiterte jede Anfrage einer Runde — etwa am
      // 429 des Relais —, blieb die Marke trotzdem stehen, und ein Gerät ohne
      // Schlüssel wartete zehn Minuten auf nichts.
      binIch('dev-other-202', otherPair, otherSig)
      vi.mocked(socialApi.relayE2eeEnvelope).mockRejectedValueOnce(new Error('429 Too Many Requests'))

      expect(await requestNotesKeyFromPairedDevices(KONTO)).toBe(false)
      expect(await requestNotesKeyFromPairedDevices(KONTO)).toBe(true)
    })

    it('gibt nicht doppelt, wenn zwei Ladevorgänge zugleich abgleichen', async () => {
      // Laufzeitprobe 23.09.: Erinnerungen und Messenger laden gemeinsam, und
      // jedes Gerät bekam den Schlüssel zweimal in derselben Sekunde — beide
      // Aufrufe sahen es als „noch nicht gegeben", bevor einer es vermerkte.
      await setUserNotesKey(KONTO, schluessel(26))

      const [erster, zweiter] = await Promise.all([
        syncNotesKeyToPairedDevices(KONTO),
        syncNotesKeyToPairedDevices(KONTO),
      ])

      expect(erster + zweiter).toBeGreaterThan(0)
      expect(versandt()).toHaveLength(1)
    })

    it('beantwortet eine Anfrage nicht doppelt, wenn zwei Abrufe zugleich laufen', async () => {
      await setUserNotesKey(KONTO, schluessel(27))
      postfach([{ id: 803, ciphertext_envelope: await anfrageVomAnderen() }])

      await Promise.all([
        checkAndRespondToDeviceKeyRequests(KONTO),
        checkAndRespondToDeviceKeyRequests(KONTO),
      ])

      expect(versandt()).toHaveLength(1)
    })

    it('fragt höchstens einmal je Pause nach, auch wenn viele Ladevorgänge zugleich fragen', async () => {
      binIch('dev-other-202', otherPair, otherSig)

      const runde = await Promise.all([1, 2, 3].map(() => requestNotesKeyFromPairedDevices(KONTO)))
      expect(runde.filter(Boolean)).toHaveLength(1)
      expect(await requestNotesKeyFromPairedDevices(KONTO)).toBe(false)
      expect(versandt()).toHaveLength(1)

      const spaeter = Date.now() + 11 * 60 * 1000
      const uhr = vi.spyOn(Date, 'now').mockReturnValue(spaeter)
      try {
        expect(await requestNotesKeyFromPairedDevices(KONTO)).toBe(true)
      } finally {
        uhr.mockRestore()
      }
      expect(versandt()).toHaveLength(2)
    })

    it('fragt nicht bei jedem Laden ohne Schlüssel neu', async () => {
      binIch('dev-other-202', otherPair, otherSig)

      for (let i = 0; i < 3; i++) await checkAndReceiveDeviceNotesKey(KONTO)
      await vi.waitFor(() => expect(versandt().length).toBeGreaterThan(0))
      await new Promise((fertig) => setTimeout(fertig, 150))

      expect(versandt()).toHaveLength(1)
    })

    it('verteidigt den eigenen Schlüssel einmal, nicht bei jedem Umschlag', async () => {
      // Zwei Geräte mit verschiedenen Schlüsseln — etwa aus der Zeit, als die
      // Übergabe nie ankam. Jede Übergabe des anderen beantwortete dieses
      // Gerät mit dem eigenen Schlüssel an alle, und das andere tat dasselbe.
      binIch('dev-other-202', otherPair, otherSig)
      await setUserNotesKey(KONTO, schluessel(24))
      expect(await syncNotesKeyToPairedDevices(KONTO)).toBe(1)
      const [fremd] = versandt()

      leererSchluesselspeicher()
      binIch('dev-self-202', selfPair, selfSig)
      await setUserNotesKey(KONTO, schluessel(25))
      vi.mocked(socialApi.relayE2eeEnvelope).mockClear()

      expect(await processNotesKeyControlEnvelope(fremd.ciphertext_envelope, KONTO)).toBe(true)
      expect(await processNotesKeyControlEnvelope(fremd.ciphertext_envelope, KONTO)).toBe(true)
      await new Promise((fertig) => setTimeout(fertig, 150))

      expect(exportUserNotesKey(KONTO)).toBe(schluessel(25))
      expect(versandt()).toHaveLength(1)
    })
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

  describe('Vorfall 7: Schutz vor unterschobenen Geräten', () => {
    it('gibt den Notizenschlüssel NICHT an ein vom Server als freigegeben gemeldetes Gerät, das nicht von uns freigegeben wurde', async () => {
      const userId = 501
      const raw32 = new Uint8Array(32).fill(99)
      const rawB64 = btoa(String.fromCharCode(...raw32))
      await setUserNotesKey(userId, rawB64)

      binIch('dev-self-501', selfPair, selfSig)
      eintragen(userId, 'dev-self-501', selfPair, selfSig)

      // Ein Gerät, das der Serverbetreiber mit is_approved = true, aber fremdem approved_by unterschiebt
      eintragen(userId, 'dev-rogue-501', reqPair, reqSig)
      const rogueEntry = verzeichnis.get(userId)!.find((g) => g.device_id === 'dev-rogue-501')!
      ;(rogueEntry as any).is_approved = true
      ;(rogueEntry as any).approved_by = 'dev-attacker-unbekannt'

      let pendingDispatched = false
      const onPending = () => {
        pendingDispatched = true
      }
      window.addEventListener('msm:notes-key-request-pending', onPending)

      const reqPayload = JSON.stringify({
        type: 'notes_key_request',
        version: 1,
        userId,
        requesterDeviceId: 'dev-rogue-501',
        timestamp: Date.now(),
      })
      const env = await encryptE2eeHybrid(reqPayload, selfPair.publicKeyJwk)

      vi.mocked(socialApi.relayE2eeEnvelope).mockClear()
      const processed = await processNotesKeyControlEnvelope(env, userId)

      // Anfrage bleibt offen, Schlüssel wurde NICHT versandt
      expect(processed).toBe(false)
      expect(socialApi.relayE2eeEnvelope).not.toHaveBeenCalled()
      expect(pendingDispatched).toBe(true)

      window.removeEventListener('msm:notes-key-request-pending', onPending)

      // Sobald der Benutzer das Gerät lokal für den Notizenschlüssel bestätigt:
      bestaetigeGeraetFuerNotizenSchluessel(userId, 'dev-rogue-501')
      expect(istGeraetBestaetigtFuerNotizenSchluessel(userId, 'dev-rogue-501')).toBe(true)

      const processedAfterConfirm = await processNotesKeyControlEnvelope(env, userId)
      expect(processedAfterConfirm).toBe(true)
      expect(socialApi.relayE2eeEnvelope).toHaveBeenCalled()
    })

    it('syncNotesKeyToPairedDevices überspringt Geräte mit is_approved: false oder unbestätigte freigegebene Geräte', async () => {
      const userId = 502
      await setUserNotesKey(userId, btoa(String.fromCharCode(...new Uint8Array(32).fill(77))))
      binIch('dev-self-502', selfPair, selfSig)
      eintragen(userId, 'dev-self-502', selfPair, selfSig)

      // Unapproved device
      eintragen(userId, 'dev-pending-502', otherPair, otherSig)
      const pendingEntry = verzeichnis.get(userId)!.find((g) => g.device_id === 'dev-pending-502')!
      ;(pendingEntry as any).is_approved = false

      // Rogue server-approved device without our approval
      eintragen(userId, 'dev-rogue-502', reqPair, reqSig)
      const rogueEntry = verzeichnis.get(userId)!.find((g) => g.device_id === 'dev-rogue-502')!
      ;(rogueEntry as any).is_approved = true
      ;(rogueEntry as any).approved_by = 'other-device'

      vi.mocked(e2eeGeraet.geraeteVon).mockResolvedValue(verzeichnis.get(userId)! as any)
      vi.mocked(socialApi.relayE2eeEnvelope).mockClear()

      const sent = await syncNotesKeyToPairedDevices(userId)
      expect(sent).toBe(0)
      expect(socialApi.relayE2eeEnvelope).not.toHaveBeenCalled()
    })
  })
})
