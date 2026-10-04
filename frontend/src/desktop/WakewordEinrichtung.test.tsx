/**
 * Das Wake-Word ist der Name der KI: Singra.
 *
 * Bis 05.10.2026 kam es aus dem frei wählbaren Rufnamen des Kontos. Wer damals
 * auf einen eigenen Namen (oder den Standard „Assistent“) kalibriert hat, soll
 * den Hinweis zum Neueinsprechen sehen — trainiert wird aber nur noch auf
 * Singra, egal was das Konto früher trug.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const wakewordTrainierenMock = vi.fn().mockResolvedValue(undefined)
const wakewordStandMock = vi.fn()

vi.mock('./tauri', () => ({
  konfigLaden: vi.fn().mockResolvedValue({ wakeword_aktiv: false, wakeword_schwelle: 0.45 }),
  konfigSpeichern: vi.fn().mockResolvedValue(undefined),
  wakewordAufnehmen: vi.fn().mockResolvedValue(undefined),
  wakewordLauschen: vi.fn().mockResolvedValue(undefined),
  wakewordStand: () => wakewordStandMock(),
  wakewordTrainieren: (wort: string) => wakewordTrainierenMock(wort),
  wakewordZuruecksetzen: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('@tauri-apps/api/event', () => ({ listen: vi.fn().mockResolvedValue(() => {}) }))
vi.mock('@/lib/errungenschaft', () => ({ meldeErrungenschaft: vi.fn() }))

import i18n from '@/i18n'
import { WakewordEinrichtung } from './WakewordEinrichtung'

describe('WakewordEinrichtung', () => {
  beforeEach(() => {
    wakewordTrainierenMock.mockClear()
    wakewordStandMock.mockResolvedValue({
      aufnahmen: 6,
      trainiert: true,
      lauscht: false,
      wort: 'Assistent',
    })
  })

  it('nennt Singra als Wake-Word und bietet das Neueinsprechen an', async () => {
    render(<WakewordEinrichtung />)

    expect(await screen.findByText('Singra')).toBeInTheDocument()
    expect(
      await screen.findByText(i18n.t('mss.wakeword.neuKalibrieren', { alt: 'Assistent', neu: 'Singra' })),
    ).toBeInTheDocument()
  })

  it('trainiert immer auf Singra', async () => {
    render(<WakewordEinrichtung />)

    const knopf = await screen.findByRole('button', { name: i18n.t('mss.wakeword.trainieren') })
    await waitFor(() => expect(knopf).toBeEnabled())
    fireEvent.click(knopf)

    await waitFor(() => expect(wakewordTrainierenMock).toHaveBeenCalledWith('Singra'))
  })
})
