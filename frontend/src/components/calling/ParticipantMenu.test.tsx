/**
 * Zwei Ebenen, die nicht verwechselt werden dürfen: was nur ich höre, und was
 * für alle gilt. Die lokale Ebene braucht kein Recht, die andere immer eines —
 * und der Server prüft es beim Ausführen noch einmal.
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CallParticipant } from '@/stores/useCallStore'

vi.mock('@/api/calls', () => ({
  setzeServerStumm: vi.fn().mockResolvedValue(undefined),
  entferneAusAnruf: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('@/stores/toastStore', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}))

// Nach den Mocks importieren, sonst greifen sie nicht.
const api = await import('@/api/calls')
const { ParticipantMenu } = await import('./ParticipantMenu')

function teilnehmer(ueberschreibung: Partial<CallParticipant> = {}): CallParticipant {
  return {
    userId: 2,
    identity: 'u2',
    username: 'bob',
    avatarUrl: null,
    isSelf: false,
    isMuted: false,
    isCameraOff: true,
    isSpeaking: false,
    isPending: false,
    videoTrack: null,
    volume: 1,
    ...ueberschreibung,
  }
}

function zeige(props: Partial<Parameters<typeof ParticipantMenu>[0]> = {}) {
  const onVolumeChange = vi.fn()
  const onClose = vi.fn()
  render(
    <ParticipantMenu
      participant={teilnehmer()}
      onClose={onClose}
      onVolumeChange={onVolumeChange}
      raum="raum-1"
      darfStummschalten={false}
      darfEntfernen={false}
      {...props}
    />,
  )
  return { onVolumeChange, onClose }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Lokale Ebene', () => {
  it('stellt die Lautstärke nur für mich', () => {
    const { onVolumeChange } = zeige()
    fireEvent.change(screen.getByLabelText('Lautstärke für mich'), { target: { value: '50' } })
    expect(onVolumeChange).toHaveBeenCalledWith('u2', 0.5)
  })

  it('schaltet für mich stumm und wieder laut', () => {
    const { onVolumeChange } = zeige({ participant: teilnehmer({ volume: 0.8 }) })
    fireEvent.click(screen.getByText('Für mich stumm schalten'))
    expect(onVolumeChange).toHaveBeenCalledWith('u2', 0)
  })

  it('holt beim Aufheben die vorherige Lautstärke zurück, nicht stumpf 100 %', () => {
    const { onVolumeChange } = zeige({ participant: teilnehmer({ volume: 0 }) })
    fireEvent.click(screen.getByText('Wieder hörbar machen'))
    expect(onVolumeChange).toHaveBeenCalledWith('u2', 1)
  })

  it('braucht dafür kein Recht', () => {
    zeige({ darfStummschalten: false, darfEntfernen: false })
    expect(screen.getByLabelText('Lautstärke für mich')).toBeTruthy()
  })
})

describe('Moderation', () => {
  it('zeigt ohne Recht keinen Moderationsknopf', () => {
    zeige()
    expect(screen.queryByText('Mikrofon abschalten')).toBeNull()
    expect(screen.queryByText('Aus dem Anruf entfernen')).toBeNull()
  })

  it('zeigt nur, wofür das Recht da ist', () => {
    zeige({ darfStummschalten: true, darfEntfernen: false })
    expect(screen.getByText('Mikrofon abschalten')).toBeTruthy()
    expect(screen.queryByText('Aus dem Anruf entfernen')).toBeNull()
  })

  it('schaltet serverseitig stumm', async () => {
    zeige({ darfStummschalten: true })
    fireEvent.click(screen.getByText('Mikrofon abschalten'))
    await waitFor(() => expect(api.setzeServerStumm).toHaveBeenCalledWith('raum-1', 2, true))
  })

  it('gibt ein abgeschaltetes Mikrofon wieder frei', async () => {
    zeige({ participant: teilnehmer({ isMuted: true }), darfStummschalten: true })
    fireEvent.click(screen.getByText('Wieder sprechen lassen'))
    await waitFor(() => expect(api.setzeServerStumm).toHaveBeenCalledWith('raum-1', 2, false))
  })

  it('entfernt aus dem Anruf', async () => {
    zeige({ darfEntfernen: true })
    fireEvent.click(screen.getByText('Aus dem Anruf entfernen'))
    await waitFor(() => expect(api.entferneAusAnruf).toHaveBeenCalledWith('raum-1', 2))
  })

  it('bietet im Zweiergespräch keine Moderation an', () => {
    // Ohne Gruppenraum gibt es niemanden, der ein Recht vergeben hätte.
    zeige({ raum: null, darfStummschalten: true, darfEntfernen: true })
    expect(screen.queryByText('Mikrofon abschalten')).toBeNull()
  })

  it('bietet nichts gegen einen selbst an', () => {
    zeige({ participant: teilnehmer({ isSelf: true }), darfStummschalten: true })
    expect(screen.queryByText('Mikrofon abschalten')).toBeNull()
    expect(screen.queryByLabelText('Lautstärke für mich')).toBeNull()
  })
})

describe('Nichts zu zeigen', () => {
  it('rendert ohne Teilnehmer gar nichts', () => {
    const { container } = render(
      <ParticipantMenu
        participant={null}
        onClose={vi.fn()}
        onVolumeChange={vi.fn()}
        raum="raum-1"
        darfStummschalten
        darfEntfernen
      />,
    )
    expect(container.textContent).toBe('')
  })
})
