/**
 * Zwei Ebenen, die nicht verwechselt werden dürfen: was nur ich höre, und was
 * für alle gilt. Die lokale Ebene braucht kein Recht, die andere immer eines —
 * und der Server prüft es beim Ausführen noch einmal.
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import type { CallParticipant } from '@/stores/useCallStore'

// Die Sprache festlegen: die Behauptungen unten prüfen deutsche Texte, und
// ohne diese Zeile entscheidet navigator.language der Testumgebung.
beforeAll(async () => {
  await i18n.changeLanguage('de')
})

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
    fireEvent.change(screen.getByLabelText(i18n.t('calls.volumeForMe')), { target: { value: '50' } })
    expect(onVolumeChange).toHaveBeenCalledWith('u2', 0.5)
  })

  it('schaltet für mich stumm und wieder laut', () => {
    const { onVolumeChange } = zeige({ participant: teilnehmer({ volume: 0.8 }) })
    fireEvent.click(screen.getByText(i18n.t('calls.muteForMe')))
    expect(onVolumeChange).toHaveBeenCalledWith('u2', 0)
  })

  it('holt beim Aufheben die vorherige Lautstärke zurück, nicht stumpf 100 %', () => {
    const { onVolumeChange } = zeige({ participant: teilnehmer({ volume: 0 }) })
    fireEvent.click(screen.getByText(i18n.t('calls.unmuteForMe')))
    expect(onVolumeChange).toHaveBeenCalledWith('u2', 1)
  })

  it('braucht dafür kein Recht', () => {
    zeige({ darfStummschalten: false, darfEntfernen: false })
    expect(screen.getByLabelText(i18n.t('calls.volumeForMe'))).toBeTruthy()
  })
})

describe('Moderation', () => {
  it('zeigt ohne Recht keinen Moderationsknopf', () => {
    zeige()
    expect(screen.queryByText(i18n.t('calls.muteParticipant'))).toBeNull()
    expect(screen.queryByText(i18n.t('calls.removeFromCall'))).toBeNull()
  })

  it('zeigt nur, wofür das Recht da ist', () => {
    zeige({ darfStummschalten: true, darfEntfernen: false })
    expect(screen.getByText(i18n.t('calls.muteParticipant'))).toBeTruthy()
    expect(screen.queryByText(i18n.t('calls.removeFromCall'))).toBeNull()
  })

  it('schaltet serverseitig stumm', async () => {
    zeige({ darfStummschalten: true })
    fireEvent.click(screen.getByText(i18n.t('calls.muteParticipant')))
    await waitFor(() => expect(api.setzeServerStumm).toHaveBeenCalledWith('raum-1', 2, true))
  })

  it('gibt ein abgeschaltetes Mikrofon wieder frei', async () => {
    zeige({ participant: teilnehmer({ isMuted: true }), darfStummschalten: true })
    fireEvent.click(screen.getByText(i18n.t('calls.unmuteParticipant')))
    await waitFor(() => expect(api.setzeServerStumm).toHaveBeenCalledWith('raum-1', 2, false))
  })

  it('entfernt aus dem Anruf', async () => {
    zeige({ darfEntfernen: true })
    fireEvent.click(screen.getByText(i18n.t('calls.removeFromCall')))
    await waitFor(() => expect(api.entferneAusAnruf).toHaveBeenCalledWith('raum-1', 2))
  })

  it('bietet im Zweiergespräch keine Moderation an', () => {
    // Ohne Gruppenraum gibt es niemanden, der ein Recht vergeben hätte.
    zeige({ raum: null, darfStummschalten: true, darfEntfernen: true })
    expect(screen.queryByText(i18n.t('calls.muteParticipant'))).toBeNull()
  })

  it('bietet nichts gegen einen selbst an', () => {
    zeige({ participant: teilnehmer({ isSelf: true }), darfStummschalten: true })
    expect(screen.queryByText(i18n.t('calls.muteParticipant'))).toBeNull()
    expect(screen.queryByLabelText(i18n.t('calls.volumeForMe'))).toBeNull()
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
