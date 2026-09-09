import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { AudioTab } from './AudioTab'

const t = (key: string) => i18n.t(key)

describe('AudioTab', () => {
  const enumerateDevicesMock = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    enumerateDevicesMock.mockResolvedValue([
      { deviceId: 'mic-1', kind: 'audioinput', label: 'USB Mikrofon', groupId: 'group-1', toJSON: () => ({}) },
      { deviceId: 'speaker-1', kind: 'audiooutput', label: 'Kopfhörer', groupId: 'group-2', toJSON: () => ({}) },
    ])

    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: {
        enumerateDevices: enumerateDevicesMock,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      },
    })
  })

  it('rendert Mikrofon & Audio Einstellungen mit Gerätedropdown und Test-Option', async () => {
    render(<AudioTab />)

    expect(screen.getByText(t('profile.audioTitle'))).toBeInTheDocument()
    expect(screen.getByText(t('profile.audioTestStart'))).toBeInTheDocument()
    expect(screen.getByText(t('profile.audioNoiseSuppression'))).toBeInTheDocument()

    await waitFor(() => {
      expect(enumerateDevicesMock).toHaveBeenCalled()
    })
  })

  it('rendert Schalter für Signalverarbeitung (Rauschen, Echo, Pegelanpassung)', async () => {
    render(<AudioTab />)

    expect(screen.getByLabelText(t('profile.audioNoiseSuppression'))).toBeInTheDocument()
    expect(screen.getByLabelText(t('profile.audioEchoCancellation'))).toBeInTheDocument()
    expect(screen.getByLabelText(t('profile.audioAutoGain'))).toBeInTheDocument()

    await waitFor(() => {
      expect(enumerateDevicesMock).toHaveBeenCalled()
    })
  })
})
