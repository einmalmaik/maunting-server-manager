import { fireEvent, render, screen } from '@testing-library/react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AiVoiceConfig } from '@/api/ai'
import i18n from '@/i18n'
import { SprachAnsicht } from './SprachAnsicht'
import type { Beleg, Sprachzeile, Sprachzustand } from './useSprachsitzung'

const starten = vi.fn()
const beenden = vi.fn()

let sitzung: {
  zustand: Sprachzustand
  zeilen: Sprachzeile[]
  werkzeug: string | null
  fehler: string | null
  belege: Beleg[]
}

vi.mock('./useSprachsitzung', () => ({
  useSprachsitzung: () => ({ ...sitzung, pegel: () => 0, starten, beenden }),
}))

// Die Kugel zeichnet auf ein Canvas, und jsdom hat keinen 2D-Kontext. Sie
// bringt sich selbst nicht um (der Kontext wird geprueft), aber hier geht es um
// die Bedienung — nicht um Farbverlaeufe.
vi.mock('./Sprachblase', () => ({ Sprachblase: () => null }))

const KONFIGURATION: AiVoiceConfig = {
  available: true,
  model: 'gpt-realtime-2.1',
  // Kommt vom Server bereits aufgeloest: hat der Zugang keine Stimme
  // hinterlegt, steht hier die Standardstimme. Die Ansicht bekommt nie `null`
  // und muss deshalb auch nie eine einsetzen.
  voice: 'alloy',
  sample_rate: 24_000,
  max_seconds: 900,
}

function ansicht(
  teil: Partial<typeof sitzung> = {},
  aufChat = vi.fn(),
  konfiguration: AiVoiceConfig = KONFIGURATION,
) {
  sitzung = { zustand: 'bereit', zeilen: [], werkzeug: null, fehler: null, belege: [], ...teil }
  const ergebnis = render(
    <SprachAnsicht konfiguration={konfiguration} aufChat={aufChat} />,
  )
  return { ...ergebnis, aufChat }
}

describe('SprachAnsicht', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('de')
  })

  beforeEach(() => {
    starten.mockClear()
    beenden.mockClear()
  })

  it('hat fuer jeden Zustand Ueberschrift und Erklaerung', () => {
    const zustaende: Sprachzustand[] = ['aus', 'verbindet', 'bereit', 'hoert', 'denkt', 'spricht']

    // Ein fehlender Schluessel kommt bei i18next als der Schluessel selbst
    // zurueck. Ohne diese Zusicherung stuende im Panel woertlich
    // `ai.voice.hint.hoert` — gross und mittig.
    for (const zustand of zustaende) {
      expect(i18n.t(`ai.voice.zustand.${zustand}`)).not.toBe(`ai.voice.zustand.${zustand}`)
      expect(i18n.t(`ai.voice.hint.${zustand}`)).not.toBe(`ai.voice.hint.${zustand}`)
    }
    for (const s of ['ai.voice.end', 'ai.voice.endHint', 'ai.voice.toVoiceMode', 'ai.voice.toTextMode']) {
      expect(i18n.t(s)).not.toBe(s)
    }
  })

  it('faengt von selbst an zu hoeren', () => {
    ansicht()

    // Wer in den Sprachmodus wechselt, will sprechen. Ein zweiter Klick auf
    // „jetzt aber wirklich" waere eine Tuer hinter der Tuer.
    expect(starten).toHaveBeenCalledOnce()
  })

  it('legt auf, bevor sie zurueck in den Chat geht', () => {
    const { aufChat } = ansicht({ zustand: 'hoert' })

    fireEvent.click(screen.getByText(i18n.t('ai.voice.end')))

    // Die Reihenfolge ist die Zusage: andersherum bliebe fuer einen
    // Wimpernschlag ein offenes Mikrofon hinter einer Ansicht stehen, die es
    // nicht mehr anzeigt.
    expect(beenden).toHaveBeenCalledOnce()
    expect(aufChat).toHaveBeenCalledOnce()
    expect(beenden.mock.invocationCallOrder[0])
      .toBeLessThan(aufChat.mock.invocationCallOrder[0])
  })

  it('beendet das Gespraech mit ESC', () => {
    const { aufChat } = ansicht({ zustand: 'spricht' })

    fireEvent.keyDown(window, { key: 'Escape' })

    expect(beenden).toHaveBeenCalledOnce()
    expect(aufChat).toHaveBeenCalledOnce()
  })

  it('nennt die Tastenkombination am Knopf', () => {
    ansicht()

    // Eine Tastenkombination, die man nicht sieht, ist keine.
    expect(screen.getByText(i18n.t('ai.voice.endHint'))).toBeInTheDocument()
  })

  it('zeigt den Fehler statt der Zustandsueberschrift', () => {
    ansicht({ zustand: 'aus', fehler: 'ai.voice.errors.audio' })

    expect(screen.getByText(i18n.t('ai.voice.errors.audio'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('ai.voice.zustand.aus'))).not.toBeInTheDocument()
  })

  it('zeigt nur den Werkzeugnamen', () => {
    ansicht({ zustand: 'denkt', werkzeug: 'read_server_status' })

    // Argumente tragen Serverkennungen und Pfade.
    expect(screen.getByText('read_server_status')).toBeInTheDocument()
  })

  it('haelt den Wortwechsel vorlesbar', () => {
    ansicht({
      zustand: 'spricht',
      zeilen: [
        { wer: 'ich', text: 'welche server laufen?' },
        { wer: 'ki', text: 'Zwei laufen.' },
      ],
    })

    // Wer nicht hoert — weil der Ton aus ist oder weil er nicht hoeren kann —
    // bekommt die Antwort nur hierueber.
    const bereich = screen.getByText(/Zwei laufen\./).closest('[aria-live]')
    expect(bereich).toHaveAttribute('aria-live', 'polite')
  })

  it('zeigt die Belegstelle als reinen Text und sagt, woher sie stammt', () => {
    ansicht({
      zustand: 'spricht',
      belege: [
        {
          quelle: 'latest.log',
          zeilen: [
            '[12:03:44] [Server thread/ERROR]: **kein fetter Text**',
            'siehe [kein Link](https://boese.example/) — Zeile 88',
          ],
        },
      ],
    })

    expect(screen.getByText(i18n.t('ai.voice.beleg.heading'))).toBeInTheDocument()
    expect(screen.getByText('latest.log')).toBeInTheDocument()
    // Ohne diesen Hinweis kann niemand unterscheiden, was die KI *sagt* und was
    // sie nur *zeigt* — und genau darauf beruht der ganze Kasten.
    expect(screen.getByText(i18n.t('ai.voice.beleg.untrusted'))).toBeInTheDocument()

    // Reiner Text heisst reiner Text: Sternchen und Klammern stehen so da, wie
    // sie im Log stehen. Ein Markdown-Renderer waere hier der kuerzeste Weg von
    // einer Logzeile zu einem klickbaren Link im Panel — geschrieben hat die
    // Zeile irgendwer auf irgendeinem Server, nicht die KI.
    expect(screen.getByText(/\*\*kein fetter Text\*\*/)).toBeInTheDocument()
    expect(screen.getByText(/\[kein Link\]\(https:\/\/boese\.example\/\)/)).toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('zeigt nur die zuletzt gezeigte Stelle', () => {
    ansicht({
      zustand: 'spricht',
      belege: [
        { quelle: 'server.properties', zeilen: ['online-mode=true'] },
        { quelle: 'latest.log', zeilen: ['[Server thread/ERROR]: Adresse belegt'] },
      ],
    })

    // Uebereinander waere es ein Protokoll. Die KI spricht ueber *eine* Stelle,
    // und die muss dastehen — sonst liest der Mensch beim Zuhoeren die falsche.
    expect(screen.getByText(/Adresse belegt/)).toBeInTheDocument()
    expect(screen.queryByText(/online-mode=true/)).not.toBeInTheDocument()
    expect(screen.queryByText('server.properties')).not.toBeInTheDocument()
  })

  it('zeigt hinter dem Zahnrad Angaben und keine erfundenen Regler', () => {
    ansicht()

    fireEvent.click(screen.getByRole('button', { name: i18n.t('ai.voice.settings') }))

    expect(screen.getByText('gpt-realtime-2.1')).toBeInTheDocument()
    expect(screen.getByText('24 kHz')).toBeInTheDocument()
    expect(screen.getByText(i18n.t('ai.voice.info.minutes', { count: 15 }))).toBeInTheDocument()
    // Kein Schalter, der nichts tut: es gibt am Sprachmodus nichts zu stellen,
    // und das steht auch so da.
    expect(screen.queryByRole('switch')).not.toBeInTheDocument()
  })

  it('nennt hinter dem Zahnrad die Stimme, mit der gerade gesprochen wird', () => {
    ansicht({}, vi.fn(), { ...KONFIGURATION, voice: 'shimmer' })

    fireEvent.click(screen.getByRole('button', { name: i18n.t('ai.voice.settings') }))

    // Was hier steht, kommt vom Server. Ein fest eingetragenes „alloy" waere
    // eine zweite Wahrheit — und sie loege in dem Augenblick, in dem der
    // Betreiber am Zugang eine andere Stimme hinterlegt.
    expect(screen.getByText(i18n.t('ai.voice.info.voice'))).toBeInTheDocument()
    expect(screen.getByText('shimmer')).toBeInTheDocument()
  })

  it('schliesst mit ESC erst die Angaben und dann das Gespraech', () => {
    const { aufChat } = ansicht()
    fireEvent.click(screen.getByRole('button', { name: i18n.t('ai.voice.settings') }))

    fireEvent.keyDown(window, { key: 'Escape' })

    // Die erste Flucht gilt dem, was zuletzt aufging. Sonst riesse ein ESC,
    // das nur das Panel schliessen sollte, das ganze Gespraech ab.
    expect(screen.queryByText('gpt-realtime-2.1')).not.toBeInTheDocument()
    expect(aufChat).not.toHaveBeenCalled()

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(aufChat).toHaveBeenCalledOnce()
  })
})
