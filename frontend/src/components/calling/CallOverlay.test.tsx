/**
 * Wo das Anruffenster im Baum hängt — und warum das kein Detail ist.
 *
 * Der Inhaltsbereich der App-Hülle liegt in zwei Stapelkontexten (`relative
 * z-10` in `Shell.tsx`). Ein `z-50` darin zählt nach außen nur als 10 und
 * verliert gegen die Navigation (`z-40`). Genau so lag die Navigation über den
 * linken 256 Pixeln des Anruffensters, und der Rest sah aus, als liefe er nach
 * rechts hinaus.
 *
 * Deshalb geht das Fenster über einen Portal an `document.body`. Ein höherer
 * z-Wert hätte nichts geändert — er wäre im selben Käfig geblieben. Weder
 * `tsc` noch ein Schnappschusstest sehen den Unterschied, dieser Test schon.
 */

import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'

// Die Sprache festlegen: die Behauptungen unten prüfen deutsche Texte, und
// ohne diese Zeile entscheidet navigator.language der Testumgebung.
beforeAll(async () => {
  await i18n.changeLanguage('de')
})

// Die ganze Kapsel, nicht `livekit-client` selbst — dort endet der Code, den
// dieses Projekt schreibt. Der echte Modulpfad zieht den E2EE-Worker mit, den
// jsdom nicht starten kann. `RoomEvent` und `Track` kommen trotzdem echt aus
// dem SDK, damit die Ereignisnamen nicht auseinanderlaufen.
vi.mock('@/services/livekitRaum', async () => {
  const echt = await vi.importActual<typeof import('livekit-client')>('livekit-client')
  return {
    ConnectionState: echt.ConnectionState,
    RoomEvent: echt.RoomEvent,
    Track: echt.Track,
    FREIGABE_STANDARD: { aufloesung: '1080p', bildrate: 60, systemton: true },
    E2eeNichtUnterstuetzt: class extends Error {},
    e2eeMoeglich: () => true,
    bildschirmfreigabeMoeglich: () => true,
    systemtonMoeglich: () => true,
    benutzerIdAusIdentity: (id: string) => (/^u\d+$/.test(id) ? Number(id.slice(1)) : null),
    freigabeAufnahmeOptionen: () => ({}),
    freigabeSendeOptionen: () => ({}),
    setzeLautsprecher: vi.fn().mockResolvedValue(undefined),
    setzeMikrofon: vi.fn().mockResolvedValue(undefined),
    setzeKamera: vi.fn().mockResolvedValue(undefined),
    setzeTaub: vi.fn(),
    setzeLautstaerke: vi.fn(),
    wechsleGeraet: vi.fn().mockResolvedValue(undefined),
    starteBildschirmfreigabe: vi.fn().mockResolvedValue(undefined),
    beendeBildschirmfreigabe: vi.fn().mockResolvedValue(undefined),
    trenne: vi.fn().mockResolvedValue(undefined),
    erlaubeWiedergabe: vi.fn().mockResolvedValue(true),
    setzeRaumSchluessel: vi.fn().mockResolvedValue(undefined),
    verbinde: vi.fn(),
  }
})
vi.mock('@/api/calls', () => ({
  ladeZuAnrufEin: vi.fn(),
  holeInAnruf: vi.fn(),
  lehneAnrufAb: vi.fn(),
  brichAnrufAb: vi.fn(),
  holeZugang: vi.fn(),
  beendeGruppenanruf: vi.fn(),
  setzeServerStumm: vi.fn(),
  entferneAusAnruf: vi.fn(),
}))
vi.mock('@/services/raumSchluessel', () => ({
  erzeugeRaumSchluessel: () => new Uint8Array(32),
  alsArrayBuffer: (b: Uint8Array) => b.buffer,
  istSchluesselhalter: () => false,
  verteileAn: vi.fn(),
  verteileAnAlle: vi.fn(),
  entpacke: vi.fn(),
}))
vi.mock('@/lib/benachrichtigung', () => ({
  sendeGeraeteBenachrichtigung: vi.fn().mockResolvedValue(undefined),
}))
vi.mock('@/stores/toastStore', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}))
vi.mock('@/components/calling/anrufToene', () => ({
  toneBeitritt: vi.fn(),
  toneAbgang: vi.fn(),
  toneAufgelegt: vi.fn(),
}))

// Nach den Mocks importieren, sonst greifen sie nicht.
const { CallOverlay } = await import('./CallOverlay')
const { useCallStore } = await import('@/stores/useCallStore')

const FRISCH = useCallStore.getState()

/** Der Käfig aus `Shell.tsx`: zwei Stapelkontexte über der Seite. */
function imInhaltsbereich(kind: React.ReactElement) {
  const kaefig = document.createElement('div')
  kaefig.className = 'relative z-10'
  const innen = document.createElement('div')
  innen.className = 'relative z-10'
  kaefig.appendChild(innen)
  document.body.appendChild(kaefig)
  return { ...render(kind, { container: innen }), kaefig, innen }
}

beforeEach(() => {
  useCallStore.setState(FRISCH, true)
})

afterEach(() => {
  cleanup()
  useCallStore.setState(FRISCH, true)
  document.querySelectorAll('body > div.relative').forEach((el) => el.remove())
})

describe('CallOverlay: Platz im Baum', () => {
  it('rendert nichts, solange kein Anruf läuft', () => {
    const { innen } = imInhaltsbereich(<CallOverlay />)
    expect(innen.innerHTML).toBe('')
  })

  it('hängt das Anruffenster an document.body, nicht in den Inhaltsbereich', () => {
    const { innen } = imInhaltsbereich(<CallOverlay />)
    act(() => {
      useCallStore.setState({
        state: 'outgoing',
        kind: 'direkt',
        partner: { userId: 2, username: 'Maik', avatarUrl: null },
        raum: 'r1',
      })
    })

    const fenster = document.querySelector('div.fixed.inset-0.z-50')
    expect(fenster).not.toBeNull()
    // Der Kern: nicht im Käfig. Sonst läge die Navigation darüber.
    expect(innen.contains(fenster)).toBe(false)
    expect(fenster!.parentElement).toBe(document.body)
  })

  it('räumt das Fenster wieder ab, wenn der Anruf endet', () => {
    imInhaltsbereich(<CallOverlay />)
    act(() => {
      useCallStore.setState({
        state: 'active',
        kind: 'direkt',
        partner: { userId: 2, username: 'Maik', avatarUrl: null },
        raum: 'r1',
      })
    })
    expect(document.querySelector('div.fixed.inset-0.z-50')).not.toBeNull()

    act(() => {
      useCallStore.setState({ state: 'idle' })
    })
    expect(document.querySelector('div.fixed.inset-0.z-50')).toBeNull()
  })

  it('zeigt den Auflegen-Knopf beschriftet, nicht nur als Symbol', () => {
    imInhaltsbereich(<CallOverlay />)
    act(() => {
      useCallStore.setState({
        state: 'active',
        kind: 'direkt',
        partner: { userId: 2, username: 'Maik', avatarUrl: null },
        raum: 'r1',
      })
    })
    expect(screen.getByLabelText(i18n.t('calls.endCall'))).toBeTruthy()
    expect(screen.getByText(i18n.t('calls.hangUp'))).toBeTruthy()
  })
})
