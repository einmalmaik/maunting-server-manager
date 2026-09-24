import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import { MemoryRouter } from 'react-router-dom'
import { Messenger, clearSessionChatCache } from './Messenger'
import * as socialApi from '@/api/social'
import { leereMailboxAbos, offeneMailboxAbos } from '@/services/mailboxAbo'
import { ladeGespraeche, leereGespraeche, merkeGespraech } from '@/services/gespraechsListe'
import { teamsApi } from '@/api/teams'
import { useAuthStore } from '@/stores/authStore'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'
import { erzeugeSignaturPaar, type SignaturPaar } from '@/services/absenderSignatur'
import { signiereNutzlast } from '@/services/nutzlastSignatur'
import { eigenesGeraet, signaturSchluesselVon } from '@/services/e2eeGeraet'
import { decryptE2eeHybridWithKeyring } from '@/services/e2eeCrypto'
import { verfallStand } from '@/services/nachrichtVerfall'

/**
 * Seit 09/2026 stehen die Aktionen eines Chats im Blattmenue, nicht mehr als
 * Knopfreihe in der Kopfzeile: bei 375 px war dort Platz fuer drei Knoepfe,
 * nicht fuer acht.
 */
async function oeffneChatMenue() {
  fireEvent.click(await screen.findByLabelText(i18n.t('messenger.moreChatSettings')))
}

/**
 * Dasselbe fuer eine einzelne Nachricht: Reagieren bis Loeschen steht im Menue.
 * Jede Blase traegt den Knopf, deshalb die Stelle statt des Titels — ohne
 * Angabe die letzte, also die zuletzt geschriebene Nachricht.
 */
function oeffneNachrichtenMenue(stelle = -1) {
  const knoepfe = screen.getAllByLabelText(i18n.t('messenger.messageActions'))
  fireEvent.click(knoepfe.at(stelle)!)
}


// Die Sprache festlegen: die Behauptungen unten prüfen deutsche Texte, und
// ohne diese Zeile entscheidet navigator.language der Testumgebung.
beforeAll(async () => {
  await i18n.changeLanguage('de')
})

const { mockEnvelopeCache } = vi.hoisted(() => ({
  mockEnvelopeCache: new Map<number, { plain: string; ok: boolean }>(),
}))

vi.mock('@/api/social', () => ({
  getFriends: vi.fn(),
  getGroups: vi.fn().mockResolvedValue([]),
  createGroup: vi.fn(),
  deleteGroup: vi.fn(),
  joinGroupByInvite: vi.fn(),
  leaveGroup: vi.fn(),
  getStories: vi.fn().mockResolvedValue([]),
  createStory: vi.fn(),
  deleteStory: vi.fn(),
  getGroupMembers: vi.fn().mockResolvedValue([]),
  updateGroupMemberRole: vi.fn(),
  kickGroupMember: vi.fn(),
  updateGroupPermissions: vi.fn(),
  getE2eePublicKey: vi.fn(),
  setE2eePublicKey: vi.fn(),
  getE2eeKeyring: vi.fn().mockResolvedValue({ wrapped_keyring: null, public_key: null, version: 0 }),
  putE2eeKeyring: vi.fn(),
  relayE2eeEnvelope: vi.fn(),
  fetchE2eeEnvelopes: vi.fn(),
  getPublicProfiles: vi.fn().mockResolvedValue([]),
  sendFriendRequest: vi.fn().mockResolvedValue({ success: true, message: 'Anfrage gesendet' }),
  sendTypingSignal: vi.fn().mockResolvedValue({ ok: true }),
}))

vi.mock('@/api/teams', () => ({
  teamsApi: {
    list: vi.fn(),
    get: vi.fn(),
  },
}))

vi.mock('@/services/e2eeCrypto', () => ({
  deriveBlindMailboxId: vi.fn().mockResolvedValue('test-blind-mailbox'),
  deriveGroupBlindMailboxId: vi.fn().mockResolvedValue('test-group-blind-mailbox'),
  getCachedBlindMailboxId: vi.fn().mockReturnValue(undefined),
  getCachedGroupBlindMailboxId: vi.fn().mockReturnValue(undefined),
  envelopePlaintextCache: mockEnvelopeCache,
  clearEnvelopePlaintextCache: vi.fn(() => mockEnvelopeCache.clear()),
  encryptE2eeHybrid: vi.fn().mockResolvedValue('sv-e2ee-hybrid-v1:...'),
  decryptE2eeHybrid: vi.fn().mockResolvedValue('Hallo Hybrid'),
  // Produktivpfad: gegen alle Schlüssel des Kontos, nicht gegen einen Gerätesschlüssel.
  decryptE2eeHybridWithKeyring: vi.fn().mockResolvedValue('Hallo Hybrid'),
  scrubPlaintextStorage: vi.fn(),
}))

/**
 * Was ein Umschlag im Test bedeutet.
 *
 * Bis 09/2026 lieh sich diese Rolle `decryptE2eeMessage` aus dem Produktivcode
 * — die Ableitung aus den beiden Benutzerkennungen, die der Server nachbauen
 * konnte. Sie ist gelöscht. Der Haken heißt jetzt, was er ist, und gehört dem
 * Test.
 */
const { identitaet, MockRecipientKeyMissingError, testKlartext } = vi.hoisted(() => ({
  testKlartext: vi.fn(async (_umschlag: string): Promise<string> => 'Hallo Welt'),
  identitaet: {
    state: 'ready' as 'needs-setup' | 'locked' | 'ready',
    sendPair: { publicKeyJwk: '{"kty":"oct"}', privateKeyJwk: '{"kty":"oct"}' } as
      | { publicKeyJwk: string; privateKeyJwk: string }
      | null,
    decryptionKeys: ['{"kty":"oct"}'] as string[],
    empfaengerSchluessel: 'mock-empfaenger-pub-key' as string | null,
  },
  MockRecipientKeyMissingError: class extends Error {
    constructor(public readonly userId: number) {
      super('Für diesen Empfänger liegt kein Schlüssel vor')
      this.name = 'E2eeRecipientKeyMissingError'
    }
  },
}))

/**
 * Stellvertreter für den Ratchet-Pfad.
 *
 * Der echte Double Ratchet erzeugt je Zielgerät einen eigenen Umschlag und
 * verbraucht seinen Nachrichtenschlüssel beim Öffnen — beides braucht dieser
 * Test nicht nachzubilden, um Zustellung, Häkchen und Verlauf zu prüfen.
 * Nachgebildet wird genau das, was der Messenger von der Schicht sieht: das
 * Umschlagformat, das Auffächern in eine Liste und die Ablegefunktion, die vor
 * dem Fortschreiben läuft. Die Krypto selbst prüft `ratchetSitzung.test.ts`.
 */
/**
 * Der lokale Verlaufsspeicher im Arbeitsspeicher statt in IndexedDB.
 *
 * jsdom bringt keine IndexedDB mit, und der Messenger braucht sie seit dem
 * Ratchet zwingend: ein Nachrichtenschlüssel ist nach dem Öffnen verbraucht,
 * der Klartext muss also abgelegt sein, bevor der Zustand weiterrückt.
 * Nachgebildet wird hier nur die Ablage; dass die Reihenfolge stimmt, prüfen
 * `ratchetSpeicher.test.ts` und `ratchetSitzung.test.ts` gegen die echte.
 */
const { nachrichten, klartexte } = vi.hoisted(() => ({
  nachrichten: new Map<string, any[]>(),
  klartexte: new Map<string, Map<number, string>>(),
}))

/** Zwischen zwei Tests muss der Speicher leer sein, sonst leckt der Verlauf. */
function leereTestSpeicher(): void {
  nachrichten.clear()
  klartexte.clear()
}

vi.mock('@/services/messengerLocalStore', async () => {
  const echt = await vi.importActual<typeof import('@/services/messengerLocalStore')>(
    '@/services/messengerLocalStore'
  )
  return {
    ...echt,
    loadLocalMessages: vi.fn(async (mid: string) => nachrichten.get(mid) ?? []),
    saveLocalMessages: vi.fn(async (mid: string, msgs: any[]) => {
      nachrichten.set(mid, msgs)
    }),
    // Die Ablage verliert eine Zeile nur, wenn jemand sie herausnimmt:
    // `saveLocalMessages` schreibt nur. Der Stellvertreter muss das nachbilden,
    // sonst prüfte der Test eine Ablage, die grosszügiger vergisst als die echte.
    entferneLokaleNachricht: vi.fn(
      async (mid: string, kennung: { clientUuid?: string; id?: number }) => {
        const vorhanden = nachrichten.get(mid)
        if (!vorhanden) return
        nachrichten.set(
          mid,
          vorhanden.filter(
            (m) =>
              !(
                (kennung.clientUuid !== undefined && m.clientUuid === kennung.clientUuid) ||
                (typeof kennung.id === 'number' && m.id === kennung.id)
              )
          )
        )
      }
    ),
    updateMessageInLocalStore: vi.fn(async () => {}),
    getLocalMailboxLastSyncedId: vi.fn(async () => 0),
    clearLocalMessengerStore: vi.fn(async () => {
      nachrichten.clear()
      klartexte.clear()
    }),
    speichereUmschlagKlartext: vi.fn(async (mid: string, id: number, plain: string) => {
      if (!klartexte.has(mid)) klartexte.set(mid, new Map())
      klartexte.get(mid)!.set(id, plain)
    }),
    leseUmschlagKlartext: vi.fn(
      async (mid: string, id: number) => klartexte.get(mid)?.get(id) ?? null
    ),
    ladeUmschlagKlartexte: vi.fn(
      async (mid: string) => new Map(klartexte.get(mid) ?? new Map())
    ),
  }
})

vi.mock('@/services/ratchetSitzung', async (importOriginal) => {
  // Die Fehlerklassen echt: der Messenger unterscheidet an ihnen, was er sagt.
  const { DrGeraetNichtEingetragenError, DrZustellungFehlgeschlagenError } =
    await importOriginal<typeof import('@/services/ratchetSitzung')>()
  const PREFIX = 'sv-e2ee-dr-v1:'
  const einpacken = (t: string, von: number = 1) => PREFIX + `${von}.testgeraet.zielgeraet.` + btoa(unescape(encodeURIComponent(t)))
  const auspacken = (u: string) => decodeURIComponent(escape(atob(u.split('.').slice(3).join('.'))))
  return {
    DR_PREFIX: PREFIX,
    DR_INIT_TYP: 'dr-init',
    DrGeraetNichtEingetragenError,
    DrZustellungFehlgeschlagenError,
    einpackenDr: einpacken,
    baueZustellungen: vi.fn(async (_kontext: any, klartext: string, basisUuid: string) => [
      {
        empfaengerId: 101,
        zielGeraet: 'zielgeraet',
        bootstrap: null,
        nachricht: einpacken(klartext),
        clientUuid: basisUuid,
        bootstrapClientUuid: basisUuid + '#i',
      },
    ]),
    liesDrUmschlag: vi.fn(
      async (
        _kontext: any,
        umschlag: string,
        klartext: { lies(): Promise<string | null>; lege(t: string): Promise<void> },
      ) => {
        // Wie die echte Fassung: erst nachsehen, ob ein anderer Durchlauf den
        // Umschlag schon geöffnet hat. Ein Ratchet-Nachrichtenschlüssel geht
        // kein zweites Mal auf, und ein zweiter Versuch sähe aus wie eine
        // Fälschung.
        const schon = await klartext.lies()
        if (schon !== null) {
          let von = 101
          if (umschlag.startsWith(PREFIX)) {
            const h = Number(umschlag.slice(PREFIX.length).split('.')[0])
            if (!isNaN(h) && h > 0) von = h
          } else {
            try {
              const p = JSON.parse(schon)
              if (p && typeof p === 'object' && p.sender_id) von = Number(p.sender_id)
            } catch {}
          }
          return { art: 'klartext', text: schon, vonKonto: von, vonGeraet: 'zielgeraet' }
        }
        // Der Klartext kommt weiterhin aus dem Stellvertreter, den die Tests
        // ohnehin je Fall setzen. So bleibt jede bestehende Vorgabe gültig,
        // obwohl der Messenger jetzt über den Ratchet liest.
        let text: string
        try {
          text = umschlag.startsWith(PREFIX) && umschlag.split('.').length > 3
            ? auspacken(umschlag)
            : await testKlartext(umschlag)
        } catch {
          return { art: 'bruch', vonKonto: 101, vonGeraet: 'zielgeraet', grund: 'Test' }
        }
        if (typeof text !== 'string' || text === '') {
          return { art: 'unbekannt' }
        }
        await klartext.lege(text)
        let von = 101
        if (umschlag.startsWith(PREFIX)) {
          const h = Number(umschlag.slice(PREFIX.length).split('.')[0])
          if (!isNaN(h) && h > 0) von = h
        } else {
          try {
            const p = JSON.parse(text)
            if (p && typeof p === 'object' && p.sender_id) von = Number(p.sender_id)
          } catch {}
        }
        return { art: 'klartext', text, vonKonto: von, vonGeraet: 'zielgeraet' }
      }
    ),
    verarbeiteBootstrap: vi.fn(async () => ({ istAufbau: false, ersetzt: false })),
    verwirfDrSitzung: vi.fn(async () => {}),
    drUrheber: (u: string) => {
      if (!u.startsWith(PREFIX)) return null
      const [konto, geraet] = u.slice(PREFIX.length).split('.')
      return Number(konto) > 0 ? { vonKonto: Number(konto), vonGeraet: geraet } : null
    },
    logischeUuid: (u?: string | null) =>
      !u ? undefined : u.indexOf('#') === -1 ? u : u.slice(0, u.indexOf('#')),
  }
})

/**
 * Ein Geraet der Gegenstelle, mit genau dem Schluessel, den der Test gesetzt hat.
 *
 * Wichtig fuer die Dateien, die den echten Hybridumschlag benutzen: dort muss
 * hier ein gueltiger JWK stehen, sonst scheitert das Versiegeln der Quittungen
 * und der Test misst etwas anderes, als er glaubt.
 */
const zielGeraete = () => [
  {
    device_id: 'zielgeraet',
    public_key: identitaet.empfaengerSchluessel || 'mock-empfaenger-pub-key',
    signing_public_key: '',
    label: '',
  },
]

/** Konten, die laut Verzeichnis einen Signaturschlüssel führen. Je Test gesetzt. */
let kontenMitSignatur: number[] = []
let schluesselWarnungCallback: ((ev: any) => void) | null = null

vi.mock('@/services/e2eeGeraet', () => ({
  eigenesGeraet: vi.fn(async () => ({
    kennung: 'testgeraet',
    paar: identitaet.sendPair ?? { publicKeyJwk: '{"kty":"oct"}', privateKeyJwk: '{"kty":"oct"}' },
  })),
  geraeteVon: vi.fn(async () => zielGeraete()),
  verlangeGeraeteVon: vi.fn(async () => {
    if (!identitaet.empfaengerSchluessel) throw new MockRecipientKeyMissingError(0)
    return zielGeraete()
  }),
  // Die Downgrade-Schranke der Absenderbeglaubigung: wer hier drinsteht, führt
  // einen Signaturschlüssel — eine unsignierte Nutzlast in seinem Namen gehört
  // dann verworfen. Die Signatur selbst prüft `nutzlastSignatur.test.ts`.
  kontoNutztSignaturen: vi.fn(async (uid: number) => kontenMitSignatur.includes(uid)),
  signaturSchluesselVon: vi.fn(async () => null),
  vergessenGeraete: vi.fn(),
  clearGeraeteMemory: vi.fn(),
  eigenesGeraetFreigegeben: vi.fn(() => true),
  onEigeneFreigabe: vi.fn(() => () => {}),
  onSchluesselWarnung: vi.fn((cb) => {
    schluesselWarnungCallback = cb
    return () => {
      schluesselWarnungCallback = null
    }
  }),
  sicherheitsnummer: vi.fn(async () => '11111 22222 33333 44444'),
  E2eeKeinGeraetError: class extends Error {},
}))

/**
 * Nutzlasten, die als beglaubigt gelten: `client_uuid` → Konto. Je Test gesetzt.
 *
 * Wer beglaubigen kann, muss es auch — seit 09/2026 auch über den Ratchet. Ein
 * Test, der ein unterschreibendes Konto aufstellt, braucht deshalb für dessen
 * echte Nachrichten einen Beleg. Die Signatur selbst prüft
 * `nutzlastSignatur.test.ts`; hier zählt nur, was der Messenger mit dem
 * Ergebnis macht.
 */
let beglaubigt = new Map<string, number>()

vi.mock('@/services/nutzlastSignatur', async (importOriginal) => {
  const echt = await importOriginal<typeof import('@/services/nutzlastSignatur')>()
  return {
    ...echt,
    pruefeNutzlast: vi.fn(async (mid: string, roh: Record<string, unknown>) => {
      const konto = beglaubigt.get(String(roh.client_uuid))
      return konto !== undefined
        ? { art: 'geprueft' as const, vonKonto: konto }
        : echt.pruefeNutzlast(mid, roh)
    }),
  }
})

vi.mock('@/services/e2eeIdentity', () => ({
  IDENTITY_LOADING: { state: 'loading', sendPair: null, decryptionKeys: [] },
  resolveIdentity: vi.fn(async () => ({
    state: identitaet.state,
    sendPair: identitaet.sendPair,
    decryptionKeys: identitaet.decryptionKeys,
  })),
  getRecipientPublicKey: vi.fn(async () => identitaet.empfaengerSchluessel),
  requireRecipientPublicKey: vi.fn(async (userId: number) => {
    if (!identitaet.empfaengerSchluessel) throw new MockRecipientKeyMissingError(userId)
    return identitaet.empfaengerSchluessel
  }),
  forgetRecipientPublicKey: vi.fn(),
  createIdentity: vi.fn(),
  unlockWithRecoveryKey: vi.fn(),
  rotateRecoveryKey: vi.fn(),
  clearIdentityMemory: vi.fn(),
  E2eeRecipientKeyMissingError: MockRecipientKeyMissingError,
  E2eeLockedError: class extends Error {},
}))

vi.mock('@/lib/offlineSync', () => ({
  loadNotesOfflineFirst: vi.fn().mockResolvedValue({ notes: [] }),
  loadCalendarEventsOfflineFirst: vi.fn().mockResolvedValue({ events: [] }),
  saveNoteOffline: vi.fn().mockResolvedValue({ id: 1, title: 'Mock' }),
  saveCalendarEventOffline: vi.fn().mockResolvedValue({ id: 1, title: 'Mock' }),
  enqueueMessageMutation: vi.fn().mockReturnValue({ id: 'mock-mutation' }),
  // Der Chat fasst die Warteschlange selbst nach; ohne diese beiden bricht
  // schon das Einhaengen der Seite ab.
  getOutbox: vi.fn().mockReturnValue([]),
  setOutbox: vi.fn(),
  replayOutbox: vi.fn().mockResolvedValue({ processed: 0, failed: 0, remaining: 0 }),
}))

function setupUser() {
  useAuthStore.setState({
    user: {
      id: 1,
      username: 'me',
      email: 'me@example.test',
      is_owner: false,
      is_active: true,
      email_verified: true,
      two_factor_enabled: false,
      email_notifications: false,
      ai_notifications: false,
      device_notifications: false,
      role_id: null,
      created_at: '2026-08-28T00:00:00Z',
    },
    isAuthenticated: true,
  })
}

describe('Messenger (Allround Chat)', () => {
  beforeEach(() => {
    leereTestSpeicher()
    vi.clearAllMocks()
    if (typeof sessionStorage !== 'undefined') sessionStorage.clear()
    if (typeof localStorage !== 'undefined') localStorage.clear()
    clearSessionChatCache()
    mockEnvelopeCache.clear()
    // Die Gesprächsliste liegt seit Stufe 6b im Arbeitsspeicher dieses Moduls
    // und nicht nur in `localStorage`; `clear()` oben erreicht sie nicht.
    leereGespraeche()
    kontenMitSignatur = []
    beglaubigt = new Map()
    setupUser()

    // Standardlage: Geraet entsperrt, Gegenseite hat einen Schluessel.
    identitaet.state = 'ready'
    identitaet.sendPair = { publicKeyJwk: '{"kty":"oct"}', privateKeyJwk: '{"kty":"oct"}' }
    identitaet.decryptionKeys = ['{"kty":"oct"}']
    identitaet.empfaengerSchluessel = 'mock-empfaenger-pub-key'
    vi.mocked(socialApi.relayE2eeEnvelope).mockResolvedValue({ id: 1 } as any)

    vi.mocked(socialApi.getFriends).mockResolvedValue([
      {
        id: 101,
        user_id: 101,
        username: 'alice',
        avatar_url: null,
        status: 'accepted',
        is_requester: false,
        created_at: '2026-09-01T00:00:00Z',
        presence: {
          status: 'online',
          device_type: 'web',
          activity_label: 'Im Panel',
          activity_detail: null,
        },
      },
    ])

    vi.mocked(teamsApi.list).mockResolvedValue([
      { id: 'team-1', name: 'Dev Team', description: '', created_at: '' },
    ])

    vi.mocked(teamsApi.get).mockResolvedValue({
      id: 'team-1',
      name: 'Dev Team',
      description: '',
      created_at: '',
      members: [
        {
          user_id: 1,
          username: 'me',
          email: 'me@example.test',
          role: 'member',
          joined_at: '',
        },
        {
          user_id: 202,
          username: 'charlie_teammate',
          email: 'charlie@example.test',
          role: 'member',
          joined_at: '',
        },
      ],
    } as any)

    vi.mocked(socialApi.getGroups).mockResolvedValue([])
    vi.mocked(socialApi.getPublicProfiles).mockResolvedValue([])
    vi.mocked(socialApi.getStories).mockResolvedValue([])
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([])
    vi.mocked(socialApi.getE2eePublicKey).mockResolvedValue({ user_id: 101, username: 'alice', public_key: null })
  })

  it('rendert Kontakte inklusive Freunde und Teammitglieder ohne bestehenden Freundschaftsstatus', async () => {
    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument()
      expect(screen.getByText('charlie_teammate')).toBeInTheDocument()
    })

    expect(screen.getByText('Dev Team')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Freunde oder Teammitglieder suchen …')).toBeInTheDocument()
  })

  it('zeigt einen Freund einmal, auch wenn zwei Zeilen zur selben Freundschaft ankommen', async () => {
    // Die Lage vom 20.09.2026: `user_friends` hielt die Freundschaft in beiden
    // Richtungen, die Liste bekam zwei Zeilen mit derselben Benutzer-Id — und
    // vergab für beide denselben Schlüssel.
    const zeile = (id: number) => ({
      id,
      user_id: 101,
      username: 'alice',
      avatar_url: null,
      status: 'accepted',
      is_requester: id === 1,
      created_at: '2026-09-01T00:00:00Z',
      presence: {
        status: 'online',
        device_type: 'web',
        activity_label: 'Im Panel',
        activity_detail: null,
      },
    })
    vi.mocked(socialApi.getFriends).mockResolvedValue([zeile(1), zeile(2)] as any)
    const konsolenfehler = vi.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('charlie_teammate')).toBeInTheDocument()
    })

    expect(screen.getAllByText('alice')).toHaveLength(1)
    // Und React hat nichts zu beanstanden: kein zweiter Eintrag unter `f-101`.
    const schluesselwarnung = konsolenfehler.mock.calls.some((args) =>
      args.some((a) => String(a).includes('same key'))
    )
    konsolenfehler.mockRestore()
    expect(schluesselwarnung).toBe(false)
  })

  it('öffnet die Konversation beim Klick auf einen Kontakt und zeigt dezente E2EE-Statuszeile', async () => {
    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('alice'))

    await waitFor(() => {
      expect(screen.getByText('Nachrichten in diesem Chat sind Ende-zu-Ende verschlüsselt.')).toBeInTheDocument()
    })

    expect(screen.getByPlaceholderText(i18n.t('messenger.writePlaceholder'))).toBeInTheDocument()
    // Open unified attachment menu
    fireEvent.click(screen.getByLabelText('Anhang hinzufügen'))
    expect(screen.getByLabelText('Foto anhängen')).toBeInTheDocument()
    expect(screen.getByLabelText('Notiz teilen')).toBeInTheDocument()
    expect(screen.getByLabelText('Kalendereintrag teilen')).toBeInTheDocument()
  })

  it('erlaubt das Senden einer Nachricht an ein Teammitglied ohne vorherige Freundschaft', async () => {
    vi.mocked(socialApi.relayE2eeEnvelope).mockResolvedValueOnce({
      id: 999,
      blind_mailbox_id: 'test-blind-mailbox',
      ciphertext_envelope: 'ciphertext',
      created_at: new Date().toISOString(),
    })

    render(
      <MemoryRouter initialEntries={['/chat?userId=202']}>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByPlaceholderText(i18n.t('messenger.writePlaceholder'))).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText(i18n.t('messenger.writePlaceholder'))
    fireEvent.change(input, { target: { value: 'Hallo Teammate!' } })

    const sendBtn = screen.getByTitle('Senden')
    fireEvent.click(sendBtn)

    await waitFor(() => {
      expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
        expect.not.objectContaining({
          recipient_user_id: expect.anything(),
        })
      )
      expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
        expect.objectContaining({
          blind_mailbox_id: expect.any(String),
          ciphertext_envelope: expect.any(String),
        })
      )
    })
  })

  it('rendert Gruppen, filtert nach Gruppen und öffnet Gruppenchat mit Verschlüsselungsanzeige', async () => {
    vi.mocked(socialApi.getGroups).mockResolvedValue([
      {
        id: 77,
        name: 'Dev Community',
        description: 'Offizielle Entwicklergruppe',
        avatar_url: null,
        invite_code: 'dev-invite-123',
        owner_user_id: 1,
        member_count: 5,
        role: 'admin',
        created_at: '2026-09-07T00:00:00Z',
        members: [],
      },
    ])

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('Dev Community')).toBeInTheDocument()
      expect(screen.getByText('Offizielle Entwicklergruppe')).toBeInTheDocument()
      expect(screen.getByText('5 M.')).toBeInTheDocument()
    })

    // Click on group to open group chat
    fireEvent.click(screen.getByText('Dev Community'))

    await waitFor(() => {
      expect(screen.getByPlaceholderText(i18n.t('messenger.writePlaceholder'))).toBeInTheDocument()
    })

    await oeffneChatMenue()
    expect(screen.getByText(i18n.t('messenger.copyInvite'))).toBeInTheDocument()
  })

  it('meldet jede Gruppe und jeden Chat beim Strom an, nicht nur das offene Gespräch', async () => {
    /*
     * Die Zusage, an der seit Stufe 4 alles hängt.
     *
     * Der Server schlägt nicht mehr nach, wer zu einer Gruppe gehört — er
     * stellt an die Abonnenten einer Mailbox zu und sonst an niemanden. Ohne
     * diese Anmeldung erführe man von einer Gruppennachricht erst beim Öffnen
     * genau dieses Gesprächs, und auf dem geschlossenen Tab nie. Der Ausfall
     * wäre vollkommen still: die Nachrichten kommen ja an, nur eben erst beim
     * nächsten Abruf.
     *
     * Deshalb wird hier nichts angeklickt. Gemessen wird, was **ohne**
     * geöffnetes Gespräch im Abo steht.
     */
    leereMailboxAbos()
    vi.mocked(socialApi.getGroups).mockResolvedValue([
      {
        id: 77,
        name: 'Dev Community',
        description: null,
        avatar_url: null,
        invite_code: 'dev-invite-123',
        owner_user_id: 1,
        member_count: 5,
        role: 'admin',
        created_at: '2026-09-07T00:00:00Z',
        members: [],
      },
    ])
    /*
     * Das Gespräch kommt seit Stufe 6b aus der versiegelten örtlichen Ablage
     * und nicht mehr von `GET /social/direct-chats` — die Route ist entfernt,
     * weil der Server nicht mehr wissen soll, wer mit wem schreibt.
     *
     * Konto 104 steht bewusst in **keiner** anderen Liste dieses Tests: kein
     * Freund, kein Teammitglied, kein öffentliches Profil. Wäre es das, ginge
     * die Anmeldung auch ohne die Ablage durch, und dieser Test prüfte nichts.
     */
    await merkeGespraech(104, { username: 'bob' })

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    // Die Werte aus dem Stellvertreter für `e2eeCrypto` weiter oben.
    await waitFor(() => {
      expect(offeneMailboxAbos()).toEqual(
        expect.arrayContaining(['test-group-blind-mailbox', 'test-blind-mailbox']),
      )
    })
  })

  it('erlaubt das Erstellen einer neuen Gruppe über den Dialog', async () => {
    vi.mocked(socialApi.createGroup).mockResolvedValueOnce({
      id: 88,
      name: 'Neue Supergruppe',
      description: 'Testbeschreibung',
      avatar_url: null,
      invite_code: 'super-invite-code',
      owner_user_id: 1,
      member_count: 1,
      role: 'admin',
      created_at: '2026-09-07T00:00:00Z',
      members: [],
    })

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    // Open create group dialog
    const createBtn = screen.getAllByLabelText('Neue Gruppe erstellen')[0]
    fireEvent.click(createBtn)

    await waitFor(() => {
      expect(screen.getByText('Neue Gruppe erstellen')).toBeInTheDocument()
    })

    const nameInput = screen.getByPlaceholderText('z. B. Server-Admins oder Gaming')
    fireEvent.change(nameInput, { target: { value: 'Neue Supergruppe' } })

    const submitBtn = screen.getByRole('button', { name: 'Gruppe erstellen' })
    fireEvent.click(submitBtn)

    /*
     * Ohne Nutzlast — und das ist der ganze Punkt von Stufe 6.
     *
     * `chat_groups.name`, `description` und `avatar_url` sind geräumt; der
     * Server vergibt nur noch eine Kennung und einen Einladungscode. Der Name
     * geht von hier in den versiegelten örtlichen Speicher und in den
     * verschlüsselten Gruppenblock. Stünde er hier wieder im Aufruf, läge er
     * beim nächsten Blick in die Datenbank wieder im Klartext da.
     */
    await waitFor(() => {
      expect(socialApi.createGroup).toHaveBeenCalledWith()
    })
    const ruf = (socialApi.createGroup as unknown as { mock: { calls: unknown[][] } }).mock
      .calls[0]
    expect(JSON.stringify(ruf)).not.toContain('Neue Supergruppe')
  })

  it('zeigt WhatsApp-typischen Sprachnachricht-Button bei leerem Textfeld und Senden-Button bei Eingabe', async () => {
    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('alice'))

    await waitFor(() => {
      expect(screen.getByLabelText('Sprachnachricht aufnehmen')).toBeInTheDocument()
    })

    // Typing text replaces Mic button with Send button
    const input = screen.getByPlaceholderText(i18n.t('messenger.writePlaceholder'))
    fireEvent.change(input, { target: { value: 'Hey!' } })

    expect(screen.queryByLabelText('Sprachnachricht aufnehmen')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Senden/i })).toBeInTheDocument()

    // Clearing input restores Mic button
    fireEvent.change(input, { target: { value: '' } })
    expect(screen.getByLabelText('Sprachnachricht aufnehmen')).toBeInTheDocument()
  })

  it('öffnet den Sticker- und Emoji-Wähler im Chat', async () => {
    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('alice'))

    await waitFor(() => {
      expect(screen.getByLabelText('Sticker auswählen')).toBeInTheDocument()
    })

    // Click sticker toggle button
    fireEvent.click(screen.getByLabelText('Sticker auswählen'))

    // Expect sticker and emoji tab buttons
    expect(screen.getByRole('button', { name: 'Sticker' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Emojis' })).toBeInTheDocument()

    // Switch to Emojis tab
    fireEvent.click(screen.getByRole('button', { name: 'Emojis' }))
    expect(screen.getByText('👍')).toBeInTheDocument()

    // Switch back to Stickers tab
    fireEvent.click(screen.getByRole('button', { name: 'Sticker' }))
    expect(screen.getByText('Feuer')).toBeInTheDocument()
  })

  it('schaltet zwischen den WhatsApp-typischen Reitern auf Mobilgeräten um', async () => {
    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument()
    })

    // Beide Leisten stehen im Baum — im Browser blendet CSS eine aus, in jsdom
    // nicht. Gemeint ist hier die untere, also die Navigation.
    const untereLeiste = within(screen.getByRole('navigation'))

    // Click Aktuelles tab
    const updatesTab = untereLeiste.getByRole('button', { name: 'Aktuelles' })
    fireEvent.click(updatesTab)
    expect(screen.getByText('Status')).toBeInTheDocument()

    // Click Community tab
    const communityTab = untereLeiste.getByRole('button', { name: 'Community' })
    fireEvent.click(communityTab)
    expect(screen.getByText('Communities & Gruppen')).toBeInTheDocument()

    // Audio tab should be removed
    expect(screen.queryByRole('button', { name: 'Audio' })).not.toBeInTheDocument()

    // Switch back to Chats tab
    const chatsTab = untereLeiste.getByRole('button', { name: 'Chats' })
    fireEvent.click(chatsTab)
    expect(screen.getByText('alice')).toBeInTheDocument()
  })

  it('bietet eine Schnellkamera-Schaltfläche in der Kopfzeile an', () => {
    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    const cameraBtn = screen.getByRole('button', { name: 'Foto aufnehmen' })
    expect(cameraBtn).toBeInTheDocument()
  })

  it('erlaubt dem Gruppen-Eigentümer das Löschen der Gruppe', async () => {
    vi.mocked(socialApi.getGroups).mockResolvedValue([
      {
        id: 77,
        name: 'Delete Me Clan',
        description: 'Temporary group',
        invite_code: 'temp-123',
        owner_user_id: 1,
        member_count: 3,
        role: 'admin',
        created_at: '2026-09-02T00:00:00Z',
        members: [],
      } as any,
    ])
    vi.mocked(socialApi.deleteGroup).mockResolvedValueOnce({ success: true } as any)

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('Delete Me Clan')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Delete Me Clan'))

    await oeffneChatMenue()
    fireEvent.click(screen.getByText(i18n.t('messenger.deleteGroup')))

    await waitFor(() => {
      expect(screen.getByText('Endgültig löschen')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('Endgültig löschen'))

    await waitFor(() => {
      expect(socialApi.deleteGroup).toHaveBeenCalledWith(77)
    })
  })

  it('öffnet Gruppenrollen & Rechte Modal über den Shield-Button im Header', async () => {
    vi.mocked(socialApi.getGroups).mockResolvedValue([
      {
        id: 88,
        name: 'Admin Tribe',
        description: 'Protected group',
        invite_code: 'admin-123',
        owner_user_id: 1,
        member_count: 2,
        role: 'owner',
        created_at: '2026-09-02T00:00:00Z',
        members: [],
      } as any,
    ])

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('Admin Tribe')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Admin Tribe'))

    await oeffneChatMenue()
    fireEvent.click(screen.getByText(i18n.t('messenger.manageGroupRoles')))

    await waitFor(() => {
      expect(screen.getByText('Gruppen-Rollen & Rechte')).toBeInTheDocument()
    })
  })

  it('rendert Status-Stories unter Aktuelles und öffnet den Erstellungs-Dialog', async () => {
    vi.mocked(socialApi.getStories).mockResolvedValue([
      {
        id: 1,
        user_id: 101,
        username: 'alice',
        user_avatar: null,
        content: 'Mein cooler Status',
        media_url: null,
        background: 'gradient-1',
        created_at: new Date().toISOString(),
        expires_at: new Date(Date.now() + 86400000).toISOString(),
      },
    ])

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getAllByText('alice')[0]).toBeInTheDocument()
    })

    const updatesTab = within(screen.getByRole('navigation')).getByRole('button', { name: 'Aktuelles' })
    fireEvent.click(updatesTab)

    await waitFor(() => {
      expect(screen.getAllByText('Mein Status')[0]).toBeInTheDocument()
      expect(screen.getByText('Hinzufügen')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('Hinzufügen'))

    await waitFor(() => {
      expect(screen.getByText('Status erstellen')).toBeInTheDocument()
    })
  })

  it('entdeckt öffentliche Profile im Messenger und erlaubt Direktchats sowie Freundschaftsanfragen', async () => {
    vi.mocked(socialApi.getPublicProfiles).mockResolvedValue([
      {
        user_id: 303,
        username: 'bob_public',
        social_privacy: 'public',
        is_friend: false,
        presence: {
          user_id: 303,
          username: 'bob_public',
          status: 'online',
          device_type: 'desktop',
          activity_label: 'Online',
          last_seen_at: null,
          updated_at: null,
        },
      },
    ])

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText('bob_public')).toBeInTheDocument()
    })

    // Filter by public tab
    const publicTab = screen.getByTitle(/Öffentlich/i)
    fireEvent.click(publicTab)

    expect(screen.getByRole('button', { name: /bob_public/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /alice/i })).not.toBeInTheDocument()

    // Click on public contact to open chat
    fireEvent.click(screen.getByRole('button', { name: /bob_public/i }))

    await waitFor(() => {
      expect(screen.getByLabelText(i18n.t('messenger.sendFriendRequest'))).toBeInTheDocument()
    })

    // Send friend request
    fireEvent.click(screen.getByLabelText(i18n.t('messenger.sendFriendRequest')))
    expect(socialApi.sendFriendRequest).toHaveBeenCalledWith('bob_public')
  })

  it('unterstützt dynamische Lesebestätigungen und das Bearbeiten & Löschen von Nachrichten (Zero Knowledge)', async () => {
    // 1. Setup existing chat envelopes (E2EE)
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 10,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-10',
        created_at: '2026-09-08T12:00:00Z',
      },
      {
        id: 11,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-11',
        created_at: '2026-09-08T12:01:00Z',
      },
    ])

    testKlartext.mockImplementation(async (envelope) => {
      if (envelope === 'ciphertext-10') {
        return JSON.stringify({
          sender_id: 1, // Self
          text: 'Meine ursprüngliche Nachricht',
          timestamp: '2026-09-08T12:00:00Z',
        })
      }
      if (envelope === 'ciphertext-11') {
        return JSON.stringify({
          sender_id: 101, // Alice
          text: 'Hallo von Alice!',
          timestamp: '2026-09-08T12:01:00Z',
        })
      }
      return 'Unbekannt'
    })

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    // Open chat with Alice
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /alice/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /alice/i }))

    await waitFor(() => {
      expect(screen.getByText('Meine ursprüngliche Nachricht')).toBeInTheDocument()
      expect(screen.getByText('Hallo von Alice!')).toBeInTheDocument()
      // Initial status before acknowledgement is "Nicht zugestellt", "Gesendet" or "Zugestellt"
      expect(screen.getByTitle(/Gesendet|Zugestellt/i)).toBeInTheDocument()
    })

    // 2. Simulate incoming read receipt envelope from Alice for message 10
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 10,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-10',
        created_at: '2026-09-08T12:00:00Z',
      },
      {
        id: 11,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-11',
        created_at: '2026-09-08T12:01:00Z',
      },
      {
        id: 12,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-read-receipt',
        created_at: '2026-09-08T12:02:00Z',
      },
    ])
    testKlartext.mockImplementation(async (envelope) => {
      if (envelope === 'ciphertext-10') {
        return JSON.stringify({
          sender_id: 1,
          text: 'Meine ursprüngliche Nachricht',
          timestamp: '2026-09-08T12:00:00Z',
        })
      }
      if (envelope === 'ciphertext-11') {
        return JSON.stringify({
          sender_id: 101,
          text: 'Hallo von Alice!',
          timestamp: '2026-09-08T12:01:00Z',
        })
      }
      if (envelope === 'ciphertext-read-receipt') {
        return JSON.stringify({
          type: 'read_receipt',
          read_up_to_id: 10,
          reader_id: 101, // Alice read our message
        })
      }
      return 'Unbekannt'
    })

    // Trigger sync event
    window.dispatchEvent(
      new CustomEvent('msm:sync-event', {
        detail: { type: 'e2ee_blind_message', blind_mailbox_id: 'test-blind-mailbox' },
      })
    )

    await waitFor(() => {
      expect(screen.getByTitle(i18n.t('messenger.stateRead'))).toBeInTheDocument()
    })

    // 3. Test Editing Message
    // Die eigene Nachricht steht als erste im Verlauf; nur sie kennt Bearbeiten.
    oeffneNachrichtenMenue(0)
    fireEvent.click(screen.getByText(i18n.t('common.edit')))

    expect(screen.getByText(i18n.t('messenger.editMessage'))).toBeInTheDocument()
    const input = screen.getByPlaceholderText('Nachricht bearbeiten …')
    fireEvent.change(input, { target: { value: 'Meine korrigierte Nachricht' } })

    const sendBtn = screen.getByTitle('Senden')
    fireEvent.click(sendBtn)

    await waitFor(() => {
      expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
        expect.objectContaining({
          blind_mailbox_id: 'test-blind-mailbox',
        })
      )
    })

    // 4. Test Deleting Message with Opferschutz / Beweissicherung
    oeffneNachrichtenMenue(0)
    fireEvent.click(screen.getByText(i18n.t('messenger.deleteForAllShort')))

    await waitFor(() => {
      expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
        expect.objectContaining({
          blind_mailbox_id: 'test-blind-mailbox',
        })
      )
    })
  })

  it('unterstützt WhatsApp-ähnliche Audiogeschwindigkeit und verhindert doppelte Kalender- & Notizeinträge', async () => {
    const { saveNoteOffline, saveCalendarEventOffline } = await import('@/lib/offlineSync')

    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 20,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-voice',
        created_at: '2026-09-08T14:00:00Z',
      },
      {
        id: 21,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-note',
        created_at: '2026-09-08T14:01:00Z',
      },
      {
        id: 22,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-calendar',
        created_at: '2026-09-08T14:02:00Z',
      },
    ])

    testKlartext.mockImplementation(async (envelope) => {
      if (envelope === 'ciphertext-voice') {
        return JSON.stringify({
          sender_id: 101,
          audio_attachment: {
            dataUrl: 'data:audio/webm;base64,GkXfo59ChoEBQveBAULygQRC84EIQoKEd2VibUKHgQRChYECGFOAZwE=',
            durationSeconds: 15,
            mimeType: 'audio/webm',
          },
        })
      }
      if (envelope === 'ciphertext-note') {
        return JSON.stringify({
          sender_id: 101,
          note_attachment: {
            title: 'Wichtige Notiz',
            content: 'Notizinhalt für den Test',
          },
        })
      }
      if (envelope === 'ciphertext-calendar') {
        return JSON.stringify({
          sender_id: 101,
          calendar_attachment: {
            title: 'Strategiemeeting',
            start: '2026-09-10T10:00:00Z',
            end: '2026-09-10T11:00:00Z',
            description: 'Vorbereitung',
          },
        })
      }
      return '{}'
    })

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /alice/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /alice/i }))

    // 1. Audio Playback Speed Toggle
    await waitFor(() => {
      expect(screen.getByText('1x')).toBeInTheDocument()
    })

    const speedBtn = screen.getByText('1x')
    fireEvent.click(speedBtn)
    expect(screen.getByText('1.5x')).toBeInTheDocument()

    fireEvent.click(screen.getByText('1.5x'))
    expect(screen.getByText('2x')).toBeInTheDocument()

    fireEvent.click(screen.getByText('2x'))
    expect(screen.getByText('1x')).toBeInTheDocument()

    // 2. Note Import (Double-click prevention)
    const importNoteBtn = screen.getByTitle('In eigene Notizen übernehmen')
    fireEvent.click(importNoteBtn)

    await waitFor(() => {
      expect(saveNoteOffline).toHaveBeenCalledTimes(1)
      expect(screen.getByText('Übernommen')).toBeInTheDocument()
    })

    // Clicking again should not trigger saveNoteOffline again
    const importedNoteBtn = screen.getByTitle('Bereits in eigene Notizen übernommen')
    expect(importedNoteBtn).toBeDisabled()
    fireEvent.click(importedNoteBtn)
    expect(saveNoteOffline).toHaveBeenCalledTimes(1)

    // 3. Calendar Import (Double-click prevention)
    const importCalBtn = screen.getByTitle('In eigenen Kalender eintragen')
    fireEvent.click(importCalBtn)

    await waitFor(() => {
      expect(saveCalendarEventOffline).toHaveBeenCalledTimes(1)
      expect(screen.getByText('Eingetragen')).toBeInTheDocument()
    })

    const importedCalBtn = screen.getByTitle('Bereits in eigenen Kalender eingetragen')
    expect(importedCalBtn).toBeDisabled()
    fireEvent.click(importedCalBtn)
    expect(saveCalendarEventOffline).toHaveBeenCalledTimes(1)
  })

  it('rendert Datumstrenner zwischen Nachrichten unterschiedlicher Tage', async () => {
    vi.mocked(socialApi.getFriends).mockResolvedValue([
      {
        id: 1,
        friend_user_id: 102,
        username: 'bob',
        avatar_url: null,
        presence: { status: 'online' },
      } as any,
    ])

    const todayIso = new Date().toISOString()
    const yesterday = new Date()
    yesterday.setDate(yesterday.getDate() - 1)
    const yesterdayIso = yesterday.toISOString()

    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 1,
        blind_mailbox_id: 'mailbox-102',
        ciphertext_envelope: 'ciphertext-yesterday',
        created_at: yesterdayIso,
      },
      {
        id: 2,
        blind_mailbox_id: 'mailbox-102',
        ciphertext_envelope: 'ciphertext-today',
        created_at: todayIso,
      },
    ])

    testKlartext.mockImplementation(async (envelope) => {
      if (envelope === 'ciphertext-yesterday') {
        return JSON.stringify({
          sender_id: 102,
          text: 'Hallo von gestern!',
        })
      }
      if (envelope === 'ciphertext-today') {
        return JSON.stringify({
          sender_id: 102,
          text: 'Hallo von heute!',
        })
      }
      return '{}'
    })

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /bob(?!_)/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /bob(?!_)/i }))

    await waitFor(() => {
      expect(screen.getByText('Hallo von gestern!')).toBeInTheDocument()
      expect(screen.getByText('Hallo von heute!')).toBeInTheDocument()
    })

    // Both date badges should be present
    expect(screen.getByText('Gestern')).toBeInTheDocument()
    expect(screen.getByText('Heute')).toBeInTheDocument()
  })

  // Am laufenden System gefunden: die Warteschlange meldet die Kennung **mit**
  // Gerätesuffix (`<uuid>#<geraet>`), weil dort je Zielgerät ein Auftrag
  // liegt. Die Zeile im Verlauf trägt die logische Kennung. Der Abgleich traf
  // deshalb nie zu: der Umschlag ging raus, die Zeile behielt ihr Uhr-Symbol,
  // und weil dieselbe Kennung in die Ablage ging, überlebte sie jedes
  // Neuladen. Es waren die Nachrichten, die zugestellt waren und trotzdem für
  // immer „in der Warteschlange" standen.
  it('verwirft eine hängengebliebene Nachricht endgültig — aus Ansicht, Ablage und Warteschlange', async () => {
    /**
     * Am laufenden System gemeldet: eine Nachricht mit der Uhr liess sich
     * löschen, stand aber Sekunden später wieder da. Zwei Ursachen, beide hier
     * geprüft.
     *
     * `saveLocalMessages` schreibt nur — die weggelassene Zeile blieb in der
     * Ablage stehen und kam beim nächsten Abgleich über `mischeVerlauf` zurück.
     *
     * Und die Warteschlange führt einen Auftrag je Zielgerät, auseinandergehalten
     * durch `#<geraet>`; verglichen wurde mit der Kennung ohne Zusatz. Der
     * Auftrag blieb also liegen und wäre später doch noch hinausgegangen. Der
     * Sitzungsaufbau muss dabei stehen bleiben: ohne ihn findet die Gegenstelle
     * für alles Spätere keine Sitzung.
     */
    const { entferneLokaleNachricht } = await import('@/services/messengerLocalStore')
    const { baueZustellungen } = await import('@/services/ratchetSitzung')
    const { getOutbox, setOutbox, enqueueMessageMutation } = await import('@/lib/offlineSync')

    vi.mocked(baueZustellungen).mockImplementation(async (_k: any, _klartext: string, basis: string) => [
      {
        empfaengerId: 101,
        zielGeraet: 'zielgeraet',
        bootstrap: 'sv-e2ee-hybrid-v1:aufbau',
        nachricht: 'sv-e2ee-dr-v1:1.testgeraet.zielgeraet.xx',
        clientUuid: `${basis}#zielgeraet01`,
        bootstrapClientUuid: `${basis}#izielgeraet01`,
      },
    ] as any)

    const warteschlange: any[] = []
    vi.mocked(enqueueMessageMutation).mockImplementation((p: any) => {
      const auftrag = {
        id: p.client_uuid,
        entity: 'message' as const,
        action: 'relay' as const,
        entityId: p.blind_mailbox_id,
        payload: p,
        timestamp: '2026-09-19T10:00:00.000Z',
        retryCount: 0,
      }
      warteschlange.push(auftrag)
      return auftrag
    })
    vi.mocked(getOutbox).mockImplementation(() => warteschlange)

    // Kein Netz für das Relais: die Nachricht bleibt in der Warteschlange.
    vi.mocked(socialApi.relayE2eeEnvelope).mockRejectedValue(
      Object.assign(new Error('Failed to fetch'), { status: 0 })
    )

    render(
      <MemoryRouter initialEntries={['/chat?userId=101']}>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByPlaceholderText(i18n.t('messenger.writePlaceholder'))).toBeInTheDocument()
    })

    fireEvent.change(screen.getByPlaceholderText(i18n.t('messenger.writePlaceholder')), {
      target: { value: 'Geht nicht raus' },
    })
    fireEvent.click(screen.getByTitle('Senden'))

    // Sitzungsaufbau und Nachricht liegen als zwei Aufträge in der Schlange.
    await waitFor(() => {
      expect(screen.getByTitle(/Warteschlange/)).toBeInTheDocument()
      expect(warteschlange).toHaveLength(2)
    })
    const basisUuid = warteschlange[1].payload.client_uuid.split('#')[0]

    oeffneNachrichtenMenue()
    fireEvent.click(screen.getByText(i18n.t('messenger.deleteForAllShort')))

    await waitFor(() => {
      expect(entferneLokaleNachricht).toHaveBeenCalledWith(
        'test-blind-mailbox',
        expect.objectContaining({ clientUuid: basisUuid })
      )
    })

    // Der Sitzungsaufbau bleibt, die Nachricht geht.
    const neueSchlange = vi.mocked(setOutbox).mock.calls.at(-1)![0]
    expect(neueSchlange.map((m: any) => m.payload.control_type)).toEqual(['dr-init'])

    // Und sie kommt beim nächsten Abgleich nicht zurück.
    expect(screen.queryByText('Geht nicht raus')).not.toBeInTheDocument()
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent('msm:sync-event', {
          detail: { type: 'e2ee_blind_message', blind_mailbox_id: 'test-blind-mailbox' },
        })
      )
      await Promise.resolve()
    })
    await waitFor(() => {
      expect(screen.queryByText('Geht nicht raus')).not.toBeInTheDocument()
    })
  })

  it('zieht eine bestätigte Nachricht nach, auch wenn die Warteschlange die Kennung mit Gerätesuffix meldet', async () => {
    const { updateMessageInLocalStore } = await import('@/services/messengerLocalStore')
    vi.mocked(updateMessageInLocalStore).mockClear()

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )
    // Der Zuhörer hängt an der Seite, nicht am geöffneten Gespräch: die
    // Bestätigung kann jede Mailbox betreffen.
    await act(async () => {
      await Promise.resolve()
    })

    await act(async () => {
      window.dispatchEvent(
        new CustomEvent('msm:message-confirmed', {
          detail: {
            client_uuid: 'uuid-mit-suffix#a1b2c3d4e5f6',
            envelope_id: 4711,
            blind_mailbox_id: 'mailbox-102',
          },
        })
      )
    })

    await waitFor(() => {
      expect(updateMessageInLocalStore).toHaveBeenCalledWith(
        'mailbox-102',
        'uuid-mit-suffix',
        expect.objectContaining({ id: 4711, status: 'sent' })
      )
    })
  })

  // Am laufenden System gemeldet: die Meldung über die neu aufgebaute Sitzung
  // stand in einer Sprechblase links und sah damit aus, als hätte das
  // Gegenüber sie geschrieben. Bei einer Aussage über die Sicherheit genau
  // dieses Gesprächs ist das die schlechteste denkbare Verwechslung.
  it('zeigt eine neu aufgebaute Sitzung als Systemzeile, nicht als Nachricht des Gegenübers', async () => {
    vi.mocked(socialApi.getFriends).mockResolvedValue([
      {
        id: 1,
        friend_user_id: 102,
        username: 'bob',
        avatar_url: null,
        presence: { status: 'online' },
      } as any,
    ])

    const jetzt = new Date().toISOString()
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 1,
        blind_mailbox_id: 'mailbox-102',
        ciphertext_envelope: 'ciphertext-bruch',
        created_at: jetzt,
      },
      {
        id: 2,
        blind_mailbox_id: 'mailbox-102',
        ciphertext_envelope: 'ciphertext-heil',
        created_at: jetzt,
      },
    ] as any)

    testKlartext.mockImplementation(async (envelope) => {
      if (envelope === 'ciphertext-bruch') throw new Error('Sitzung trägt nicht mehr')
      return JSON.stringify({ sender_id: 102, text: 'echte Nachricht von bob' })
    })

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /bob(?!_)/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /bob(?!_)/i }))

    const meldung = await screen.findByText(/Sicherheitssitzung mit diesem Gerät wurde neu aufgebaut/)
    await waitFor(() => {
      expect(screen.getByText('echte Nachricht von bob')).toBeInTheDocument()
    })

    // `.group` ist der Rahmen einer Sprechblase samt Absender, Uhrzeit und
    // Kontextmenü. Die Systemzeile darf darin nicht stehen, die echte
    // Nachricht schon.
    expect(meldung.closest('.group')).toBeNull()
    expect(screen.getByText('echte Nachricht von bob').closest('.group')).not.toBeNull()
  })

  it('öffnet das Chat-Hintergrund-Modal und erlaubt die Auswahl von Presets', async () => {
    vi.mocked(socialApi.getFriends).mockResolvedValue([
      {
        id: 1,
        friend_user_id: 103,
        username: 'charlie',
        avatar_url: null,
        presence: { status: 'online' },
      } as any,
    ])

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /charlie(?!_)/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /charlie(?!_)/i }))

    // Der Hintergrund steht heute im Blattmenue, nicht in der Kopfzeile.
    await oeffneChatMenue()
    fireEvent.click(screen.getByText(i18n.t('social.wallpaper.title')))

    // Modal opens
    await waitFor(() => {
      expect(screen.getByText(i18n.t('social.wallpaper.title'))).toBeInTheDocument()
      expect(screen.getByText('Cyber Grid')).toBeInTheDocument()
      expect(screen.getByText('Deep Petrol')).toBeInTheDocument()
      expect(screen.getByText('Mitternacht')).toBeInTheDocument()
      expect(screen.getByText('Schlicht dunkel')).toBeInTheDocument()
    })

    // Select Midnight preset and apply
    fireEvent.click(screen.getByText('Mitternacht'))
    fireEvent.click(screen.getByRole('button', { name: 'Übernehmen' }))

    await waitFor(() => {
      expect(screen.queryByText('Design-Hintergründe')).not.toBeInTheDocument()
    })
  })

  it('rendert Story-Antworten mit reichhaltiger Vorschau und sendet Typing-Signale beim Tippen', async () => {
    vi.mocked(socialApi.getFriends).mockResolvedValue([
      {
        id: 104,
        user_id: 104,
        friend_user_id: 104,
        username: 'diana',
        status: 'accepted',
        is_requester: false,
        avatar_url: null,
        presence: { status: 'online' },
      } as any,
    ])

    const nowIso = new Date().toISOString()
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 1,
        blind_mailbox_id: 'mailbox-104',
        ciphertext_envelope: 'ciphertext-story-reply',
        created_at: nowIso,
      },
    ])

    testKlartext.mockImplementation(async (envelope) => {
      if (envelope === 'ciphertext-story-reply') {
        return JSON.stringify({
          sender_id: 104,
          text: 'Tolles Bild!',
          story_reply: {
            storyId: 42,
            storyContent: 'Urlaubsausblick 2026',
            storyUsername: 'me',
          },
        })
      }
      return '{}'
    })

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /diana/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /diana/i }))

    // Story reply preview card should render
    await waitFor(() => {
      expect(screen.getByText('Status von me')).toBeInTheDocument()
      expect(screen.getByText('Urlaubsausblick 2026')).toBeInTheDocument()
      expect(screen.getByText('Tolles Bild!')).toBeInTheDocument()
    })

    // Typing sends typing signal
    const input = screen.getByPlaceholderText(i18n.t('messenger.writePlaceholder'))
    fireEvent.change(input, { target: { value: 'Ich schreibe gerade' } })

    await waitFor(() => {
      expect(socialApi.sendTypingSignal).toHaveBeenCalledWith(
        expect.objectContaining({
          status: 'typing',
        })
      )
    })
  })

  it('faechert plattformuebergreifend (Tauri <-> Web) je Empfaengergeraet auf', async () => {
    // Zwei Verfahren sind hier nacheinander gestorben. Zuerst die
    // „deterministische Kanalverschluesselung", deren Schluessel sich allein aus
    // den beiden Benutzerkennungen ergab — die das Backend beim Relais ohnehin
    // kennt. Danach der einzelne hybride Umschlag gegen den Kontoschluessel: ein
    // Konto hat keinen gemeinsamen privaten Schluessel mehr, weil der Double
    // Ratchet eine lineare Kette je Geraet ist. Geblieben ist das Auffaechern.
    const { baueZustellungen } = await import('@/services/ratchetSitzung')
    vi.mocked(socialApi.getFriends).mockResolvedValue([
      {
        id: 99, // Friendship table ID
        user_id: 205, // Actual user ID
        username: 'bob_desktop',
        avatar_url: null,
        status: 'accepted',
        presence: { status: 'online', device_type: 'desktop' },
      },
    ])
    // Both users have registered public keys (e.g. from Tauri or Web)
    identitaet.empfaengerSchluessel = '{"kty":"RSA","n":"pub_bob"}'

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /bob_desktop/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /bob_desktop/i }))

    await waitFor(() => {
      expect(screen.getByText('Nachrichten in diesem Chat sind Ende-zu-Ende verschlüsselt.')).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText(i18n.t('messenger.writePlaceholder'))
    fireEvent.change(input, { target: { value: 'Nachricht aus Tauri' } })

    // Das Tippsignal traegt keine Empfaengerkennung mehr; dass die richtige
    // Gegenstelle gemeint ist, haelt der `baueZustellungen`-Aufruf unten fest.
    await waitFor(() => {
      expect(socialApi.sendTypingSignal).toHaveBeenCalledWith(
        expect.objectContaining({
          status: 'typing',
        })
      )
    })

    const sendBtn = screen.getByTitle('Senden')
    fireEvent.click(sendBtn)

    // Der Ratchet baut je Zielgeraet einen Umschlag. Der Empfaenger steht
    // nicht mehr daneben — `baueZustellungen` oben haelt fest, dass es die
    // echte Benutzerkennung (205) ist und nicht die Freundschafts-ID.
    await waitFor(() => {
      expect(baueZustellungen).toHaveBeenCalledWith(
        { eigeneId: 1, peerId: 205 },
        expect.stringContaining('Nachricht aus Tauri'),
        expect.any(String)
      )
      expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
        expect.objectContaining({
          ciphertext_envelope: expect.stringContaining('sv-e2ee-dr-v1:'),
        })
      )
    })
  })

  it('sagt, warum ein entferntes Gerät nicht sendet, und gibt den Text zurück', async () => {
    // In einem anderen Tab aus der Geräteliste entfernt. Senden hiesse
    // Aufbauten, die drüben niemand prüfen kann; die Nachricht verschwände
    // dort still, und hier stünde sie als gesendet.
    const { baueZustellungen, DrGeraetNichtEingetragenError } = (await import(
      '@/services/ratchetSitzung'
    )) as any
    const { useToastStore } = await import('@/stores/toastStore')
    vi.mocked(baueZustellungen).mockRejectedValueOnce(new DrGeraetNichtEingetragenError())

    render(
      <MemoryRouter initialEntries={['/chat?userId=101']}>
        <Messenger />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByPlaceholderText(i18n.t('messenger.writePlaceholder'))).toBeInTheDocument()
    })
    fireEvent.change(screen.getByPlaceholderText(i18n.t('messenger.writePlaceholder')), {
      target: { value: 'Von einem entfernten Gerät' },
    })
    fireEvent.click(screen.getByTitle('Senden'))

    await waitFor(() => {
      expect(useToastStore.getState().toasts.map((t) => t.message)).toContain(
        i18n.t('messenger.deviceNotListed'),
      )
    })
    expect(screen.getByPlaceholderText(i18n.t('messenger.writePlaceholder'))).toHaveValue(
      'Von einem entfernten Gerät',
    )
    // Hinaus ging keine Nachricht. Eine Lesequittung für das, was beim Öffnen
    // im Fenster lag, darf es geben — sie läuft nicht über den Ratchet.
    const nachrichten = vi
      .mocked(socialApi.relayE2eeEnvelope)
      .mock.calls.filter(([auftrag]: any[]) => !auftrag?.is_control)
    expect(nachrichten).toEqual([])
  })

  it('entschluesselt empfangene Nachrichten aus Tauri/Web zuverlaessig ueber den synchronisierten Direktkanal', async () => {
    const { decryptE2eeHybridWithKeyring } = await import('@/services/e2eeCrypto')
    vi.mocked(socialApi.getFriends).mockResolvedValue([
      {
        id: 1,
        user_id: 206,
        username: 'charlie_e2ee',
        avatar_url: null,
        status: 'accepted',
        presence: { status: 'online' },
      },
    ])
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 501,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'sv-e2ee-v1:message-from-tauri',
        created_at: '2026-09-10T12:00:00Z',
      },
      {
        id: 502,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'sv-e2ee-v1:message-from-web',
        created_at: '2026-09-10T12:01:00Z',
      },
      {
        id: 503,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'sv-e2ee-hybrid-v1:unmatched-device-key',
        created_at: '2026-09-10T12:02:00Z',
      },
    ])

    // Direct channel decryption succeeds for cross-platform messages
    testKlartext.mockImplementation(async (env) => {
      if (env.includes('message-from-tauri')) {
        return JSON.stringify({
          sender_id: 206,
          text: 'Nachricht aus Tauri auf Web lesbar',
          timestamp: '2026-09-10T12:00:00Z',
        })
      }
      if (env.includes('message-from-web')) {
        return JSON.stringify({
          sender_id: 206,
          text: 'Nachricht aus Web auf Tauri lesbar',
          timestamp: '2026-09-10T12:01:00Z',
        })
      }
      return ''
    })

    // Unmatched legacy hybrid envelope throws
    vi.mocked(decryptE2eeHybridWithKeyring).mockRejectedValue(
      new Error('Kein passender Schlüssel im Bund für diesen Umschlag')
    )

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /charlie_e2ee/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /charlie_e2ee/i }))

    // Cross-platform messages must be decoded and readable, and unmatched legacy hybrid safely shows encrypted notice
    await waitFor(() => {
      expect(screen.getByText('Nachricht aus Tauri auf Web lesbar')).toBeInTheDocument()
      expect(screen.getByText('Nachricht aus Web auf Tauri lesbar')).toBeInTheDocument()
      expect(screen.getByText(i18n.t('messenger.encryptedMessage'))).toBeInTheDocument()
    })
  })

  it('dedupliziert eingehende Envelopes mit identischer client_uuid in der UI', async () => {
    vi.mocked(socialApi.getFriends).mockResolvedValue([
      {
        id: 1,
        user_id: 301,
        username: 'dedup_user',
        avatar_url: null,
        status: 'accepted',
        presence: { status: 'online', device_type: 'web' },
      },
    ])

    // Server liefert 2 Envelopes mit derselben client_uuid (Retry-Szenario)
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 701,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'cipher-msg-1',
        client_uuid: 'unique-client-uuid-777',
        created_at: '2026-09-10T14:00:00Z',
      },
      {
        id: 702,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'cipher-msg-1-retry',
        client_uuid: 'unique-client-uuid-777',
        created_at: '2026-09-10T14:00:05Z',
      },
    ])

    testKlartext.mockResolvedValue(
      JSON.stringify({
        sender_id: 301,
        client_uuid: 'unique-client-uuid-777',
        text: 'Einmalige Nachricht trotz Retry',
        timestamp: '2026-09-10T14:00:00Z',
      })
    )

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /dedup_user/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /dedup_user/i }))

    await waitFor(() => {
      const messages = screen.getAllByText('Einmalige Nachricht trotz Retry')
      expect(messages).toHaveLength(1)
    })
  })

  it('queues message offline and displays it optimistically when network is offline', async () => {
    const { enqueueMessageMutation } = await import('@/lib/offlineSync')
    vi.stubGlobal('navigator', {
      ...window.navigator,
      onLine: false,
    })
    // Ohne Netz scheitert das Relais — daran und an nichts anderem erkennt der
    // Sendepfad seit 09/2026, dass er einreihen muss. `navigator.onLine` allein
    // reichte vorher und war der Grund, warum im Tauri-Fenster unter Windows
    // Nachrichten stillschweigend liegenblieben, obwohl das Netz stand.
    vi.mocked(socialApi.relayE2eeEnvelope).mockRejectedValue(new TypeError('Failed to fetch'))

    vi.mocked(socialApi.getFriends).mockResolvedValue([
      {
        id: 1,
        user_id: 302,
        username: 'offline_partner',
        avatar_url: null,
        status: 'accepted',
        presence: { status: 'offline', device_type: 'web' },
      },
    ])
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([])

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /offline_partner/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /offline_partner/i }))

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Nachricht schreiben/i)).toBeInTheDocument()
    })

    const input = screen.getByPlaceholderText(/Nachricht schreiben/i)
    fireEvent.change(input, { target: { value: 'Offline gesendete Nachricht' } })

    const sendBtn = screen.getByRole('button', { name: 'Senden' })
    fireEvent.click(sendBtn)

    await waitFor(() => {
      expect(enqueueMessageMutation).toHaveBeenCalledWith(
        expect.objectContaining({
          blind_mailbox_id: 'test-blind-mailbox',
          ciphertext_envelope: expect.any(String),
          client_uuid: expect.stringMatching(/^msg-|[0-9a-f-]+$/),
        })
      )
    })

    // Optimistische Anzeige muss sofort im Chat sichtbar sein
    await waitFor(() => {
      expect(screen.getByText('Offline gesendete Nachricht')).toBeInTheDocument()
    })

    vi.mocked(socialApi.relayE2eeEnvelope).mockReset()
    vi.unstubAllGlobals()
  })

  it('sendet trotzdem, wenn das System fälschlich offline meldet', async () => {
    // Vom Betreiber gemeldet: im Tauri-Fenster unter Windows stand die App auf
    // offline, obwohl das Netz lief, und die Nachrichten kamen nie an. Bis
    // 09/2026 entschied `navigator.onLine` hier, ob überhaupt ein Versuch
    // stattfindet. Diese Auskunft stammt vom Betriebssystem und sagt nichts
    // über die Erreichbarkeit des Backends — ein virtueller Netzadapter reicht,
    // damit sie falsch ist. Ob es geht, weiss nur der Versuch.
    const { enqueueMessageMutation } = await import('@/lib/offlineSync')
    vi.mocked(enqueueMessageMutation).mockClear()
    vi.stubGlobal('navigator', { ...window.navigator, onLine: false })
    vi.mocked(socialApi.relayE2eeEnvelope).mockResolvedValue({ success: true, id: 4711 } as any)

    vi.mocked(socialApi.getFriends).mockResolvedValue([
      {
        id: 1,
        user_id: 302,
        username: 'offline_partner',
        avatar_url: null,
        status: 'accepted',
        presence: { status: 'online', device_type: 'web' },
      },
    ])
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([])

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /offline_partner/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /offline_partner/i }))

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Nachricht schreiben/i)).toBeInTheDocument()
    })

    fireEvent.change(screen.getByPlaceholderText(/Nachricht schreiben/i), {
      target: { value: 'Geht trotzdem raus' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Senden' }))

    await waitFor(() => {
      expect(socialApi.relayE2eeEnvelope).toHaveBeenCalled()
    })
    expect(enqueueMessageMutation).not.toHaveBeenCalled()

    vi.mocked(socialApi.relayE2eeEnvelope).mockReset()
    vi.unstubAllGlobals()
  })

  it('zeigt exakte Drei-Stufen-Zustellung: 1 Strich (nicht angekommen), 2 graue Striche (zugestellt), 2 blaue Striche (gelesen)', async () => {
    // Initial message from current user (id: 1), not yet acknowledged
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValueOnce([
      {
        id: 50,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-msg-50',
        created_at: '2026-09-08T14:00:00Z',
      },
    ])

    testKlartext.mockImplementation(async (envelope) => {
      if (envelope === 'ciphertext-msg-50') {
        return JSON.stringify({
          sender_id: 1,
          text: 'Hallo Alice, ist das angekommen?',
          timestamp: '2026-09-08T14:00:00Z',
        })
      }
      if (envelope === 'ciphertext-delivery-receipt') {
        return JSON.stringify({
          type: 'delivery_receipt',
          delivered_up_to_id: 50,
          receiver_id: 101,
        })
      }
      if (envelope === 'ciphertext-read-receipt') {
        return JSON.stringify({
          type: 'read_receipt',
          read_up_to_id: 50,
          reader_id: 101,
        })
      }
      return 'Unknown'
    })

    render(
      <MemoryRouter>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /alice/i })).toBeInTheDocument()
    })
    fireEvent.click(screen.getByRole('button', { name: /alice/i }))

    // 1. Initialer Zustand: 1 grauer Strich (noch nicht beim Empfänger angekommen)
    await waitFor(() => {
      expect(screen.getByText('Hallo Alice, ist das angekommen?')).toBeInTheDocument()
      expect(screen.getByTitle('Noch nicht zugestellt')).toBeInTheDocument()
    })

    // 2. Zwischensprung: Bob empfängt Nachricht (Zustellbestätigung -> 2 graue Striche)
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValueOnce([
      {
        id: 50,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-msg-50',
        created_at: '2026-09-08T14:00:00Z',
      },
      {
        id: 51,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-delivery-receipt',
        created_at: '2026-09-08T14:00:05Z',
      },
    ])

    window.dispatchEvent(
      new CustomEvent('msm:sync-event', {
        detail: {
          type: 'e2ee_blind_message',
          blind_mailbox_id: 'test-blind-mailbox',
          is_control: true,
          control_type: 'delivery_receipt',
        },
      })
    )

    await waitFor(() => {
      expect(screen.getByTitle('Zugestellt')).toBeInTheDocument()
    })

    // 3. Gelesen: Bob öffnet den Chat (Lesebestätigung -> 2 blaue Striche)
    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValueOnce([
      {
        id: 50,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-msg-50',
        created_at: '2026-09-08T14:00:00Z',
      },
      {
        id: 51,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-delivery-receipt',
        created_at: '2026-09-08T14:00:05Z',
      },
      {
        id: 52,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-read-receipt',
        created_at: '2026-09-08T14:00:10Z',
      },
    ])

    window.dispatchEvent(
      new CustomEvent('msm:sync-event', {
        detail: {
          type: 'e2ee_blind_message',
          blind_mailbox_id: 'test-blind-mailbox',
          is_control: true,
          control_type: 'read_receipt',
        },
      })
    )

    await waitFor(() => {
      expect(screen.getByTitle(i18n.t('messenger.stateRead'))).toBeInTheDocument()
    })
  })

  it('renders outgoing messages optimistically and clears input immediately without blocking', async () => {
    let resolveRelay: (value: any) => void
    const relayPromise = new Promise((resolve) => {
      resolveRelay = resolve
    })
    vi.mocked(socialApi.relayE2eeEnvelope).mockReturnValue(relayPromise as any)

    render(
      <MemoryRouter initialEntries={['/chat?userId=101']}>
        <Messenger />
      </MemoryRouter>
    )

    const input = await screen.findByPlaceholderText(i18n.t('messenger.writePlaceholder'))
    fireEvent.change(input, { target: { value: 'Sofortige optimistische Nachricht' } })

    const sendButton = screen.getByTitle('Senden')
    fireEvent.click(sendButton)

    // Input must be cleared synchronously / immediately
    expect((input as HTMLTextAreaElement).value).toBe('')
    // Message bubble must appear optimistically before relay resolves
    expect(screen.getByText('Sofortige optimistische Nachricht')).toBeInTheDocument()

    // Resolve backend relay cleanly
    resolveRelay!({ success: true, id: 999 })
  })

  it('synchronously hydrates conversation messages from cache on switch without flashing empty state', async () => {
    render(
      <MemoryRouter initialEntries={['/chat?userId=101']}>
        <Messenger />
      </MemoryRouter>
    )

    const input = await screen.findByPlaceholderText(i18n.t('messenger.writePlaceholder'))
    vi.mocked(socialApi.relayE2eeEnvelope).mockResolvedValue({ success: true, id: 1001 } as any)

    fireEvent.change(input, { target: { value: 'Nachricht vor Wechsel' } })
    fireEvent.click(screen.getByTitle('Senden'))

    await waitFor(() => {
      expect(screen.getByText('Nachricht vor Wechsel')).toBeInTheDocument()
    })

    // Now re-click / switch back to Alice: sessionChatCache is populated, false empty state should never appear
    const contactItems = await screen.findAllByText('alice')
    fireEvent.click(contactItems[0])

    expect(screen.queryByText('Noch keine Nachrichten. Schreibe die erste Nachricht!')).not.toBeInTheDocument()
    expect(screen.getByText('Nachricht vor Wechsel')).toBeInTheDocument()
  })

  it('K-1: verwirft gefälschte sender_id im 1:1 Direktchat (keine Identitätsfälschung)', async () => {
    const { einpackenDr } = await import('@/services/ratchetSitzung') as any
    // Envelope from Alice (101), but payload falsely claims sender_id: 1 (current user)
    const forgedEnvelope = einpackenDr(
      JSON.stringify({
        sender_id: 1,
        text: 'Gefälschte Nachricht',
        client_uuid: 'forged-uuid-1',
        timestamp: '2026-09-08T12:00:00Z',
      }),
      101,
    )

    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 501,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: forgedEnvelope,
        created_at: '2026-09-08T12:00:00Z',
      },
    ])

    render(
      <MemoryRouter initialEntries={['/chat?userId=101']}>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /alice/i })).toBeInTheDocument()
    })

    // Gefälschte Nachricht darf im Chatverlauf nicht angezeigt werden
    expect(screen.queryByText('Gefälschte Nachricht')).not.toBeInTheDocument()
  })

  it('K-2: verhindert Bearbeiten fremder Nachrichten durch Dritte', async () => {
    const { einpackenDr } = await import('@/services/ratchetSitzung') as any
    // Nachricht von Alice (101)
    const originalEnvelope = einpackenDr(
      JSON.stringify({
        sender_id: 101,
        text: 'Originalnachricht von Alice',
        client_uuid: 'alice-msg-1',
        timestamp: '2026-09-08T12:00:00Z',
      }),
      101,
    )

    // Unberechtigter Änderungsversuch von Charlie (102)
    const maliciousEditEnvelope = einpackenDr(
      JSON.stringify({
        type: 'edit_message',
        target_client_uuid: 'alice-msg-1',
        actor_id: 102,
        new_text: 'Gehackter Text',
        edited_at: '2026-09-08T12:01:00Z',
      }),
      101,
    )

    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 601,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: originalEnvelope,
        created_at: '2026-09-08T12:00:00Z',
      },
      {
        id: 602,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: maliciousEditEnvelope,
        created_at: '2026-09-08T12:01:00Z',
      },
    ])

    render(
      <MemoryRouter initialEntries={['/chat?userId=101']}>
        <Messenger />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /alice/i })).toBeInTheDocument()
    })

    // Original bleibt erhalten, Manipulierter Text wird nicht übernommen
    await waitFor(() => {
      expect(screen.getByText('Originalnachricht von Alice')).toBeInTheDocument()
    })
    expect(screen.queryByText('Gehackter Text')).not.toBeInTheDocument()
  })

  it('H-6: reiht nachfolgende Ratchet-Nachricht ein wenn dr-init fehlschlägt', async () => {
    const { baueZustellungen } = await import('@/services/ratchetSitzung')
    const { enqueueMessageMutation } = await import('@/lib/offlineSync')
    vi.mocked(enqueueMessageMutation).mockClear()

    // baueZustellungen liefert Bootstrap + Nachricht
    vi.mocked(baueZustellungen).mockResolvedValueOnce([
      {
        empfaengerId: 101,
        zielGeraet: 'dev-1',
        bootstrap: 'sv-e2ee-dr-v1:bootstrap-data',
        nachricht: 'sv-e2ee-dr-v1:1.testgeraet.dev-1.msg-data',
        clientUuid: 'msg-uuid-1#dev-1',
        bootstrapClientUuid: 'msg-uuid-1#idev-1',
      },
    ])

    // dr-init scheitert beim Relaying
    vi.mocked(socialApi.relayE2eeEnvelope).mockImplementation(async (auftrag: any) => {
      if (auftrag.control_type === 'dr-init' || auftrag.is_control) {
        throw new Error('Netzwerkfehler bei dr-init')
      }
      return { id: 701 }
    })

    render(
      <MemoryRouter initialEntries={['/chat?userId=101']}>
        <Messenger />
      </MemoryRouter>
    )

    const input = await screen.findByPlaceholderText(i18n.t('messenger.writePlaceholder'))
    fireEvent.change(input, { target: { value: 'Nachricht mit Session-Init' } })
    fireEvent.click(screen.getByTitle('Senden'))

    await waitFor(() => {
      // Sowohl dr-init als auch die abhängige Nachricht müssen eingereiht werden
      expect(enqueueMessageMutation).toHaveBeenCalledTimes(2)
    })
    // Die Nachricht selbst darf NICHT über den Relay gesendet worden sein (nur dr-init wurde versucht)
    expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledTimes(1)
  })

  /**
   * Der Fall, den die Ratchet-Prüfung nicht erreicht.
   *
   * Steuerpakete laufen im Direktchat bewusst über den Hybridumschlag und
   * nicht durch den Ratchet — Begründung in `useKonversation.baueSteuerversand`.
   * Ein Hybridumschlag trägt keinen Absenderkopf, also stand dort `actor_id`
   * allein, und die Gegenseite konnte damit meine eigene Nachricht
   * umschreiben. Die Prüfung gegen `lesung.vonKonto` griff hier nie, weil es
   * auf diesem Weg kein `vonKonto` gibt.
   */
  const hybridFaelschung = async (kontenMitSchluessel: number[]) => {
    const { decryptE2eeHybridWithKeyring } = await import('@/services/e2eeCrypto')
    kontenMitSignatur = kontenMitSchluessel

    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 701,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'ciphertext-meins',
        created_at: '2026-09-08T12:00:00Z',
      },
      {
        id: 702,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'sv-e2ee-hybrid-v1:steuerpaket',
        created_at: '2026-09-08T12:01:00Z',
      },
    ] as any)

    testKlartext.mockImplementation(async () =>
      JSON.stringify({
        sender_id: 1,
        text: 'Meine echte Nachricht',
        client_uuid: 'meine-msg-1',
        timestamp: '2026-09-08T12:00:00Z',
      })
    )

    vi.mocked(decryptE2eeHybridWithKeyring).mockImplementation(async () =>
      JSON.stringify({
        type: 'edit_message',
        target_client_uuid: 'meine-msg-1',
        actor_id: 1,
        new_text: 'Ich habe gekündigt',
        edited_at: '2026-09-08T12:01:00Z',
      })
    )

    render(
      <MemoryRouter initialEntries={['/chat?userId=101']}>
        <Messenger />
      </MemoryRouter>
    )
  }

  it('K-2: verwirft ein Steuerpaket über den Hybridpfad, das sich als ich ausgibt', async () => {
    // Mein Konto führt einen Signaturschlüssel. Eine unsignierte Nutzlast in
    // meinem Namen ist damit keine Nachsicht wert, sondern eine Fälschung.
    // Meine echte Nachricht trägt deshalb einen Beleg — ohne ihn fiele sie
    // unter dieselbe Schranke.
    beglaubigt.set('meine-msg-1', 1)
    await hybridFaelschung([1])

    await waitFor(() => {
      expect(screen.getByText('Meine echte Nachricht')).toBeInTheDocument()
    })
    expect(screen.queryByText('Ich habe gekündigt')).not.toBeInTheDocument()
  })

  /**
   * Die Gegenprobe, und sie gehört dazu: ohne sie liesse sich nicht
   * unterscheiden, ob der Test oben die Schranke prüft oder nur ein Gerüst,
   * das die Bearbeitung ohnehin nie anwendet. Führt niemand einen
   * Signaturschlüssel, gilt weiterhin der alte Stand — sonst stünde jede
   * Installation, die seit der Umstellung nicht neu gestartet wurde, ohne
   * Bearbeiten da.
   */
  it('lässt dasselbe Paket durch, solange niemand beglaubigen kann', async () => {
    await hybridFaelschung([])

    await waitFor(() => {
      expect(screen.getByText('Ich habe gekündigt')).toBeInTheDocument()
    })
  })

  /**
   * Die Downgrade-Schranke am Ratchet.
   *
   * Eine Sitzung aus der Zeit vor der Unterschrift am Sitzungsaufbau wurde nie
   * geprüft. War sie untergeschoben, läuft sie weiter, und ihr Kopf nennt das
   * Konto, das der Fälscher gewählt hat — unterschreiben kann er in dessen
   * Namen aber nicht.
   *
   * Die eigene Nachricht daneben ist der Beweis, dass der Durchlauf fertig
   * ist: ohne sie bestünde der erste Test auch, wenn noch gar nichts gelesen
   * wäre.
   */
  const ratchetOhneBeleg = async (kontenMitSchluessel: number[]) => {
    const { einpackenDr } = (await import('@/services/ratchetSitzung')) as any
    kontenMitSignatur = kontenMitSchluessel

    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 801,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: einpackenDr(
          JSON.stringify({
            sender_id: 1,
            text: 'Von meinem Zweitgerät',
            client_uuid: 'zweitgeraet-1',
            timestamp: '2026-09-08T12:00:00Z',
          }),
          1,
        ),
        created_at: '2026-09-08T12:00:00Z',
      },
      {
        id: 802,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: einpackenDr(
          JSON.stringify({
            sender_id: 101,
            text: 'Über eine alte Sitzung',
            client_uuid: 'alte-sitzung-1',
            timestamp: '2026-09-08T12:01:00Z',
          }),
          101,
        ),
        created_at: '2026-09-08T12:01:00Z',
      },
    ] as any)

    render(
      <MemoryRouter initialEntries={['/chat?userId=101']}>
        <Messenger />
      </MemoryRouter>
    )
  }

  it('verwirft eine unsignierte Ratchet-Nachricht eines Kontos, das unterschreibt', async () => {
    await ratchetOhneBeleg([101])

    await waitFor(() => {
      expect(screen.getByText('Von meinem Zweitgerät')).toBeInTheDocument()
    })
    expect(screen.queryByText('Über eine alte Sitzung')).not.toBeInTheDocument()
  })

  it('zeigt dieselbe Nachricht, solange das Konto nicht unterschreiben kann', async () => {
    await ratchetOhneBeleg([])

    await waitFor(() => {
      expect(screen.getByText('Über eine alte Sitzung')).toBeInTheDocument()
    })
  })

  /**
   * Klartext ohne JSON-Hülle über den Ratchet.
   *
   * Der Rückfallweg für Altbestand nahm jeden solchen Text, und `[ME]:` am
   * Anfang machte ihn zur eigenen Nachricht — gleich, von welchem Gerät der
   * Ratchet ihn brachte. Ohne Hülle gibt es auch keine Unterschrift: dieselbe
   * Schranke wie oben.
   */
  const ratchetOhneHuelle = async (kontenMitSchluessel: number[], text: string) => {
    const { einpackenDr } = (await import('@/services/ratchetSitzung')) as any
    kontenMitSignatur = kontenMitSchluessel

    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 811,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: einpackenDr(
          JSON.stringify({
            sender_id: 1,
            text: 'Von meinem Zweitgerät',
            client_uuid: 'zweitgeraet-2',
            timestamp: '2026-09-08T12:00:00Z',
          }),
          1,
        ),
        created_at: '2026-09-08T12:00:00Z',
      },
      {
        id: 812,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: einpackenDr(text, 101),
        created_at: '2026-09-08T12:01:00Z',
      },
    ] as any)

    render(
      <MemoryRouter initialEntries={['/chat?userId=101']}>
        <Messenger />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText('Von meinem Zweitgerät')).toBeInTheDocument()
    })
  }

  it('verwirft Klartext ohne Hülle über den Ratchet, wenn das Konto unterschreibt', async () => {
    await ratchetOhneHuelle([101], 'Einfach nur Text')
    expect(screen.queryByText('Einfach nur Text')).not.toBeInTheDocument()
  })

  it('macht die Gegenseite über den Ratchet nie zu mir, auch nicht mit [ME]:', async () => {
    // Ein Konto aus der Zeit vor der Unterschrift darf weiter Klartext ohne
    // Hülle schicken — aber der Absender steht im Ratchet, nicht im Text.
    await ratchetOhneHuelle([], '[ME]:Ich kündige')
    await waitFor(() => {
      expect(screen.getByText('[ME]:Ich kündige')).toBeInTheDocument()
    })
  })

  /**
   * Ein Hybridumschlag im Direktchat hat keinen Absenderkopf. Versiegeln kann
   * ihn jeder, der den Geräteschlüssel des Empfängers kennt — der Server
   * allemal. Ohne Unterschrift stand er trotzdem als Nachricht des Gegenübers
   * im Verlauf.
   */
  const hybridOhneBeleg = async (kontenMitSchluessel: number[]) => {
    const { einpackenDr } = (await import('@/services/ratchetSitzung')) as any
    const { decryptE2eeHybridWithKeyring } = await import('@/services/e2eeCrypto')
    kontenMitSignatur = kontenMitSchluessel

    vi.mocked(socialApi.fetchE2eeEnvelopes).mockResolvedValue([
      {
        id: 821,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: einpackenDr(
          JSON.stringify({
            sender_id: 1,
            text: 'Von meinem Zweitgerät',
            client_uuid: 'zweitgeraet-3',
            timestamp: '2026-09-08T12:00:00Z',
          }),
          1,
        ),
        created_at: '2026-09-08T12:00:00Z',
      },
      {
        id: 822,
        blind_mailbox_id: 'test-blind-mailbox',
        ciphertext_envelope: 'sv-e2ee-hybrid-v1:erfunden',
        client_uuid: 'erfunden-1',
        created_at: '2026-09-08T12:01:00Z',
      },
    ] as any)
    vi.mocked(decryptE2eeHybridWithKeyring).mockImplementation(async () =>
      JSON.stringify({
        text: 'Vom Server erfunden',
        client_uuid: 'erfunden-1',
        timestamp: '2026-09-08T12:01:00Z',
      })
    )

    render(
      <MemoryRouter initialEntries={['/chat?userId=101']}>
        <Messenger />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText('Von meinem Zweitgerät')).toBeInTheDocument()
    })
  }

  it('verwirft eine unsignierte Hybridnachricht, wenn das Gegenüber unterschreibt', async () => {
    await hybridOhneBeleg([101])
    expect(screen.queryByText('Vom Server erfunden')).not.toBeInTheDocument()
  })

  it('zeigt dieselbe Hybridnachricht, solange das Gegenüber nicht unterschreiben kann', async () => {
    await hybridOhneBeleg([])
    await waitFor(() => {
      expect(screen.getByText('Vom Server erfunden')).toBeInTheDocument()
    })
  })

  /**
   * Wer die Verfallsfrist stellen darf — und wer sie gestellt hat.
   *
   * Bis 09/2026 las der `retention`-Zweig seinen Urheber ungeprüft aus
   * `actor_id`, als einziges Steuerpaket neben Reaktion und Anheften, die
   * längst über `urheberVon` liefen. Jedes Mitglied hält den Gruppenschlüssel
   * und unterschreibt seine eigene Nutzlast; es konnte also bei allen die
   * Zeile „<Eigentümer> hat eingestellt …" erzeugen. Ein Recht gab es auch
   * nicht: jedes Mitglied stellte die Frist für die ganze Gruppe.
   *
   * Unterschrieben wird hier mit echtem ECDSA. Nachgebildet ist nur das
   * Geräteverzeichnis — eine Serverauskunft, keine Kryptographie.
   */
  describe('Verfallsfrist: wer sie stellen darf', () => {
    const GRUPPE = 'test-group-blind-mailbox'
    const DIREKT = 'test-blind-mailbox'
    const ICH = 1
    const ALICE = 101
    const BERT = 102
    const OLGA = 103

    /** `konto:gerät` → öffentlicher Signaturschlüssel, wie der Server ihn herausgibt. */
    const verzeichnis = new Map<string, string>()
    const paare = new Map<number, SignaturPaar>()
    let warnung: ReturnType<typeof vi.spyOn>

    beforeEach(() => {
      verzeichnis.clear()
      paare.clear()
      vi.mocked(signaturSchluesselVon).mockImplementation(
        async (konto: number, geraet: string) => verzeichnis.get(`${konto}:${geraet}`) ?? null,
      )
      // Alle Beteiligten führen einen Signaturschlüssel. Eine unsignierte
      // Nutzlast in ihrem Namen ist damit keine Nachsicht wert.
      kontenMitSignatur = [ICH, ALICE, BERT, OLGA]
      // Die Fälschungstests oben lassen ihre Stellvertreter stehen.
      vi.mocked(decryptE2eeHybridWithKeyring).mockImplementation(async () => 'Hallo Hybrid')
      testKlartext.mockImplementation(async () => 'Hallo Welt')
      warnung = vi.spyOn(console, 'warn').mockImplementation(() => {})
    })

    afterEach(() => {
      vi.mocked(signaturSchluesselVon).mockImplementation(async () => null)
      warnung.mockRestore()
    })

    /** Eine Nutzlast, unterschrieben vom Gerät dieses Kontos. */
    async function unterschrieben(
      konto: number,
      mailbox: string,
      roh: Record<string, unknown>,
    ): Promise<string> {
      let paar = paare.get(konto)
      if (!paar) {
        paar = await erzeugeSignaturPaar()
        paare.set(konto, paar)
        verzeichnis.set(`${konto}:geraet-${konto}`, paar.publicKeyJwk)
      }
      vi.mocked(eigenesGeraet).mockResolvedValueOnce({
        kennung: `geraet-${konto}`,
        paar: identitaet.sendPair,
        signaturPaar: paar,
      } as any)
      return JSON.stringify(await signiereNutzlast(mailbox, konto, roh))
    }

    const frist = (konto: number, dauer: number, actor: number, zeitpunkt: string) =>
      unterschrieben(konto, GRUPPE, {
        type: 'retention',
        dauer,
        actor_id: actor,
        zeitpunkt,
        client_uuid: `ctrl-frist-${konto}-${zeitpunkt}`,
      })

    /** Eine gewöhnliche Nachricht danach: ist sie da, ist der Durchlauf durch. */
    const danach = (text: string) =>
      unterschrieben(OLGA, GRUPPE, {
        sender_id: OLGA,
        text,
        client_uuid: `nachricht-${text}`,
        timestamp: '2026-09-23T10:09:00Z',
      })

    function mitglied(userId: number, username: string, role: string, darf: boolean) {
      return {
        user_id: userId,
        username,
        role,
        permissions: null,
        can_set_disappearing_messages: darf,
        joined_at: '2026-09-01T00:00:00Z',
      }
    }

    /** Olga ist Eigentümerin, ich bin gewöhnliches Mitglied. */
    function verfallsgruppe({ aliceDarf, ichDarf = false }: { aliceDarf: boolean; ichDarf?: boolean }) {
      return {
        id: 77,
        name: 'Verfallsgruppe',
        description: null,
        avatar_url: null,
        invite_code: null,
        owner_user_id: OLGA,
        member_count: 4,
        role: 'member',
        default_permissions: 'attach_media,invite_members,send_messages',
        can_set_disappearing_messages: ichDarf,
        created_at: '2026-09-07T00:00:00Z',
        members: [
          mitglied(ICH, 'me', 'member', ichDarf),
          mitglied(ALICE, 'alice', 'member', aliceDarf),
          mitglied(BERT, 'bert', 'member', false),
          mitglied(OLGA, 'olga', 'owner', true),
        ],
      } as any
    }

    /**
     * Die Gruppenmailbox mit genau diesen Nutzlasten, in dieser Reihenfolge.
     *
     * Der Gruppenschlüssel ist hier nicht Gegenstand, geprüft wird, was nach
     * dem Öffnen geschieht. Jeder Umschlag steht deshalb schon im
     * Klartext-Zwischenspeicher, wie nach einem früheren Abruf; `useKonversation`
     * nimmt ihn von dort, und zwar ohne `vonKonto`. In der Gruppe belegt also
     * allein die Unterschrift an der Nutzlast, wer geschrieben hat.
     */
    function gruppenpost(nutzlasten: string[]) {
      const umschlaege = nutzlasten.map((plain, i) => {
        const id = 900 + i
        mockEnvelopeCache.set(id, { plain, ok: true })
        return {
          id,
          blind_mailbox_id: GRUPPE,
          ciphertext_envelope: `gruppenumschlag-${id}`,
          client_uuid: `umschlag-${id}`,
          created_at: `2026-09-23T10:0${i}:00Z`,
        }
      })
      vi.mocked(socialApi.fetchE2eeEnvelopes).mockImplementation(async (kennung: string) =>
        kennung === GRUPPE ? (umschlaege as any) : [],
      )
    }

    const oeffneGruppe = () =>
      render(
        <MemoryRouter initialEntries={['/chat?groupId=77']}>
          <Messenger />
        </MemoryRouter>
      )

    const gestellt = (name: string, stufe: 'h24' | 'd7') =>
      i18n.t('messenger.retentionSetOther', {
        name,
        frist: i18n.t(`messenger.retentionDative.${stufe}`),
      })

    const vonMirGestellt = (stufe: 'h24' | 'd7') =>
      i18n.t('messenger.retentionSetSelf', {
        name: i18n.t('messenger.retentionYou'),
        frist: i18n.t(`messenger.retentionDative.${stufe}`),
      })

    it('verwirft eine Umstellung, deren actor_id ein anderes Mitglied nennt als die Unterschrift', async () => {
      // Alice darf die Frist stellen — geprüft wird hier allein die Fälschung.
      // Sie unterschreibt selbst und behauptet, Olga habe umgestellt.
      vi.mocked(socialApi.getGroups).mockResolvedValue([verfallsgruppe({ aliceDarf: true })])
      gruppenpost([
        await frist(ALICE, 86_400, OLGA, '2026-09-23T10:00:00.000Z'),
        await danach('Nach der Umstellung'),
      ])

      oeffneGruppe()
      await screen.findByText('Nach der Umstellung')

      expect(screen.queryByText(gestellt('olga', 'h24'))).not.toBeInTheDocument()
      // Nichts festgehalten: ein verworfenes Paket darf auch nicht als
      // neuester Stand in der Ablage stehen.
      expect(verfallStand(GRUPPE)).toEqual({ sekunden: 0, stand: '' })
      expect(warnung).toHaveBeenCalledWith(
        '[Messenger] Dropping retention packet with forged actor_id:',
        OLGA,
      )
    })

    it('lässt die Umstellung eines Mitglieds ohne das Recht folgenlos', async () => {
      // Bert unterschreibt echt und nennt sich selbst — ihm fehlt nur das Recht.
      vi.mocked(socialApi.getGroups).mockResolvedValue([verfallsgruppe({ aliceDarf: false })])
      gruppenpost([
        await frist(BERT, 86_400, BERT, '2026-09-23T10:00:00.000Z'),
        await danach('Nach der Umstellung'),
      ])

      oeffneGruppe()
      await screen.findByText('Nach der Umstellung')

      expect(screen.queryByText(gestellt('bert', 'h24'))).not.toBeInTheDocument()
      expect(verfallStand(GRUPPE)).toEqual({ sekunden: 0, stand: '' })
    })

    /**
     * Davor liegt Berts Versuch, die Frist festzunageln: ohne Recht, „aus",
     * mit einem Zeitpunkt weit in der Zukunft. Erreichte er
     * `uebernehmeVerfall`, stünde er dort als neuester Stand, und jede spätere
     * berechtigte Umstellung verlöre gegen ihn — lautlos, denn „aus" auf „aus"
     * schreibt keine Zeile.
     */
    it('übernimmt die Umstellung eines Mitglieds mit dem Recht und nennt, wer unterschrieben hat', async () => {
      vi.mocked(socialApi.getGroups).mockResolvedValue([verfallsgruppe({ aliceDarf: true })])
      gruppenpost([
        await frist(BERT, 0, BERT, '2099-01-01T00:00:00.000Z'),
        await frist(ALICE, 86_400, ALICE, '2026-09-23T10:01:00.000Z'),
        await danach('Nach der Umstellung'),
      ])

      oeffneGruppe()

      expect(await screen.findByText(gestellt('alice', 'h24'))).toBeInTheDocument()
      expect(verfallStand(GRUPPE)).toEqual({
        sekunden: 86_400,
        stand: '2026-09-23T10:01:00.000Z',
      })
    })

    /**
     * Im Direktchat gibt es keine Rollen: die Umstellung der Gegenseite gilt
     * ohne jede Marke. Danach schickt Alice eine zweite und behauptet, ich
     * hätte sie gestellt. Bis 09/2026 stand dann „Du hast eingestellt …" in
     * meinem eigenen Verlauf, über eine Frist, die ich nie gewählt habe.
     *
     * Steuerpakete laufen dort über den Hybridumschlag, ohne Absenderkopf —
     * den Urheber belegt auch hier allein die Unterschrift.
     */
    it('lässt im Direktchat ohne Recht umstellen und nennt nur, wer unterschrieben hat', async () => {
      const umschlaege = new Map([
        [
          'sv-e2ee-hybrid-v1:frist-echt',
          await unterschrieben(ALICE, DIREKT, {
            type: 'retention',
            dauer: 86_400,
            actor_id: ALICE,
            zeitpunkt: '2026-09-23T10:00:00.000Z',
            client_uuid: 'ctrl-frist-echt',
          }),
        ],
        [
          'sv-e2ee-hybrid-v1:frist-in-meinem-namen',
          await unterschrieben(ALICE, DIREKT, {
            type: 'retention',
            dauer: 604_800,
            actor_id: ICH,
            zeitpunkt: '2026-09-23T10:01:00.000Z',
            client_uuid: 'ctrl-frist-falsch',
          }),
        ],
      ])
      vi.mocked(decryptE2eeHybridWithKeyring).mockImplementation(async (umschlag: string) => {
        const klartext = umschlaege.get(umschlag)
        if (!klartext) throw new Error('nicht für dieses Gerät')
        return klartext
      })
      // Auch die Marke trägt Alices Unterschrift, wie jede Nutzlast eines
      // Kontos mit Signaturschlüssel: eine unsignierte weist die
      // Downgrade-Schranke am Ratchet ab, und der Test wartete vergeblich.
      const { einpackenDr } = (await import('@/services/ratchetSitzung')) as any
      const nachher = einpackenDr(
        await unterschrieben(ALICE, DIREKT, {
          sender_id: ALICE,
          text: 'Nach der Umstellung',
          client_uuid: 'alice-nachher',
          timestamp: '2026-09-23T10:02:00Z',
        }),
        ALICE,
      )
      vi.mocked(socialApi.fetchE2eeEnvelopes).mockImplementation(async (kennung: string) =>
        kennung === DIREKT
          ? ([
              {
                id: 801,
                blind_mailbox_id: DIREKT,
                ciphertext_envelope: 'sv-e2ee-hybrid-v1:frist-echt',
                client_uuid: 'ctrl-frist-echt#0',
                created_at: '2026-09-23T10:00:00Z',
              },
              {
                id: 802,
                blind_mailbox_id: DIREKT,
                ciphertext_envelope: 'sv-e2ee-hybrid-v1:frist-in-meinem-namen',
                client_uuid: 'ctrl-frist-falsch#0',
                created_at: '2026-09-23T10:01:00Z',
              },
              {
                id: 803,
                blind_mailbox_id: DIREKT,
                ciphertext_envelope: nachher,
                client_uuid: 'alice-nachher',
                created_at: '2026-09-23T10:02:00Z',
              },
            ] as any)
          : [],
      )

      // Über die Kontaktliste geöffnet und nicht über `?userId=`: dort steht
      // der Kontakt beim ersten Abruf noch als „Benutzer #101" da, und die
      // Zeile trüge diesen Platzhalter statt des Namens.
      render(
        <MemoryRouter>
          <Messenger />
        </MemoryRouter>
      )
      fireEvent.click(await screen.findByText('alice'))

      expect(await screen.findByText(gestellt('alice', 'h24'))).toBeInTheDocument()
      await screen.findByText('Nach der Umstellung')
      expect(screen.queryByText(vonMirGestellt('d7'))).not.toBeInTheDocument()
      expect(verfallStand(DIREKT)).toEqual({
        sekunden: 86_400,
        stand: '2026-09-23T10:00:00.000Z',
      })
    })

    it('sperrt die Auswahl in einer Gruppe, in der mir das Recht fehlt', async () => {
      vi.mocked(socialApi.getGroups).mockResolvedValue([verfallsgruppe({ aliceDarf: false })])
      gruppenpost([await danach('Schon da')])

      oeffneGruppe()
      await screen.findByText('Schon da')
      await oeffneChatMenue()

      const eintrag = screen.getByText(i18n.t('messenger.disappearingMessages')).closest('button')!
      expect(eintrag).toBeDisabled()
      expect(eintrag).toHaveTextContent(i18n.t('messenger.retentionNoRight'))
      fireEvent.click(eintrag)
      // Die Auswahl der Stufen geht gar nicht erst auf.
      expect(screen.queryByText(i18n.t('messenger.retention.h24'))).not.toBeInTheDocument()
      expect(
        vi
          .mocked(socialApi.relayE2eeEnvelope)
          .mock.calls.some(([auftrag]) => (auftrag as any)?.control_type === 'retention'),
      ).toBe(false)
    })

    /**
     * Die Gegenprobe zur Sperre darüber: im Direktchat gibt es kein Recht, das
     * fehlen könnte. Eine Sperre, die nur nach der Gruppenmarke fragt, nähme
     * die Frist aus jedem Direktchat — und kein anderer Test fiele.
     */
    it('lässt die Frist im Direktchat einstellen, über den Steuerweg', async () => {
      render(
        <MemoryRouter initialEntries={['/chat?userId=101']}>
          <Messenger />
        </MemoryRouter>
      )
      await screen.findByPlaceholderText(i18n.t('messenger.writePlaceholder'))
      await oeffneChatMenue()

      const eintrag = screen.getByText(i18n.t('messenger.disappearingMessages')).closest('button')!
      expect(eintrag).toBeEnabled()
      fireEvent.click(eintrag)
      fireEvent.click(await screen.findByText(i18n.t('messenger.retention.h24')))

      expect(await screen.findByText(vonMirGestellt('h24'))).toBeInTheDocument()
      expect(socialApi.relayE2eeEnvelope).toHaveBeenCalledWith(
        expect.objectContaining({ is_control: true, control_type: 'retention' }),
      )
      expect(verfallStand(DIREKT).sekunden).toBe(86_400)
    })
  })

  describe('Schlüsselwarnungen und Sicherheitsnummer (Schritt 3)', () => {
    it('blendet eine Warnung ein, wenn ein Kontakt ein nicht freigegebenes Gerät hat', async () => {
      render(
        <MemoryRouter initialEntries={['/chat?userId=101']}>
          <Messenger />
        </MemoryRouter>
      )
      await screen.findByPlaceholderText(i18n.t('messenger.writePlaceholder'))

      expect(schluesselWarnungCallback).not.toBeNull()
      act(() => {
        schluesselWarnungCallback!({
          userId: 101,
          typ: 'unbestaetigt',
          geraete: ['dev-101'],
        })
      })

      expect(
        await screen.findByText(/hat ein Gerät, das nicht auf einem bekannten Gerät freigegeben wurde/),
      ).toBeInTheDocument()
    })

    it('blendet eine Warnung ein, wenn ein Konto-Neustart erkannt wird', async () => {
      render(
        <MemoryRouter initialEntries={['/chat?userId=101']}>
          <Messenger />
        </MemoryRouter>
      )
      await screen.findByPlaceholderText(i18n.t('messenger.writePlaceholder'))

      expect(schluesselWarnungCallback).not.toBeNull()
      act(() => {
        schluesselWarnungCallback!({
          userId: 101,
          typ: 'konto_neustart',
          geraete: ['dev-new-1', 'dev-new-2'],
        })
      })

      expect(
        await screen.findByText(/Alle Geräte von .* wurden ersetzt/),
      ).toBeInTheDocument()
    })

    it('warnt auch fürs eigene Konto — ein Gerät, das jemand mit deinem Passwort einträgt', async () => {
      render(
        <MemoryRouter initialEntries={['/chat?userId=101']}>
          <Messenger />
        </MemoryRouter>
      )
      await screen.findByPlaceholderText(i18n.t('messenger.writePlaceholder'))

      act(() => {
        schluesselWarnungCallback!({ userId: 1, typ: 'unbestaetigt', geraete: ['dieb-1'] })
      })

      expect(
        await screen.findByText(/Für dein Konto ist ein Gerät eingetragen, das keines deiner Geräte freigegeben hat/),
      ).toBeInTheDocument()
    })

    it('erlaubt das Öffnen der Sicherheitsnummer-Ansicht über das Chatmenü', async () => {
      render(
        <MemoryRouter initialEntries={['/chat?userId=101']}>
          <Messenger />
        </MemoryRouter>
      )
      await screen.findByPlaceholderText(i18n.t('messenger.writePlaceholder'))
      await oeffneChatMenue()

      const eintrag = screen.getByText(i18n.t('messenger.verifySafetyNumber')).closest('button')!
      expect(eintrag).toBeInTheDocument()
      fireEvent.click(eintrag)

      expect(await screen.findByText(i18n.t('messenger.safetyNumberModalTitle'))).toBeInTheDocument()
      expect(screen.getByText(i18n.t('messenger.safetyNumberModalDesc'))).toBeInTheDocument()
      expect(await screen.findByText('11111 22222 33333 44444')).toBeInTheDocument()
    })
  })

  /**
   * Stummschalten und Blockieren kommen aus eigenen Dialogen. Hier steht, dass
   * die Wahl im Dialog wirklich beim Store und in der Gesprächsliste ankommt.
   */
  describe('Stummschalten und Blockieren über das Chatmenü', () => {
    afterEach(() => {
      // Der Store lebt über den Test hinaus; `localStorage.clear()` erreicht
      // seinen Arbeitsspeicher nicht.
      useMessengerNotificationStore.setState({ mutedChats: {}, blockedUserIds: [], blockedProfiles: {} })
    })

    it('schaltet den offenen Chat für 8 Stunden stumm', async () => {
      render(
        <MemoryRouter initialEntries={['/chat?userId=101']}>
          <Messenger />
        </MemoryRouter>
      )
      await screen.findByPlaceholderText(i18n.t('messenger.writePlaceholder'))
      await oeffneChatMenue()
      fireEvent.click(screen.getByText(i18n.t('messenger.mute')).closest('button')!)
      fireEvent.click(await screen.findByText(i18n.t('messenger.mute8h')))

      await waitFor(() => {
        const fristen = Object.values(useMessengerNotificationStore.getState().mutedChats)
        expect(fristen).toHaveLength(1)
        expect(fristen[0]).toBeGreaterThan(Date.now() + 7 * 3600 * 1000)
      })
      await waitFor(() => {
        expect(screen.queryByText(i18n.t('messenger.mute8h'))).not.toBeInTheDocument()
      })
    })

    it('blockiert den Kontakt und nimmt das Gespräch aus der örtlichen Liste', async () => {
      await merkeGespraech(101, { username: 'alice' })
      render(
        <MemoryRouter initialEntries={['/chat?userId=101']}>
          <Messenger />
        </MemoryRouter>
      )
      await screen.findByPlaceholderText(i18n.t('messenger.writePlaceholder'))
      await oeffneChatMenue()
      fireEvent.click(screen.getByText(i18n.t('messenger.blockContact')).closest('button')!)
      fireEvent.click(await screen.findByRole('button', { name: i18n.t('messenger.block') }))

      await waitFor(() => {
        expect(useMessengerNotificationStore.getState().isBlocked(101)).toBe(true)
      })
      await waitFor(async () => {
        expect((await ladeGespraeche()).has(101)).toBe(false)
      })
    })

    it('hebt eine Blockierung auf, ohne das Gespräch anzufassen', async () => {
      useMessengerNotificationStore.setState({ blockedUserIds: [101] })
      await merkeGespraech(101, { username: 'alice' })
      render(
        <MemoryRouter initialEntries={['/chat?userId=101']}>
          <Messenger />
        </MemoryRouter>
      )
      // Blockiert gibt es kein Eingabefeld; das Menü steht trotzdem da. Der
      // Hinweisbalken unten trägt einen eigenen Aufheben-Knopf ohne Dialog,
      // deshalb wird im Menü gesucht.
      await oeffneChatMenue()
      const menue = screen.getByRole('dialog')
      fireEvent.click(within(menue).getByText(i18n.t('messenger.unblockContact')).closest('button')!)
      const titel = await screen.findByText(i18n.t('messenger.unblockTitle'))
      const frage = titel.closest<HTMLElement>('[role="dialog"]')!
      fireEvent.click(within(frage).getByRole('button', { name: i18n.t('messenger.unblock') }))

      await waitFor(() => {
        expect(useMessengerNotificationStore.getState().isBlocked(101)).toBe(false)
      })
      expect((await ladeGespraeche()).has(101)).toBe(true)
    })
  })
})

