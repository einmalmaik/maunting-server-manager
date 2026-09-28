import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import type { ActiveCallInfo, PendingGroupCallInfo } from '@/api/calls'
import { leereGruppenNamen, merkeGruppenName } from '@/services/gruppenName'

// Die Sprache festlegen: die Behauptungen unten prüfen deutsche Texte, und
// ohne diese Zeile entscheidet navigator.language der Testumgebung.
beforeAll(async () => {
  await i18n.changeLanguage('de')
})

const mockStore = {
  crossDeviceCall: null as ActiveCallInfo | null,
  activeGroupCalls: [] as PendingGroupCallInfo[],
  state: 'idle',
  checkActiveCall: vi.fn(),
  transferCallToThisDevice: vi.fn().mockResolvedValue(undefined),
  terminateCrossDeviceCall: vi.fn().mockResolvedValue(undefined),
  handleCrossDeviceEvent: vi.fn(),
  joinGroupCall: vi.fn().mockResolvedValue(undefined),
}

vi.mock('@/stores/useCallStore', () => ({
  useCallStore: () => mockStore,
}))

const { CrossDeviceCallBanner } = await import('./CrossDeviceCallBanner')

describe('CrossDeviceCallBanner', () => {
  const MOCK_CALL: ActiveCallInfo = {
    raum: 'raum-test-42',
    art: 'direkt',
    mode: 'audio',
    device_id: 'dev-phone-1',
    device_type: 'mobile',
    group_id: null,
    partner: {
      user_id: 2,
      username: 'Alice',
      avatar_url: null,
    },
    started_at: '2026-09-17T12:00:00Z',
  }

  beforeEach(() => {
    vi.clearAllMocks()
    // Der Namensspeicher liegt im Modulgedaechtnis, nicht nur in
    // `localStorage` — ohne dies traegt ein Test den Namen des vorigen.
    leereGruppenNamen()
    mockStore.crossDeviceCall = null
    mockStore.activeGroupCalls = []
    mockStore.state = 'idle'
  })

  afterEach(() => {
    cleanup()
  })

  it('rendert nichts wenn kein geräteübergreifender Anruf vorliegt', () => {
    mockStore.crossDeviceCall = null
    const { container } = render(<CrossDeviceCallBanner />)
    expect(container.firstChild).toBeNull()
  })

  it('rendert nichts wenn das lokale Gerät selbst bereits in einem Anruf ist', () => {
    mockStore.crossDeviceCall = MOCK_CALL
    mockStore.state = 'active'
    const { container } = render(<CrossDeviceCallBanner />)
    expect(container.firstChild).toBeNull()
  })

  it('rendert Banner mit Partner-Name und Gerätetyp wenn fremder Anruf aktiv ist', () => {
    mockStore.crossDeviceCall = MOCK_CALL
    mockStore.state = 'idle'
    render(<CrossDeviceCallBanner />)

    expect(screen.getByRole('region', { name: i18n.t('calls.activeOnOtherDevice') })).toBeTruthy()
    expect(screen.getByText(i18n.t('calls.alreadyInCall'))).toBeTruthy()
    expect(screen.getByText('Alice')).toBeTruthy()
    expect(screen.getByText(i18n.t('social.device.mobile'))).toBeTruthy()
    expect(screen.getByText(/Audio-Anruf/i)).toBeTruthy()
  })

  it('rendert Gruppen-Informationen wenn es sich um einen Gruppenanruf handelt', async () => {
    // Der Name kommt seit Stufe 6c aus dem oertlichen Namensspeicher und nicht
    // mehr aus dem Anrufhinweis: `chat_groups.name` ist entfernt.
    await merkeGruppenName(5, { name: 'Projektteam' })
    mockStore.crossDeviceCall = {
      ...MOCK_CALL,
      art: 'gruppe',
      group_id: 5,
      partner: null,
      device_type: 'desktop',
    }
    mockStore.state = 'idle'
    render(<CrossDeviceCallBanner />)

    expect(await screen.findByText('Projektteam')).toBeTruthy()
    expect(screen.getByText(i18n.t('social.device.desktop'))).toBeTruthy()
  })

  it('nennt eine unbekannte Gruppe verschluesselt statt sie zu erraten', async () => {
    // Ein Geraet, das diese Gruppe noch nie geoeffnet hat, hat ihren Namen
    // nicht — und der Server hat ihn auch nicht. Dann steht dort dasselbe wie
    // im Messenger, nicht etwa eine Kennung oder ein leerer Platz.
    mockStore.crossDeviceCall = {
      ...MOCK_CALL,
      art: 'gruppe',
      group_id: 77,
      partner: null,
      device_type: 'desktop',
    }
    render(<CrossDeviceCallBanner />)

    expect(await screen.findByText(i18n.t('messenger.groupSealed'))).toBeTruthy()
  })

  it('führt transferCallToThisDevice aus wenn man auf Beitreten klickt', () => {
    mockStore.crossDeviceCall = MOCK_CALL
    render(<CrossDeviceCallBanner />)

    const joinBtn = screen.getByRole('button', { name: /Auf diesem Gerät beitreten/i })
    fireEvent.click(joinBtn)

    expect(mockStore.transferCallToThisDevice).toHaveBeenCalledTimes(1)
  })

  it('führt terminateCrossDeviceCall aus wenn man auf Auflegen klickt', () => {
    mockStore.crossDeviceCall = MOCK_CALL
    render(<CrossDeviceCallBanner />)

    const hangupBtn = screen.getByRole('button', { name: /Auflegen/i })
    fireEvent.click(hangupBtn)

    expect(mockStore.terminateCrossDeviceCall).toHaveBeenCalledTimes(1)
  })

  it('leitet SSE-Ereignisse an handleCrossDeviceEvent weiter', () => {
    render(<CrossDeviceCallBanner />)

    const event = new CustomEvent('msm:sync-event', {
      detail: { type: 'call_transferred', raum: 'raum-1' },
    })
    window.dispatchEvent(event)

    expect(mockStore.handleCrossDeviceEvent).toHaveBeenCalledWith({
      type: 'call_transferred',
      raum: 'raum-1',
    })
  })

  it('rendert Gruppenanruf-Banner wenn activeGroupCalls vorhanden ist', async () => {
    await merkeGruppenName(12, { name: 'Team Alpha' })
    mockStore.activeGroupCalls = [
      {
        group_id: 12,
        room_token: 'grp_alpha_12',
        participant_count: 3,
      },
    ]
    render(<CrossDeviceCallBanner />)

    expect(screen.getByRole('region', { name: i18n.t('calls.activeGroupCall') })).toBeTruthy()
    expect(screen.getByText(i18n.t('calls.ongoingGroupCall'))).toBeTruthy()
    expect(await screen.findByText('Team Alpha')).toBeTruthy()
    expect(screen.getByText('3 aktiv')).toBeTruthy()
  })

  it('führt joinGroupCall aus wenn man im Gruppen-Banner auf Anruf beitreten klickt', async () => {
    await merkeGruppenName(12, { name: 'Team Alpha', logo: 'data:image/png;base64,AA' })
    mockStore.activeGroupCalls = [
      {
        group_id: 12,
        room_token: 'grp_alpha_12',
        participant_count: 3,
      },
    ]
    render(<CrossDeviceCallBanner />)
    // Erst wenn der Name steht, traegt der Klick ihn auch weiter.
    await screen.findByText('Team Alpha')

    const joinBtn = screen.getByRole('button', { name: /Anruf beitreten/i })
    fireEvent.click(joinBtn)

    expect(mockStore.joinGroupCall).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 12,
        name: 'Team Alpha',
        avatarUrl: 'data:image/png;base64,AA',
      }),
      'grp_alpha_12',
    )
  })
})
