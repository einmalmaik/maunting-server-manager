/**
 * Der Hinweis zum Neueinsprechen kommt einmal je altem Wort, nicht bei jedem
 * Start. Seit dem festen Namen Singra trifft er fast jeden, der das Wake-Word
 * je auf den alten Standard „Assistent“ kalibriert hat.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const wakewordStandMock = vi.fn()

vi.mock('./tauri', () => ({
  wakewordStand: () => wakewordStandMock(),
}))

import i18n from '@/i18n'
import { KALIBRIERUNG_GEFRAGT_KEY, KalibrierungsHinweis } from './KalibrierungsHinweis'

function stand(wort: string) {
  wakewordStandMock.mockResolvedValue({ aufnahmen: 6, trainiert: true, lauscht: false, wort })
}

function zeigen() {
  return render(
    <MemoryRouter>
      <KalibrierungsHinweis />
    </MemoryRouter>,
  )
}

describe('KalibrierungsHinweis', () => {
  beforeEach(() => {
    localStorage.clear()
    wakewordStandMock.mockReset()
  })

  it('fragt einmal; nach „Später“ bleibt er beim nächsten Start still', async () => {
    stand('Assistent')
    const erster = zeigen()

    fireEvent.click(await screen.findByRole('button', { name: i18n.t('mss.kalibrierung.spaeter') }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(localStorage.getItem(KALIBRIERUNG_GEFRAGT_KEY)).toBe('Assistent')
    erster.unmount()

    zeigen()
    await waitFor(() => expect(wakewordStandMock).toHaveBeenCalledTimes(2))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('„Neu kalibrieren“ zählt ebenfalls als Antwort', async () => {
    stand('Assistent')
    zeigen()

    fireEvent.click(await screen.findByRole('button', { name: i18n.t('mss.kalibrierung.jetzt') }))
    expect(localStorage.getItem(KALIBRIERUNG_GEFRAGT_KEY)).toBe('Assistent')
  })

  it('fragt erneut, wenn das Modell auf ein anderes falsches Wort kalibriert ist', async () => {
    localStorage.setItem(KALIBRIERUNG_GEFRAGT_KEY, 'Assistent')
    stand('Jarvis')
    zeigen()

    expect(await screen.findByRole('dialog')).toBeInTheDocument()
  })

  it('schweigt, wenn auf Singra kalibriert ist', async () => {
    stand('Singra')
    zeigen()

    await waitFor(() => expect(wakewordStandMock).toHaveBeenCalled())
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
