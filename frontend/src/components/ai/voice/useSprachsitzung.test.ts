import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { installFakeAudio, type FakeAudioContext } from '@/test/fakeAudio'
import { FakeWebSocket, installFakeWebSocket } from '@/test/fakeWebSocket'
import { useSprachsitzung } from './useSprachsitzung'

let audio: ReturnType<typeof installFakeAudio>
let sockets: ReturnType<typeof installFakeWebSocket>

function leitung(index = 0): FakeWebSocket {
  return sockets.instances[index]
}

/**
 * Aufnahme und Wiedergabe haben je einen eigenen Kontext. Auseinandergehalten
 * werden sie am Prozessor: den baut nur die Aufnahme.
 */
function tonKontext(): FakeAudioContext {
  return audio.kontexte.filter((kontext) => kontext.prozessoren.length === 0)[0]
}

function mikroKontext(): FakeAudioContext {
  return audio.kontexte.filter((kontext) => kontext.prozessoren.length > 0)[0]
}

/** Startet die Sitzung und wartet, bis das Mikrofon wirklich laeuft. */
async function sitzung() {
  const haken = renderHook(() => useSprachsitzung())
  await act(() => haken.result.current.starten())
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
    await act(() => haken.result.current.starten())

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

    await act(() => haken.result.current.starten())

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

  it('unterbricht nichts, wenn gar nichts laeuft', async () => {
    // Der Regelfall, und bis zum 16.08.2026 der Fehlerfall: der Mensch faengt
    // an zu reden, waehrend die KI schweigt. Es gibt nichts abzubrechen.
    //
    // Vorher ging das `unterbrechen` trotzdem hinaus, wurde zu einem
    // `response.cancel` ins Leere, und die damalige Gegenstelle (OpenAIs
    // Realtime-API) antwortete mit `response_cancel_not_active`. Der Sprechende
    // las daraufhin bei **jedem** Satz „Der Sprachanbieter hat die Sitzung
    // abgebrochen" — eine Stoerungsmeldung fuer eine Leitung, die einwandfrei
    // trug.
    //
    // Die Zusage bleibt, obwohl die heutige Bruecke ein `unterbrechen` ins
    // Leere klaglos schluckt: sie meldete dem Backend ein Dazwischenreden, das
    // nicht stattgefunden hat, und das ist auch ohne Fehlermeldung falsch.
    const haken = await sitzung()

    act(() => leitung().simulateMessage({ art: 'zustand', zustand: 'hoert' }))

    expect(leitung().sent).not.toContain(JSON.stringify({ art: 'unterbrechen' }))
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

  it('meldet ein erschoepftes Kontingent mit eigener Meldung, die „bereit" ueberlebt', async () => {
    const haken = await sitzung()

    act(() => leitung().simulateMessage({ art: 'stoerung', grund: 'kontingent' }))

    // „Warte kurz" ist eine andere Auskunft als „etwas ist kaputt" — der Grund
    // waehlt den Schluessel, wird aber nie selbst als Schluessel durchgereicht.
    expect(haken.result.current.fehler).toBe('ai.voice.errors.quota')

    // Das Backend meldet direkt nach der Stoerung `zustand=bereit`. Die
    // Auskunft muss das ueberleben, sonst liest sie niemand.
    act(() => leitung().simulateMessage({ art: 'zustand', zustand: 'bereit' }))
    expect(haken.result.current.fehler).toBe('ai.voice.errors.quota')
  })

  it('nimmt die Stoerungsmeldung zurueck, sobald die Leitung wieder traegt', async () => {
    const haken = await sitzung()

    // Anlass: die Ueberschrift stand dauerhaft auf „Sprachverbindung verloren",
    // obwohl Ton und Gespraech laengst weiterliefen. Ein einziger unkritischer
    // Anbieterfehler setzte `fehler`, und nichts nahm ihn je zurueck. Wer hoert,
    // dass es weitergeht, darf oben nicht das Gegenteil lesen. `bereit` steht
    // bewusst nicht in der Liste — siehe den Test darunter.
    for (const zustand of ['hoert', 'spricht'] as const) {
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

  it('laesst die Meldung stehen, wenn nach der Stoerung nur „bereit" kommt', async () => {
    const haken = await sitzung()

    act(() => leitung().simulateMessage({ art: 'stoerung', code: 'irgendwas' }))
    act(() => leitung().simulateMessage({ art: 'zustand', zustand: 'bereit' }))

    // Das Backend sendet in jedem Fehlerpfad erst die Stoerung und unmittelbar
    // danach `zustand=bereit`. Raeumte `bereit` die Meldung weg, loeschte jede
    // Stoerung sich selbst, bevor ein Mensch sie lesen kann — genau so blieb
    // jeder Anbieterfehler unsichtbar.
    expect(haken.result.current.fehler).toBe('ai.voice.errors.provider')

    // Erst echtes Weiterleben nimmt sie zurueck.
    act(() => leitung().simulateMessage({ art: 'zustand', zustand: 'hoert' }))
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

  it('zeigt den Vorschlag an, auf den ein Ja fehlt', async () => {
    const haken = await sitzung()

    act(() => {
      leitung().simulateMessage({
        art: 'vorschlag',
        vorschlag: {
          id: 'proposal-1',
          tool_name: 'propose_server_delete',
          expected_effect: 'Der Server „Kreativ" und seine Dateien werden entfernt.',
        },
      })
    })

    expect(haken.result.current.vorschlag).toEqual({
      werkzeug: 'propose_server_delete',
      wirkung: 'Der Server „Kreativ" und seine Dateien werden entfernt.',
    })
  })

  it('nimmt den Vorschlag weg, sobald der Mensch etwas sagt', async () => {
    const haken = await sitzung()

    act(() => {
      leitung().simulateMessage({
        art: 'vorschlag',
        vorschlag: { id: 'p1', tool_name: 'propose_backup', expected_effect: '' },
      })
    })
    expect(haken.result.current.vorschlag).not.toBeNull()

    act(() => {
      leitung().simulateMessage({ art: 'gehoert', text: 'Ja, mach das' })
    })

    // Die Bruecke raeumt ihre offenen Vorschlaege auf jedem Weg weg — Ja, Nein
    // und „etwas ganz anderes". Eine Karte, die waehrend der laufenden Loeschung
    // noch „wartet auf dich" sagt, ist die gefaehrlichste Art von falsch.
    expect(haken.result.current.vorschlag).toBeNull()
  })

  it('glaubt keinen Werkzeugnamen, der ein Uebersetzungspfad sein koennte', async () => {
    const haken = await sitzung()

    act(() => {
      // `tool_name` wird als Schluessel `ai.actions.tools.<name>` benutzt. Ein
      // Name mit Punkten liesse den Menschen anderswo in `de.json` landen — und
      // dort steht Text, den er fuer die Beschreibung der Aktion hielte.
      leitung().simulateMessage({
        art: 'vorschlag',
        vorschlag: { id: 'p1', tool_name: '../../permissionDetails.ai_voice_use.title' },
      })
      leitung().simulateMessage({ art: 'vorschlag', vorschlag: { id: 'p2' } })
      leitung().simulateMessage({ art: 'vorschlag', vorschlag: 'propose_backup' })
      leitung().simulateMessage({ art: 'vorschlag' })
    })

    expect(haken.result.current.vorschlag).toBeNull()
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

  it('schliesst das Mikrofon, wenn die Leitung waehrend der Freigabe abreisst', async () => {
    const haken = renderHook(() => useSprachsitzung())
    await act(() => haken.result.current.starten())

    act(() => {
      leitung().simulateOpen()
      // Der Abriss kommt, waehrend getUserMedia noch auf die Freigabe wartet —
      // `gewollt` bleibt dabei wahr, denn beendet hat niemand. Vorher lief das
      // Mikrofon dann bei Zustand „aus" weiter, und der naechste starten()
      // ueberschrieb den Stream kommentarlos.
      leitung().simulateClose(1006)
    })
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(audio.letzterStrom()?.getTracks()[0].gestoppt).toBe(true)
    expect(haken.result.current.zustand).toBe('aus')
  })

  it('laesst beim Verlassen der Seite kein offenes Mikrofon zurueck', async () => {
    const haken = await sitzung()

    haken.unmount()

    expect(audio.letzterStrom()?.getTracks()[0].gestoppt).toBe(true)
    expect(leitung().readyState).toBe(FakeWebSocket.CLOSED)
  })

  it('meldet eine abgewiesene Leitung, statt stumm auszugehen', async () => {
    const haken = renderHook(() => useSprachsitzung())
    await act(() => haken.result.current.starten())

    // Das Backend weist ab, **bevor** es das Upgrade annimmt: fehlendes Recht
    // `ai.voice.use`, kein eingerichteter Zugang, kein entschluesselbarer
    // Schluessel — jedes Mal `close(1008)` vor `accept()`
    // (`routers/ai_voice.py::voice_ws`). Fuer den Browser ist das kein
    // Abbruch, sondern ein gescheiterter Handschlag, und der sieht genau so
    // aus: erst `onerror`, dann ein Abbruch mit 1006. Den 1008 des Servers
    // bekommt der Client nie zu sehen — deshalb wertet der Hook zu Recht
    // keinen Code aus, sondern haengt die Meldung an `onerror`.
    act(() => {
      leitung().onerror?.({} as Event)
      leitung().simulateClose(1006)
    })

    // Die Abweisung ist die sichtbare Seite einer Rechtepruefung. Ohne diese
    // Meldung drueckt jemand ohne `ai.voice.use` auf den Knopf, der Knopf geht
    // wieder aus, und nichts sagt ihm, warum. Sie muss den unmittelbar
    // folgenden Abbruch ueberleben, sonst loescht der Abbruch sie weg, bevor
    // ein Mensch sie liest.
    expect(haken.result.current.fehler).toBe('ai.voice.errors.connection')
    expect(haken.result.current.zustand).toBe('aus')

    // Der Lautsprecher wurde bei der Geste schon geoeffnet und darf nicht
    // offen bleiben. Das Mikrofon wurde nie erfragt — danach fragt der Hook
    // erst, wenn die Leitung steht, und eine Abweisung ist kein Anlass, ein
    // Mikrofon aufzumachen.
    expect(tonKontext().geschlossen).toBe(true)
    expect(audio.letzterStrom()).toBeNull()

    // Und kein selbsttaetiger zweiter Versuch gegen eine Tuer, die zu ist.
    await act(async () => {
      vi.advanceTimersByTime(5_000)
    })
    expect(sockets.instances).toHaveLength(1)
    expect(haken.result.current.fehler).toBe('ai.voice.errors.connection')
  })

  it('macht den Lautsprecher schon bei der Nutzergeste bereit', async () => {
    audio.restore()
    audio = installFakeAudio({ gesperrt: true })
    const haken = renderHook(() => useSprachsitzung())

    // Der Kontext muss VOR dem await in starten() stehen (Nutzergeste!) —
    // das await hier prueft nur den Rest des Aufbaus mit.
    await act(() => haken.result.current.starten())

    // Browser entsperren Ton nur in einer Nutzergeste. Der Klick auf den
    // Sprachknopf ist die einzige, die diese Sitzung je bekommt: wer den
    // Kontext erst beim ersten Tonstueck oeffnet, oeffnet ihn in einem
    // Netzwerkereignis — und dann bleibt er gesperrt. Der Mensch saehe eine
    // laufende Sitzung und hoerte nichts.
    expect(audio.kontexte).toHaveLength(1)
    expect(audio.kontexte[0].state).toBe('running')
  })

  it('misst beim Sprechen die Stimme und beim Zuhoeren das Mikrofon', async () => {
    const haken = await sitzung()

    // Ein lauter Block durchs Mikrofon. Der Pegel ist geglaettet, nach dem
    // ersten Block steht er deshalb bei einem Drittel des Ausschlags.
    mikroKontext().prozessoren[0].sende(new Float32Array(64).fill(0.5))
    // Und ein Tonstueck der KI laeuft, mit vollem Ausschlag am Messpunkt.
    act(() => leitung().onmessage?.({ data: new Int16Array(64).buffer } as MessageEvent))
    tonKontext().messer[0].welle = 32

    act(() => leitung().simulateMessage({ art: 'zustand', zustand: 'spricht' }))
    expect(haken.result.current.pegel()).toBe(1)

    // Wer gerade redet, bestimmt die Quelle. Ein Maximum ueber beide waere
    // bequemer und falsch — dann atmete die Blase auch dann, wenn nur ein
    // Luefter neben dem Mikrofon steht.
    act(() => leitung().simulateMessage({ art: 'zustand', zustand: 'hoert' }))
    expect(haken.result.current.pegel()).toBeCloseTo(0.3, 5)
  })

  it('meldet eine verweigerte Mikrofonfreigabe und raeumt auf', async () => {
    audio.restore()
    audio = installFakeAudio({ verweigern: true })
    const haken = renderHook(() => useSprachsitzung())

    await act(() => haken.result.current.starten())
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
