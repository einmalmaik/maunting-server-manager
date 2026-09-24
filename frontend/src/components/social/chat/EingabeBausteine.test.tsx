/**
 * Die Bausteine rund um die Eingabeleiste.
 *
 * Das Anhangsmenü hält seinen offenen Zustand seit 09/2026 selbst, samt dem
 * Schliessen bei einem Klick daneben; vorher tat das die Seite.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import { AttachMenu } from './AttachMenu'
import { formatFileSize, StagedFileBar } from './ChatComposerBars'
import { MentionSuggestions } from './ChatComposerTop'
import { ComposerSendActions } from './ComposerSendActions'

beforeAll(async () => {
  await i18n.changeLanguage('de')
})

function anhang(erlaubt = true) {
  const aktionen = {
    onKamera: vi.fn(),
    onFoto: vi.fn(),
    onDokument: vi.fn(),
    onNotiz: vi.fn(),
    onTermin: vi.fn(),
  }
  render(
    <div>
      <p>daneben</p>
      <AttachMenu erlaubt={erlaubt} {...aktionen} />
    </div>,
  )
  return aktionen
}

const plus = () => screen.getByLabelText(i18n.t('messenger.addAttachment'))
const dokument = () => screen.queryByLabelText(i18n.t('messenger.attachFile'))

describe('AttachMenu', () => {
  it('ist ohne das Recht gesperrt und nennt den Grund', () => {
    anhang(false)
    const knopf = screen.getByLabelText(i18n.t('messenger.attachNoRight'))
    expect(knopf).toBeDisabled()
  })

  it('schliesst nach einer Wahl und führt sie aus', () => {
    const aktionen = anhang()
    fireEvent.click(plus())
    fireEvent.click(dokument()!)
    expect(aktionen.onDokument).toHaveBeenCalledTimes(1)
    expect(dokument()).not.toBeInTheDocument()
  })

  it('schliesst bei einem Klick daneben, ohne etwas auszuführen', () => {
    const aktionen = anhang()
    fireEvent.click(plus())
    expect(dokument()).toBeInTheDocument()
    fireEvent.mouseDown(screen.getByText('daneben'))
    expect(dokument()).not.toBeInTheDocument()
    expect(Object.values(aktionen).every((f) => f.mock.calls.length === 0)).toBe(true)
  })

  it('bleibt bei einem Klick ins Menü offen', () => {
    anhang()
    fireEvent.click(plus())
    fireEvent.mouseDown(dokument()!)
    expect(dokument()).toBeInTheDocument()
  })
})

describe('ComposerSendActions', () => {
  it('zeigt mit Inhalt nur Senden, gesperrt während des Sendens', () => {
    render(<ComposerSendActions hatInhalt sendet onVideonotiz={() => {}} onSprachnachricht={() => {}} />)
    const senden = screen.getByLabelText('Senden')
    expect(senden).toHaveAttribute('type', 'submit')
    expect(senden).toBeDisabled()
    expect(screen.queryByLabelText(i18n.t('messenger.recordVoice'))).not.toBeInTheDocument()
  })

  it('bietet ohne Inhalt Video- und Sprachnachricht an', () => {
    const onSprachnachricht = vi.fn()
    render(
      <ComposerSendActions hatInhalt={false} sendet={false} onVideonotiz={() => {}} onSprachnachricht={onSprachnachricht} />,
    )
    expect(screen.queryByLabelText('Senden')).not.toBeInTheDocument()
    fireEvent.click(screen.getByLabelText(i18n.t('messenger.recordVoice')))
    expect(onSprachnachricht).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText(i18n.t('messenger.recordVideoNote'))).toBeInTheDocument()
  })
})

describe('MentionSuggestions', () => {
  it('nimmt dem Feld beim Tippen auf einen Vorschlag nicht den Fokus', () => {
    const onWaehlen = vi.fn()
    render(<MentionSuggestions vorschlaege={[{ userId: 3, name: 'carla' }]} onWaehlen={onWaehlen} />)
    const zeile = screen.getByText('@carla').closest('button')!
    // `fireEvent` meldet `false`, wenn `preventDefault` gerufen wurde.
    expect(fireEvent.mouseDown(zeile)).toBe(false)
    fireEvent.click(zeile)
    expect(onWaehlen).toHaveBeenCalledWith({ userId: 3, name: 'carla' })
  })

  it('zeichnet ohne Vorschläge nichts', () => {
    const { container } = render(<MentionSuggestions vorschlaege={[]} onWaehlen={() => {}} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('StagedFileBar', () => {
  it('nennt die Größe lesbar', () => {
    expect(formatFileSize(512)).toBe('512 B')
    expect(formatFileSize(1536)).toBe('1.5 KB')
    expect(formatFileSize(5 * 1024 * 1024)).toBe('5.0 MB')
    render(
      <StagedFileBar datei={{ name: 'plan.pdf', sizeBytes: 2048, mimeType: 'application/pdf' }} onEntfernen={() => {}} />,
    )
    expect(screen.getByText('2.0 KB')).toBeInTheDocument()
  })
})
