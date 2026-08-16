import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { installFakeAudio } from '@/test/fakeAudio'
import { FakeWebSocket, installFakeWebSocket } from '@/test/fakeWebSocket'
import { useSprachsitzung } from './useSprachsitzung'

let audio: ReturnType<typeof installFakeAudio>
let sockets: ReturnType<typeof installFakeWebSocket>

function leitung(index = 0): FakeWebSocket {
  return sockets.instances[index]
}

/** Startet die Sitzung und wartet, bis das Mikrofon wirklich laeuft. */
async function sitzung() {
  const haken = renderHook(() => useSprachsitzung())
  act(() => haken.result.current.starten())
  await act(async () => {
    leitung().simulateOpen()
    // `starteAufnahme` ist ein Promise; ohne diesen Durchlauf haengt das
    // Mikrofon noch in der Warteschlange und `beenden` traefe ins Leere.
    await Promise.resolve()
    await Promise.resolve()
  })
  return haken
}

describe('useSprachsitzung', () => {
  beforeEach(() => {
    audio = installFakeAudio()
    sockets = installFakeWebSocket()
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  afterEach(() => {
    vi.useRealTimers()
    sockets.restore()
    audio.restore()
  })

  it('faengt bei aus an', () => {
    const { result } = renderHook(() => useSprachsitzung())

    expect(result.current.zustand).toBe('aus')
    expect(sockets.instances).toHaveLength(0)
  })

  it('verbindet ohne Token im Pfad', async () => {
    await sitzung()

    // Kein Geheimnis in der URL — der WS haengt unter `/api`, damit das
    // Cookie mitgeht, und traegt sonst nichts.
    expect(leitung().url).toMatch(/^wss?:\/\//)
    expect(leitung().url.endsWith('/api/ai/voice/ws')).toBe(true)
  })

  it('oeffnet das Mikrofon erst, wenn die Leitung steht', async () => {
    const haken = renderHook(() => useSprachsitzung())
    act(() => haken.result.current.starten())

    expect(haken.result.current.zustand).toBe('verbindet')
    expect(audio.letzterStrom()).toBeNull()

    await act(async () => {
      leitung().simulateOpen()
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(audio.letzterStrom()).not.toBeNull()
  })

  it('startet keine zweite Sitzung, solange eine laeuft', async () => {
    const haken = await sitzung()

    act(() => haken.result.current.starten())

    expect(sockets.instances).toHaveLength(1)
  })

  it('uebernimmt den Zustand vom Server', async () => {
    const haken = await sitzung()

    act(() => leitung().simulateMessage({ art: 'bereit' }))
    expect(haken.result.current.zustand).toBe('bereit')

    act(() => leitung().simulateMessage({ art: 'zustand', zustand: 'denkt' }))
    expect(haken.result.current.zustand).toBe('denkt')
  })

  it('bricht die Wiedergabe ab, sobald der Mensch dazwischenredet', async () => {
    const haken = await sitzung()
    act(() => leitung().simulateMessage({ art: 'zustand', zustand: 'spricht' }))

    // Ein Tonstueck ist unterwegs und wird eingeplant. Aufnahme und Wiedergabe
    // haben je einen eigenen Kontext — gesucht ist der, in dem etwas laeuft.
    act(() => leitung().onmessage?.({ data: new Int16Array(4096).buffer } as MessageEvent))
    const gespielt = audio.kontexte.flatMap((kontext) => kontext.quellen)
    expect(gespielt).toHaveLength(1)

    act(() => leitung().simulateMessage({ art: 'zustand', zustand: 'hoert' }))

    // Zwei Dinge muessen passieren: lokal sofort still werden, und der
    // Gegenstelle sagen, dass sie aufhoeren soll. Nur eines reicht nicht — das
    // Erste allein laesst die Rechnung weiterlaufen, das Zweite allein laesst
    // den Lautsprecher weiterreden.
    expect(gespielt[0].gestoppt).toBe(true)
    expect(leitung().sent).toContain(JSON.stringify({ art: 'unterbrechen' }))
    expect(haken.result.current.zustand).toBe('hoert')
  })

  it('fuehrt den Wortwechsel mit', async () => {
    const haken = await sitzung()

    act(() => {
      leitung().simulateMessage({ art: 'gehoert', text: 'welche server laufen?' })
      leitung().simulateMessage({ art: 'antworttext', text: 'Zwei ' })
      leitung().simulateMessage({ art: 'antworttext', text: 'laufen.' })
    })

    // Die KI schickt ihr Transkript stueckweise. Zwei Stuecke sind ein Satz.
    expect(haken.result.current.zeilen).toEqual([
      { wer: 'ich', text: 'welche server laufen?' },
      { wer: 'ki', text: 'Zwei laufen.' },
    ])
  })

  it('zeigt das laufende Werkzeug und raeumt es wieder weg', async () => {
    const haken = await sitzung()

    act(() => leitung().simulateMessage({ art: 'werkzeug', name: 'list_my_servers' }))
    expect(haken.result.current.werkzeug).toBe('list_my_servers')

    act(() => leitung().simulateMessage({ art: 'zustand', zustand: 'bereit' }))
    expect(haken.result.current.werkzeug).toBeNull()
  })

  it('meldet einen Stoerungsrahmen als uebersetzbaren Schluessel', async () => {
    const haken = await sitzung()

    act(() => leitung().simulateMessage({ art: 'stoerung', code: 'irgendwas' }))

    // Der Schluessel, nicht der Wortlaut der Gegenstelle. Fremdtext gehoert
    // nicht ungeprueft in die Oberflaeche.
    expect(haken.result.current.fehler).toBe('ai.voice.errors.provider')
  })

  it('nimmt die Stoerungsmeldung zurueck, sobald die Leitung wieder traegt', async () => {
    const haken = await sitzung()

    // Anlass: die Ueberschrift stand dauerhaft auf „Sprachverbindung verloren",
    // obwohl Ton und Gespraech laengst weiterliefen. Ein einziger unkritischer
    // Anbieterfehler setzte `fehler`, und nichts nahm ihn je zurueck. Wer hoert,
    // dass es weitergeht, darf oben nicht das Gegenteil lesen.
    for (const zustand of ['bereit', 'hoert', 'spricht'] as const) {
      act(() => leitung().simulateMessage({ art: 'stoerung', code: 'irgendwas' }))
      expect(haken.result.current.fehler).toBe('ai.voice.errors.provider')

      act(() => leitung().simulateMessage({ art: 'zustand', zustand }))
      expect(haken.result.current.fehler).toBeNull()
    }

    // Auch der Begruessungsrahmen einer frisch stehenden Leitung raeumt auf —
    // nach dem Neuverbinden gilt der Fehler von vorhin nicht mehr.
    act(() => leitung().simulateMessage({ art: 'stoerung', code: 'irgendwas' }))
    act(() => leitung().simulateMessage({ art: 'bereit' }))
    expect(haken.result.current.fehler).toBeNull()
  })

  it('laesst die Meldung stehen, solange die Gegenstelle nur denkt', async () => {
    const haken = await sitzung()

    act(() => leitung().simulateMessage({ art: 'stoerung', code: 'irgendwas' }))
    act(() => leitung().simulateMessage({ art: 'zustand', zustand: 'denkt' }))

    // `denkt` ist kein Beweis: dort schweigt die Gegenstelle ohnehin, und ein
    // Fehler, der genau dann kam, ist noch keiner von gestern. Ihn hier
    // wegzuraeumen hiesse, die einzige Meldung zu loeschen, die der Mensch je
    // ueber eine wirklich abgerissene Sitzung bekommt.
    expect(haken.result.current.fehler).toBe('ai.voice.errors.provider')
  })

  it('nimmt gezeigte Stellen auf, die zuletzt gezeigte zuletzt', async () => {
    const haken = await sitzung()

    act(() => {
      leitung().simulateMessage({
        art: 'beleg',
        quelle: 'server.properties',
        zeilen: ['online-mode=true'],
      })
      leitung().simulateMessage({
        art: 'beleg',
        quelle: 'latest.log',
        zeilen: ['[12:03:44] [Server thread/ERROR]: Adresse belegt', '  at Server.bind(Server.java:88)'],
      })
    })

    // Die Reihenfolge ist die Zusage: die Ansicht zeigt die letzte, und „die
    // letzte" muss die sein, ueber die gerade gesprochen wird.
    expect(haken.result.current.belege).toEqual([
      { quelle: 'server.properties', zeilen: ['online-mode=true'] },
      {
        quelle: 'latest.log',
        zeilen: ['[12:03:44] [Server thread/ERROR]: Adresse belegt', '  at Server.bind(Server.java:88)'],
      },
    ])
  })

  it('uebernimmt keinen Beleg ohne Zeilen', async () => {
    const haken = await sitzung()

    act(() => {
      leitung().simulateMessage({ art: 'beleg', quelle: 'latest.log', zeilen: [] })
      leitung().simulateMessage({ art: 'beleg', quelle: 'latest.log' })
      leitung().simulateMessage({ art: 'beleg', quelle: 'latest.log', zeilen: 'eine Zeile' })
    })

    // Ein leerer Kasten mit der Ueberschrift „Belegstelle" behauptet, es gaebe
    // etwas zu lesen. Der Rahmen kommt zwar aus unserem Backend, sein Inhalt
    // aber aus einem Werkzeugergebnis — hier wird nichts geglaubt, was nicht
    // dasteht.
    expect(haken.result.current.belege).toEqual([])
  })

  it('ueberspringt Rahmen, die kein JSON sind', async () => {
    const haken = await sitzung()

    act(() => leitung().simulateMessage('kein json'))

    expect(haken.result.current.zustand).toBe('verbindet')
    expect(haken.result.current.fehler).toBeNull()
  })

  it('verbindet nach einem Abbruch nicht von selbst neu', async () => {
    const haken = await sitzung()

    act(() => leitung().simulateClose(1006))
    await act(async () => {
      vi.advanceTimersByTime(5_000)
    })

    // Eine Sprachsitzung, die sich von selbst wieder oeffnet, nimmt ungefragt
    // das Mikrofon in Betrieb. Bei einem Werkzeug, das mithoert, ist das die
    // falsche Voreinstellung.
    expect(sockets.instances).toHaveLength(1)
    expect(haken.result.current.zustand).toBe('aus')
  })

  it('verbindet nach der Hoechstdauer neu — und nur dann', async () => {
    const haken = await sitzung()

    act(() => leitung().simulateMessage({ art: 'abgelaufen' }))
    act(() => leitung().simulateClose(1000))
    expect(haken.result.current.zustand).toBe('verbindet')

    await act(async () => {
      vi.advanceTimersByTime(500)
    })

    // Neu verbinden heisst: sich erneut anmelden. Genau dafuer gibt es die
    // Grenze — ein stundenlang offener Socket umginge Ablauf und Sperrliste.
    expect(sockets.instances).toHaveLength(2)
  })

  it('schliesst Mikrofon und Leitung beim Beenden', async () => {
    const haken = await sitzung()

    act(() => haken.result.current.beenden())

    expect(audio.letzterStrom()?.getTracks()[0].gestoppt).toBe(true)
    expect(leitung().readyState).toBe(FakeWebSocket.CLOSED)
    expect(haken.result.current.zustand).toBe('aus')
  })

  it('verbindet nach dem Beenden nicht mehr neu, auch wenn ein Ablauf kam', async () => {
    const haken = await sitzung()
    act(() => leitung().simulateMessage({ art: 'abgelaufen' }))

    act(() => haken.result.current.beenden())
    await act(async () => {
      vi.advanceTimersByTime(2_000)
    })

    expect(sockets.instances).toHaveLength(1)
    expect(haken.result.current.zustand).toBe('aus')
  })

  it('laesst beim Verlassen der Seite kein offenes Mikrofon zurueck', async () => {
    const haken = await sitzung()

    haken.unmount()

    expect(audio.letzterStrom()?.getTracks()[0].gestoppt).toBe(true)
    expect(leitung().readyState).toBe(FakeWebSocket.CLOSED)
  })

  it('meldet eine verweigerte Mikrofonfreigabe und raeumt auf', async () => {
    audio.restore()
    audio = installFakeAudio({ verweigern: true })
    const haken = renderHook(() => useSprachsitzung())

    act(() => haken.result.current.starten())
    await act(async () => {
      leitung().simulateOpen()
      await Promise.resolve()
      await Promise.resolve()
    })

    await waitFor(() => expect(haken.result.current.fehler).toBe('ai.voice.errors.microphone'))
    expect(haken.result.current.zustand).toBe('aus')
    expect(leitung().readyState).toBe(FakeWebSocket.CLOSED)
  })
})
