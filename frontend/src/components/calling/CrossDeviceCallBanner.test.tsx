import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ActiveCallInfo } from '@/api/calls'

const mockStore = {
  crossDeviceCall: null as ActiveCallInfo | null,
  state: 'idle',
  checkActiveCall: vi.fn(),
  transferCallToThisDevice: vi.fn().mockResolvedValue(undefined),
  terminateCrossDeviceCall: vi.fn().mockResolvedValue(undefined),
  handleCrossDeviceEvent: vi.fn(),
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
    group_name: null,
    partner: {
      user_id: 2,
      username: 'Alice',
      avatar_url: null,
    },
    started_at: '2026-09-17T12:00:00Z',
  }

  beforeEach(() => {
    vi.clearAllMocks()
    mockStore.crossDeviceCall = null
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

    expect(screen.getByRole('region', { name: 'Aktiver Anruf auf anderem Gerät' })).toBeTruthy()
    expect(screen.getByText('Du bist bereits in einem Anruf')).toBeTruthy()
    expect(screen.getByText('Alice')).toBeTruthy()
    expect(screen.getByText('Smartphone / APK')).toBeTruthy()
    expect(screen.getByText(/Audio-Anruf/i)).toBeTruthy()
  })

  it('rendert Gruppen-Informationen wenn es sich um einen Gruppenanruf handelt', () => {
    mockStore.crossDeviceCall = {
      ...MOCK_CALL,
      art: 'gruppe',
      group_id: 5,
      group_name: 'Projektteam',
      partner: null,
      device_type: 'desktop',
    }
    mockStore.state = 'idle'
    render(<CrossDeviceCallBanner />)

    expect(screen.getByText('Projektteam')).toBeTruthy()
    expect(screen.getByText('Desktop-App')).toBeTruthy()
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
})
