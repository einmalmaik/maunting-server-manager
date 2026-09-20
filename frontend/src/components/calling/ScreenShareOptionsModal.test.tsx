import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'

// Die Sprache festlegen: die Behauptungen unten prüfen deutsche Texte, und
// ohne diese Zeile entscheidet navigator.language der Testumgebung.
beforeAll(async () => {
  await i18n.changeLanguage('de')
})

const systemtonMoeglich = vi.fn(() => true)

vi.mock('@/services/livekitRaum', () => ({
  FREIGABE_STANDARD: { aufloesung: '1080p', bildrate: 60, systemton: true },
  systemtonMoeglich: () => systemtonMoeglich(),
}))

const { ScreenShareOptionsModal } = await import('./ScreenShareOptionsModal')

function oeffne(overrides: Partial<Parameters<typeof ScreenShareOptionsModal>[0]> = {}) {
  const onStart = vi.fn()
  const onClose = vi.fn()
  render(
    <ScreenShareOptionsModal isOpen onClose={onClose} onStart={onStart} {...overrides} />,
  )
  return { onStart, onClose }
}

/** Die Dropdowns rendern ihr Menü in ein Portal; Auswahl läuft über die Optionsrolle. */
function waehle(feld: string, option: string) {
  fireEvent.click(screen.getByRole('button', { name: new RegExp(feld, 'i') }))
  fireEvent.click(screen.getByRole('option', { name: new RegExp(option, 'i') }))
}

afterEach(() => {
  systemtonMoeglich.mockReturnValue(true)
})

describe('ScreenShareOptionsModal', () => {
  it('startet mit der Voreinstellung 1080p/60 und Systemton', () => {
    const { onStart } = oeffne()

    fireEvent.click(screen.getByRole('button', { name: i18n.t('calls.chooseSource') }))

    expect(onStart).toHaveBeenCalledWith({
      aufloesung: '1080p',
      bildrate: 60,
      systemton: true,
    })
  })

  it('gibt 2K mit 60 Bildern und Systemton so weiter, wie eingestellt', () => {
    const { onStart, onClose } = oeffne()

    waehle(i18n.t('calls.resolution'), '1440p')
    waehle(i18n.t('calls.frameRate'), '60 FPS')
    fireEvent.click(screen.getByRole('button', { name: i18n.t('calls.chooseSource') }))

    expect(onStart).toHaveBeenCalledWith({
      aufloesung: '1440p',
      bildrate: 60,
      systemton: true,
    })
    // Erst danach fragt der Browser nach der Quelle, deshalb schliesst der Dialog.
    expect(onClose).toHaveBeenCalled()
  })

  it('reicht 30 Bilder als Zahl weiter, nicht als Text', () => {
    const { onStart } = oeffne()

    waehle(i18n.t('calls.frameRate'), '30 FPS')
    fireEvent.click(screen.getByRole('button', { name: i18n.t('calls.chooseSource') }))

    expect(onStart.mock.calls[0][0].bildrate).toBe(30)
  })

  it('lässt den Systemton abwählen', () => {
    const { onStart } = oeffne()

    fireEvent.click(screen.getByRole('checkbox', { name: /Systemton/i }))
    fireEvent.click(screen.getByRole('button', { name: i18n.t('calls.chooseSource') }))

    expect(onStart.mock.calls[0][0].systemton).toBe(false)
  })

  it('warnt bei 2K mit 60 Bildern vor der Leitung', () => {
    oeffne()
    expect(screen.queryByText(/gute Leitung/i)).not.toBeInTheDocument()

    waehle(i18n.t('calls.resolution'), '1440p')
    expect(screen.getByText(/gute Leitung/i)).toBeInTheDocument()
  })

  it('sagt ehrlich, wenn der Browser keinen Systemton kann', () => {
    systemtonMoeglich.mockReturnValue(false)
    const { onStart } = oeffne()

    const schalter = screen.getByRole('checkbox', { name: /Systemton/i })
    expect(schalter).toBeDisabled()
    expect(screen.getByText(/kann keinen Systemton/i)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: i18n.t('calls.chooseSource') }))
    // Nichts versprechen, was nicht ankommt.
    expect(onStart.mock.calls[0][0].systemton).toBe(false)
  })

  it('startet nichts, wenn abgebrochen wird', () => {
    const { onStart, onClose } = oeffne()

    fireEvent.click(screen.getByRole('button', { name: i18n.t('common.cancel') }))

    expect(onStart).not.toHaveBeenCalled()
    expect(onClose).toHaveBeenCalled()
  })

  it('übernimmt eine zuvor getroffene Wahl als Ausgangspunkt', () => {
    const { onStart } = oeffne({
      initial: { aufloesung: '720p', bildrate: 30, systemton: false },
    })

    fireEvent.click(screen.getByRole('button', { name: i18n.t('calls.chooseSource') }))

    expect(onStart).toHaveBeenCalledWith({
      aufloesung: '720p',
      bildrate: 30,
      systemton: false,
    })
  })
})
