import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * Der Server in diesen Tests: ein Konto, ein Bund, eine Version.
 * `setE2eePublicKey` wird mitgezählt, weil der gemeldete Fehler genau darin
 * bestand, dass diese Route ungefragt aufgerufen wurde.
 */
// `vi.mock` wird an den Dateianfang gezogen, deshalb muss der Serverzustand
// über `vi.hoisted` entstehen — sonst existiert er zum Mock-Zeitpunkt noch nicht.
const { server, setE2eePublicKey } = vi.hoisted(() => {
  const zustand = {
    wrapped_keyring: null as string | null,
    public_key: null as string | null,
    version: 0,
  }
  return {
    server: zustand,
    setE2eePublicKey: vi.fn(async (publicKey: string) => {
      zustand.public_key = publicKey
      return { ok: true, message: '' }
    }),
  }
})

vi.mock('@/api/social', () => ({
  getE2eeKeyring: vi.fn(async () => ({ ...server })),
  putE2eeKeyring: vi.fn(
    async (p: { wrappedKeyring: string; publicKey: string; expectedVersion: number }) => {
      if (p.expectedVersion !== server.version) {
        throw new Error('409 Konflikt')
      }
      server.wrapped_keyring = p.wrappedKeyring
      server.public_key = p.publicKey
      server.version += 1
      return { ...server }
    }
  ),
  getE2eePublicKey: vi.fn(async (userId: number) => ({
    user_id: userId,
    username: 'partner',
    public_key: server.public_key,
  })),
  setE2eePublicKey,
}))

import {
  resolveIdentity,
  createIdentity,
  unlockWithRecoveryKey,
  clearIdentityMemory,
  generateRecoveryKey,
  normalizeRecoveryKey,
  wrapKeyring,
  unwrapKeyring,
  requireRecipientPublicKey,
  E2eeRecipientKeyMissingError,
  E2EE_KEYRING_PREFIX,
} from './e2eeIdentity'
import {
  generateLocalE2eeKeyPair,
  encryptE2eeHybrid,
  decryptE2eeHybridWithKeyring,
} from './e2eeCrypto'

const USER_ID = 42

/** Simuliert ein anderes Endgerät: eigener Speicher, derselbe Server. */
function neuesGeraet(): void {
  clearIdentityMemory()
}

beforeEach(() => {
  server.wrapped_keyring = null
  server.public_key = null
  server.version = 0
  setE2eePublicKey.mockClear()
  clearIdentityMemory()
})

describe('Wiederherstellungsschlüssel', () => {
  it('erzeugt Schlüssel aus einem verwechslungsarmen Alphabet', () => {
    const key = generateRecoveryKey()
    expect(normalizeRecoveryKey(key)).toHaveLength(20)
    // Ohne I, O, 0 und 1 — dasselbe Alphabet wie der Kopplungscode der App,
    // weil beide von einem Menschen abgetippt werden.
    expect(key).not.toMatch(/[01IO]/)
    expect(generateRecoveryKey()).not.toBe(key)
  })

  it('liest Bindestriche, Leerzeichen und Kleinbuchstaben als dieselbe Eingabe', () => {
    expect(normalizeRecoveryKey('a2c4e-f7h9j')).toBe('A2C4EF7H9J')
    expect(normalizeRecoveryKey(' A2C4E F7H9J ')).toBe('A2C4EF7H9J')
  })
})

describe('Schlüsselbund-Umschlag', () => {
  it('verpackt und öffnet mit demselben Schlüssel', async () => {
    const account = await generateLocalE2eeKeyPair()
    const key = generateRecoveryKey()

    const envelope = await wrapKeyring({ v: 1, account, legacy: [] }, key)
    expect(envelope.startsWith(E2EE_KEYRING_PREFIX)).toBe(true)
    // Nichts vom privaten Schlüssel steht im Klartext im Umschlag.
    expect(envelope).not.toContain('privateKeyJwk')
    expect(envelope).not.toContain(JSON.parse(account.privateKeyJwk).d)

    const geoeffnet = await unwrapKeyring(envelope, key)
    expect(geoeffnet.account.privateKeyJwk).toBe(account.privateKeyJwk)
  }, 60000)

  it('weist einen falschen Wiederherstellungsschlüssel ab', async () => {
    const account = await generateLocalE2eeKeyPair()
    const envelope = await wrapKeyring({ v: 1, account, legacy: [] }, 'A2C4E-F7H9J-K3M5N-P8Q2R')

    await expect(unwrapKeyring(envelope, 'B2C4E-F7H9J-K3M5N-P8Q2R')).rejects.toThrow()
  }, 60000)
})

describe('Zustand der Identität', () => {
  it('meldet needs-setup, solange der Server keinen Bund hat', async () => {
    const identity = await resolveIdentity(USER_ID)
    expect(identity.state).toBe('needs-setup')
    expect(setE2eePublicKey).not.toHaveBeenCalled()
  })

  it('KERNREGRESSION: fremdes Gerät bekommt locked und veröffentlicht nichts', async () => {
    // Genau der gemeldete Fehler: Gerät A richtet ein, Gerät B öffnet den
    // Messenger. Vorher erzeugte B sich hier ein neues Schlüsselpaar und lud es
    // hoch — danach war der Verlauf auf beiden Geräten unlesbar.
    await createIdentity(USER_ID)
    const publicKeyVonGeraetA = server.public_key
    const versionVonGeraetA = server.version

    neuesGeraet()
    const identity = await resolveIdentity(USER_ID)

    expect(identity.state).toBe('locked')
    expect(identity.sendPair).toBeNull()
    expect(setE2eePublicKey).not.toHaveBeenCalled()
    // Entscheidend: der Kontoschlüssel steht unverändert.
    expect(server.public_key).toBe(publicKeyVonGeraetA)
    expect(server.version).toBe(versionVonGeraetA)
  }, 60000)

  it('bleibt bei einem Netzwerkfehler gesperrt statt Einrichtung anzubieten', async () => {
    const api = await import('@/api/social')
    vi.mocked(api.getE2eeKeyring).mockRejectedValueOnce(new Error('Failed to fetch'))

    const identity = await resolveIdentity(USER_ID)
    // needs-setup wäre hier gefährlich: ein Klick darauf würde den vorhandenen
    // Bund überschreiben.
    expect(identity.state).toBe('locked')
    expect(setE2eePublicKey).not.toHaveBeenCalled()
  })
})

describe('Gerätewechsel', () => {
  it('zwei Geräte lesen mit demselben Wiederherstellungsschlüssel dieselbe Nachricht', async () => {
    const { recoveryKey } = await createIdentity(USER_ID)
    const geraetA = await resolveIdentity(USER_ID)
    expect(geraetA.state).toBe('ready')

    const nachricht = 'Hallo — geschrieben auf dem ersten Gerät'
    const umschlag = await encryptE2eeHybrid(
      nachricht,
      geraetA.sendPair!.publicKeyJwk,
      geraetA.sendPair!.publicKeyJwk
    )

    neuesGeraet()
    expect((await resolveIdentity(USER_ID)).state).toBe('locked')

    const geraetB = await unlockWithRecoveryKey(USER_ID, recoveryKey)
    expect(geraetB.state).toBe('ready')
    expect(geraetB.sendPair!.privateKeyJwk).toBe(geraetA.sendPair!.privateKeyJwk)

    await expect(
      decryptE2eeHybridWithKeyring(umschlag, geraetB.decryptionKeys)
    ).resolves.toBe(nachricht)
  }, 60000)

  it('ein falscher Schlüssel entsperrt nicht und lässt das Gerät gesperrt', async () => {
    await createIdentity(USER_ID)
    neuesGeraet()

    await expect(
      unlockWithRecoveryKey(USER_ID, 'ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ')
    ).rejects.toThrow()

    expect((await resolveIdentity(USER_ID)).state).toBe('locked')
    expect(setE2eePublicKey).not.toHaveBeenCalled()
  }, 60000)
})

describe('Altbestand', () => {
  it('liest eine Nachricht, die unter einem alten Gerätesschlüssel gewrappt wurde', async () => {
    // Der Rettungsfall: vor der Umstellung hatte jedes Gerät ein eigenes Paar.
    const altesGeraetepaar = await generateLocalE2eeKeyPair()
    const alteNachricht = 'Nachricht aus der Zeit der Gerätesschlüssel'
    const alterUmschlag = await encryptE2eeHybrid(
      alteNachricht,
      altesGeraetepaar.publicKeyJwk,
      altesGeraetepaar.publicKeyJwk
    )

    const account = await generateLocalE2eeKeyPair()
    const recoveryKey = generateRecoveryKey()
    const bund = await wrapKeyring(
      { v: 1, account, legacy: [{ ...altesGeraetepaar, adoptedAt: new Date().toISOString() }] },
      recoveryKey
    )
    const geoeffnet = await unwrapKeyring(bund, recoveryKey)
    const schluessel = [
      geoeffnet.account.privateKeyJwk,
      ...geoeffnet.legacy.map((e) => e.privateKeyJwk),
    ]

    // Der Kontoschlüssel allein reicht nicht — erst der Bund macht es lesbar.
    await expect(
      decryptE2eeHybridWithKeyring(alterUmschlag, [account.privateKeyJwk])
    ).rejects.toThrow()
    await expect(decryptE2eeHybridWithKeyring(alterUmschlag, schluessel)).resolves.toBe(
      alteNachricht
    )
  }, 60000)
})

describe('Sendepfad', () => {
  it('wirft, wenn der Empfänger keinen Schlüssel veröffentlicht hat', async () => {
    server.public_key = null
    await expect(requireRecipientPublicKey(999)).rejects.toBeInstanceOf(
      E2eeRecipientKeyMissingError
    )
  })
})

/**
 * Ein IndexedDB-Ersatz, der genau das kann, was `e2eeIdentity.ts` benutzt:
 * öffnen, Stores anlegen, lesen, schreiben. Bewusst im Test statt als neue
 * Abhängigkeit — der Umfang rechtfertigt kein zusätzliches Paket.
 */
function installiereIndexedDbErsatz(): { stores: Map<string, Map<number, any>> } {
  const stores = new Map<string, Map<number, any>>()

  const machObjectStore = (name: string) => ({
    get(key: number) {
      const anfrage: any = { onsuccess: null, onerror: null, result: undefined }
      queueMicrotask(() => {
        anfrage.result = stores.get(name)?.get(key)
        anfrage.onsuccess?.()
      })
      return anfrage
    },
    put(wert: any) {
      const anfrage: any = { onsuccess: null, onerror: null }
      queueMicrotask(() => {
        if (!stores.has(name)) stores.set(name, new Map())
        stores.get(name)!.set(wert.userId, wert)
        anfrage.onsuccess?.()
      })
      return anfrage
    },
  })

  const db: any = {
    objectStoreNames: { contains: (n: string) => stores.has(n) },
    createObjectStore: (n: string) => {
      stores.set(n, new Map())
      return machObjectStore(n)
    },
    transaction: (n: string) => ({ objectStore: () => machObjectStore(n) }),
  }

  ;(globalThis as any).indexedDB = {
    open: () => {
      const anfrage: any = { onsuccess: null, onerror: null, onupgradeneeded: null, result: db }
      queueMicrotask(() => {
        anfrage.onupgradeneeded?.()
        anfrage.onsuccess?.()
      })
      return anfrage
    },
  }

  return { stores }
}

describe('Rettung des Altbestands über den Gerätespeicher', () => {
  it('adoptiert den alten Gerätesschlüssel und überschreibt ihn dabei nicht', async () => {
    const { stores } = installiereIndexedDbErsatz()

    // Gerät A richtet ein und schickt eine Nachricht.
    const { recoveryKey } = await createIdentity(USER_ID)
    const geraetA = await resolveIdentity(USER_ID)

    // Auf Gerät B liegt noch ein Gerätesschlüssel aus der Zeit davor, und eine
    // damals empfangene Nachricht ist nur mit ihm zu öffnen.
    const altesGeraetepaar = await generateLocalE2eeKeyPair()
    stores.set('keys', new Map([[USER_ID, { userId: USER_ID, ...altesGeraetepaar }]]))
    const alteNachricht = 'Nachricht von vor der Umstellung'
    const alterUmschlag = await encryptE2eeHybrid(
      alteNachricht,
      altesGeraetepaar.publicKeyJwk,
      altesGeraetepaar.publicKeyJwk
    )

    neuesGeraet()
    const geraetB = await unlockWithRecoveryKey(USER_ID, recoveryKey)

    // Der Kontoschlüssel ist da …
    expect(geraetB.sendPair!.privateKeyJwk).toBe(geraetA.sendPair!.privateKeyJwk)
    // … und der Altschlüssel wurde adoptiert, sonst bliebe die Nachricht stumm.
    await expect(decryptE2eeHybridWithKeyring(alterUmschlag, geraetB.decryptionKeys)).resolves.toBe(
      alteNachricht
    )

    // Das Archiv bleibt unangetastet. Würde der Kontoschlüssel hier
    // hineingeschrieben, wäre das einzige Material für den alten Verlauf weg.
    expect(stores.get('keys')!.get(USER_ID).privateKeyJwk).toBe(altesGeraetepaar.privateKeyJwk)

    // Der Altschlüssel liegt jetzt auch beim Server, also lesen ihn alle Geräte.
    expect(server.wrapped_keyring).not.toBeNull()
    expect(server.version).toBe(2)
  }, 120000)
})
