import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FakeMediaStream, installFakeAudio, type FakeAudioContext } from '@/test/fakeAudio'
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

  it('handelt Realtime per WebRTC aus und sendet kein Mikrofon-Audio über den Panel-Socket', async () => {
    class FakePeer {
      static instances: FakePeer[] = []
      localDescription: RTCSessionDescriptionInit | null = null
      remoteDescription: RTCSessionDescriptionInit | null = null
      ontrack: ((event: RTCTrackEvent) => void) | null = null
      geschlossen = false

      constructor() {
        FakePeer.instances.push(this)
      }

      addTrack() {}
      createDataChannel() { return {} }
      async createOffer() { return { type: 'offer' as const, sdp: 'v=0\r\nrealtime-offer' } }
      async setLocalDescription(value: RTCSessionDescriptionInit) { this.localDescription = value }
      async setRemoteDescription(value: RTCSessionDescriptionInit) { this.remoteDescription = value }
      close() { this.geschlossen = true }
    }
    const originalPeer = globalThis.RTCPeerConnection
    ;(globalThis as { RTCPeerConnection?: unknown }).RTCPeerConnection = FakePeer
    try {
      const haken = renderHook(() => useSprachsitzung(undefined, 'openai_realtime'))
      await act(() => haken.result.current.starten())
      await act(async () => {
        leitung().simulateOpen()
        await Promise.resolve()
        await Promise.resolve()
        await Promise.resolve()
      })

      expect(FakePeer.instances).toHaveLength(1)
      expect(leitung().sent).toHaveLength(1)
      expect(JSON.parse(leitung().sent[0])).toEqual({
        art: 'webrtc_offer',
        sdp: 'v=0\r\nrealtime-offer',
      })
      act(() => leitung().simulateMessage({ art: 'webrtc_answer', sdp: 'v=0\r\nanswer' }))
      await waitFor(() => expect(FakePeer.instances[0].remoteDescription?.sdp).toContain('answer'))
      act(() => haken.result.current.beenden())
      expect(FakePeer.instances[0].geschlossen).toBe(true)
      expect(audio.letzterStrom()?.spuren[0].gestoppt).toBe(true)
    } finally {
      if (originalPeer) globalThis.RTCPeerConnection = originalPeer
      else delete (globalThis as { RTCPeerConnection?: unknown }).RTCPeerConnection
    }
  })

  describe('GPT-Live', () => {
    /**
     * Ein Peer, dessen ICE-Sammlung der Test beendet. GPT-Live nimmt genau ein
     * Angebot und kein Nachreichen von Kandidaten — das Browserbeispiel der
     * Dokumentation wartet deshalb auf `complete`, mit zehn Sekunden Frist.
     */
    class IcePeer extends EventTarget {
      static instances: IcePeer[] = []
      localDescription: RTCSessionDescriptionInit | null = null
      remoteDescription: RTCSessionDescriptionInit | null = null
      iceGatheringState: RTCIceGatheringState = 'new'
      ontrack: ((event: RTCTrackEvent) => void) | null = null

      constructor() {
        super()
        IcePeer.instances.push(this)
      }

      addTrack() {}
      createDataChannel() { return {} }
      async createOffer() { return { type: 'offer' as const, sdp: 'v=0\r\nlive-offer' } }
      async setLocalDescription(value: RTCSessionDescriptionInit) {
        this.localDescription = value
        this.iceGatheringState = 'gathering'
      }
      async setRemoteDescription(value: RTCSessionDescriptionInit) { this.remoteDescription = value }
      close() {}

      sammlungFertig() {
        this.localDescription = { type: 'offer', sdp: 'v=0\r\nlive-offer\r\na=candidate:1 1 udp 1 192.0.2.1 5000 typ host' }
        this.iceGatheringState = 'complete'
        this.dispatchEvent(new Event('icegatheringstatechange'))
      }
    }

    let originalPeer: typeof RTCPeerConnection | undefined

    beforeEach(() => {
      IcePeer.instances = []
      originalPeer = globalThis.RTCPeerConnection
      ;(globalThis as { RTCPeerConnection?: unknown }).RTCPeerConnection = IcePeer
    })

    afterEach(() => {
      if (originalPeer) globalThis.RTCPeerConnection = originalPeer
      else delete (globalThis as { RTCPeerConnection?: unknown }).RTCPeerConnection
    })

    async function liveSitzung() {
      const haken = renderHook(() => useSprachsitzung(undefined, 'openai_live'))
      await act(() => haken.result.current.starten())
      await act(async () => {
        leitung().simulateOpen()
        await Promise.resolve()
        await Promise.resolve()
        await Promise.resolve()
      })
      return haken
    }

    it('schickt das Angebot erst mit den gesammelten Kandidaten', async () => {
      await liveSitzung()

      expect(IcePeer.instances).toHaveLength(1)
      // Noch in der Sammlung: ein Angebot ohne Kandidaten wäre für GPT-Live
      // ein Angebot, das nie zu einer Verbindung wird.
      expect(leitung().sent).toHaveLength(0)

      await act(async () => {
        IcePeer.instances[0].sammlungFertig()
        await Promise.resolve()
        await Promise.resolve()
      })
      expect(leitung().sent).toHaveLength(1)
      expect(JSON.parse(leitung().sent[0])).toEqual({
        art: 'webrtc_offer',
        sdp: 'v=0\r\nlive-offer\r\na=candidate:1 1 udp 1 192.0.2.1 5000 typ host',
      })
    })

    it('gibt nach zehn Sekunden ohne fertige Sammlung auf und meldet die Verbindung', async () => {
      const haken = await liveSitzung()

      await act(async () => {
        vi.advanceTimersByTime(10_000)
        await Promise.resolve()
        await Promise.resolve()
      })
      await waitFor(() => expect(haken.result.current.fehler).toBe('ai.voice.errors.connection'))
      expect(haken.result.current.fehlerCode).toBe('ICE_TIMEOUT')
      expect(leitung().sent).toHaveLength(0)
    })
  })

  it.each([
    ['AI_PROVIDER_SAFETY_STOPPED', 'ai.voice.errors.safety'],
    ['REALTIME_CONTENT_STOPPED', 'ai.voice.errors.content'],
    ['REALTIME_CONNECTION_LOST', 'ai.voice.errors.connection'],
    ['REALTIME_HANDSHAKE_FAILED', 'ai.voice.errors.provider'],
  ])('übersetzt den Endcode %s in %s', async (code, text) => {
    // Ein Sicherheitsstopp ist keine Netzstörung, und „hat nicht geklappt"
    // verschweigt, dass niemand es still noch einmal versucht.
    const haken = await sitzung()

    act(() => leitung().simulateMessage({ art: 'fehler', code }))

    expect(haken.result.current.fehler).toBe(text)
    expect(haken.result.current.fehlerCode).toBe(code)
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

  it('folgt dem strukturierten Regionalfokus ohne Sprachheuristik', async () => {
    const haken = await sitzung()

    act(() => leitung().simulateMessage({ art: 'region_ui', focus: { tab: 'social' } }))
    expect(haken.result.current.regionalFocus).toEqual({ tab: 'social' })

    act(() => leitung().simulateMessage({ art: 'region_ui', focus: { tab: 'traffic' } }))
    expect(haken.result.current.regionalFocus).toEqual({ tab: 'traffic' })
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

  it('zeigt bereinigte Websuchergebnisse im Quellenbereich', async () => {
    const haken = await sitzung()

    act(() => {
      leitung().simulateMessage({
        art: 'werkzeug',
        name: 'web_search',
        web_results: [{
          title: 'Meldung aus Moskau',
          url: 'https://example.invalid/moskau',
          description: 'Kurzer, bereits serverseitig bereinigter Auszug.',
        }],
      })
    })

    expect(haken.result.current.belege).toEqual([{
      quelle: 'Meldung aus Moskau',
      zeilen: [
        'https://example.invalid/moskau',
        'Kurzer, bereits serverseitig bereinigter Auszug.',
      ],
    }])
  })

  it('nimmt eine gemeldete Karte nur als Anstoss zum Neuladen', async () => {
    // Seit dem 25.09.2026 zeigt die Ansicht die Karte aus der Vorschlagsliste
    // des Panels und bestätigt nur per Klick (`OffeneKarten`). Aus dem Rahmen
    // übernimmt die Sitzung deshalb nichts, keinen Werkzeugnamen und keine
    // Kennung — nur, dass es etwas Neues gibt.
    const haken = await sitzung()
    expect(haken.result.current.kartenImpuls).toBe(0)

    act(() => {
      leitung().simulateMessage({
        art: 'vorschlag',
        vorschlag: { id: 'p1', tool_name: 'propose_backup', expected_effect: '' },
        klick: true,
      })
    })
    expect(haken.result.current.kartenImpuls).toBe(1)

    // Ein Rahmen ohne Karte meldet nichts Neues, ein gesprochenes Ja auch nicht.
    act(() => {
      leitung().simulateMessage({ art: 'vorschlag', vorschlag: null })
      leitung().simulateMessage({ art: 'gehoert', text: 'Ja, mach das' })
    })
    expect(haken.result.current.kartenImpuls).toBe(1)
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
    // bequemer und falsch — dann atmete der Schwarm auch dann, wenn nur ein
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

  it('reagiert sofort spekulativ auf werkzeug_gestartet und tool_start', async () => {
    const haken = await sitzung()

    act(() => leitung().simulateMessage({ art: 'werkzeug_gestartet', name: 'calendar_read', spekulativ: true }))
    expect(haken.result.current.werkzeug).toBe('calendar_read')

    act(() => leitung().simulateMessage({ art: 'tool_start', tool_name: 'read_server_status' }))
    expect(haken.result.current.werkzeug).toBe('read_server_status')
  })

  it('haelt den Werkzeuglauf bis zum Ende des Zugs, auch wenn die KI dazwischen spricht', async () => {
    const haken = await sitzung()
    expect(haken.result.current.werkzeugLaeuft).toBe(false)
    expect(haken.result.current.werkzeugStarts).toBe(0)

    act(() => leitung().simulateMessage({ art: 'werkzeug_gestartet', name: 'list_my_servers' }))
    expect(haken.result.current.werkzeugLaeuft).toBe(true)
    expect(haken.result.current.werkzeugStarts).toBe(1)

    // „Ich sehe nach" mitten im Zug und das Ergebnis des Werkzeugs beenden
    // den Zug nicht. Der Schwarm bliebe sonst nicht beim Logo, sondern
    // spränge zwischen Logo und Globus hin und her.
    act(() => leitung().simulateMessage({ art: 'zustand', zustand: 'spricht' }))
    act(() => leitung().simulateMessage({ art: 'werkzeug', name: 'list_my_servers' }))
    act(() => leitung().simulateMessage({ art: 'zustand', zustand: 'denkt' }))
    expect(haken.result.current.werkzeugLaeuft).toBe(true)

    act(() => leitung().simulateMessage({ art: 'zustand', zustand: 'bereit' }))
    expect(haken.result.current.werkzeugLaeuft).toBe(false)
  })

  it('zaehlt jeden Werkzeugstart, auch zweimal dasselbe Werkzeug', async () => {
    const haken = await sitzung()

    act(() => leitung().simulateMessage({ art: 'werkzeug_gestartet', name: 'read_server_status' }))
    act(() => leitung().simulateMessage({ art: 'tool_start', tool_name: 'read_server_status' }))
    // Ein Ergebnis ist kein Start — es schickt keinen zweiten Lichtring.
    act(() => leitung().simulateMessage({ art: 'werkzeug', name: 'read_server_status' }))

    expect(haken.result.current.werkzeugStarts).toBe(2)
  })

  it('beendet den Werkzeuglauf, sobald der Mensch wieder spricht oder auflegt', async () => {
    const haken = await sitzung()

    act(() => leitung().simulateMessage({ art: 'werkzeug_gestartet', name: 'list_my_servers' }))
    act(() => leitung().simulateMessage({ art: 'zustand', zustand: 'hoert' }))
    expect(haken.result.current.werkzeugLaeuft).toBe(false)

    act(() => leitung().simulateMessage({ art: 'werkzeug_gestartet', name: 'list_my_servers' }))
    act(() => haken.result.current.beenden())
    expect(haken.result.current.werkzeugLaeuft).toBe(false)
  })

  it('meldet die abgelaufene Sitzung, bis die neue ihren ersten Zustand schickt', async () => {
    const haken = await sitzung()

    act(() => leitung().simulateMessage({ art: 'abgelaufen' }))
    expect(haken.result.current.abgelaufen).toBe(true)
    act(() => leitung().simulateClose(1000))
    await act(async () => {
      vi.advanceTimersByTime(500)
    })
    // Neu verbunden, aber noch ohne Nachricht: der Schwarm liegt noch flach.
    expect(sockets.instances).toHaveLength(2)
    expect(haken.result.current.abgelaufen).toBe(true)

    // Realtime und GPT-Live melden sich mit `zustand: bereit` — nicht mit
    // `bereit`. Stand hier nur `bereit`, blieb „abgelaufen" für immer stehen.
    act(() => leitung(1).simulateOpen())
    act(() => leitung(1).simulateMessage({ art: 'zustand', zustand: 'bereit' }))
    expect(haken.result.current.abgelaufen).toBe(false)
  })

  it('nimmt „abgelaufen" zurueck, wenn das Neuverbinden scheitert', async () => {
    const haken = await sitzung()

    act(() => leitung().simulateMessage({ art: 'abgelaufen' }))
    act(() => leitung().simulateClose(1000))
    await act(async () => {
      vi.advanceTimersByTime(500)
    })
    act(() => leitung(1).simulateClose(1006))

    // Aus ist aus: eine Sitzung, die nicht wiederkommt, ist nicht abgelaufen.
    expect(haken.result.current.zustand).toBe('aus')
    expect(haken.result.current.abgelaufen).toBe(false)
  })

  it('misst per WebRTC beim Sprechen die Stimme der KI und beim Zuhoeren das Mikrofon', async () => {
    class StimmPeer {
      static instances: StimmPeer[] = []
      localDescription: RTCSessionDescriptionInit | null = null
      ontrack: ((event: RTCTrackEvent) => void) | null = null
      constructor() {
        StimmPeer.instances.push(this)
      }
      addTrack() {}
      createDataChannel() { return {} }
      async createOffer() { return { type: 'offer' as const, sdp: 'v=0\r\noffer' } }
      async setLocalDescription(value: RTCSessionDescriptionInit) { this.localDescription = value }
      async setRemoteDescription() {}
      close() {}
    }
    const originalPeer = globalThis.RTCPeerConnection
    ;(globalThis as { RTCPeerConnection?: unknown }).RTCPeerConnection = StimmPeer
    // jsdom spielt nichts ab; `play()` muss nur gelingen.
    const abspielen = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined)
    try {
      const haken = renderHook(() => useSprachsitzung(undefined, 'openai_realtime'))
      await act(() => haken.result.current.starten())
      await act(async () => {
        leitung().simulateOpen()
        await Promise.resolve()
        await Promise.resolve()
        await Promise.resolve()
      })
      act(() => {
        StimmPeer.instances[0].ontrack?.({ streams: [new FakeMediaStream()] } as unknown as RTCTrackEvent)
      })
      const kontext = audio.kontexte[audio.kontexte.length - 1]
      // Der erste Messpunkt hängt am Mikrofon, der zweite an der Stimme.
      expect(kontext.messer).toHaveLength(2)
      kontext.messer[0].welle = 8
      kontext.messer[1].welle = 32

      // Vorher lief die Stimme per WebRTC an keinem Messpunkt vorbei: beim
      // Sprechen fiel der Pegel auf die Wiedergabe zurück, die es auf diesem
      // Weg gar nicht gibt — der Schwarm stand still, während die KI sprach.
      act(() => leitung().simulateMessage({ art: 'zustand', zustand: 'spricht' }))
      expect(haken.result.current.pegel()).toBe(1)
      act(() => leitung().simulateMessage({ art: 'zustand', zustand: 'hoert' }))
      expect(haken.result.current.pegel()).toBeCloseTo(0.25, 5)
      act(() => haken.result.current.beenden())
    } finally {
      abspielen.mockRestore()
      if (originalPeer) globalThis.RTCPeerConnection = originalPeer
      else delete (globalThis as { RTCPeerConnection?: unknown }).RTCPeerConnection
    }
  })

  it('normalisiert Geodaten aus alten Werkzeugereignissen vor der Anzeige', async () => {
    const haken = await sitzung()
    const mockGeo = {
      region: 'Berlin',
      summary: 'Wetter sonnig',
      coordinates: { lat: 52.52, lon: 13.405 },
      satellite: { cloud_coverage: 10, confidence: 95 },
      weather: { temperature: 22, condition: 'Clear' },
      news: [],
      social: [],
      traffic: [],
      timestamp: '2026-08-28T00:00:00Z',
    }

    act(() =>
      leitung().simulateMessage({
        art: 'werkzeug_gestartet',
        name: 'analyze_region',
        geo_analysis: mockGeo,
      }),
    )
    expect(haken.result.current.werkzeug).toBe('analyze_region')
    expect(haken.result.current.geoData).toMatchObject({
      status: 'success',
      location: 'Berlin',
      country: '',
      coordinates: { latitude: 52.52, longitude: 13.405 },
    })

    act(() =>
      leitung().simulateMessage({
        art: 'tool',
        tool_name: 'analyze_region',
        geo_analysis: mockGeo,
      }),
    )
    expect(haken.result.current.werkzeug).toBe('analyze_region')
    expect(haken.result.current.geoData?.coordinates.bbox).toHaveLength(4)
  })

  it('ignoriert unvollständige Geodaten, statt die Echtzeitansicht zu beschädigen', async () => {
    const haken = await sitzung()

    act(() =>
      leitung().simulateMessage({
        art: 'tool_start',
        tool_name: 'analyze_region',
        geo_analysis: { location: 'Berlin', coordinates: { latitude: 52.52 } },
      }),
    )

    expect(haken.result.current.werkzeug).toBe('analyze_region')
    expect(haken.result.current.geoData).toBeNull()
  })

  it('wendet einen Landmarkenbefehl auf die vorhandene Analyse an', async () => {
    const haken = await sitzung()
    act(() =>
      leitung().simulateMessage({
        art: 'tool',
        tool_name: 'analyze_region',
        geo_analysis: {
          location: 'Moskau',
          coordinates: { latitude: 55.7558, longitude: 37.6173 },
        },
      }),
    )

    act(() =>
      leitung().simulateMessage({
        art: 'tool',
        tool_name: 'control_region_camera',
        geo_camera: {
          action: 'focus_location',
          command_id: 'camera-1',
          location: 'Basilius-Kathedrale, Moskau',
          country: 'Russland',
          coordinates: { latitude: 55.7525, longitude: 37.6231 },
        },
      }),
    )

    expect(haken.result.current.geoData).toMatchObject({
      location: 'Basilius-Kathedrale, Moskau',
      coordinates: { latitude: 55.7525, longitude: 37.6231 },
      camera: { mode: 'detail', action: 'focus_location', command_id: 'camera-1' },
    })
  })

  it('verarbeitet intent_erkannt mit Intent, Entities und Geodaten', async () => {
    const haken = await sitzung()
    const mockGeo = {
      location: 'Berlin, Deutschland',
      coordinates: { latitude: 52.52, longitude: 13.405, bbox: [13.0883, 52.3382, 13.7611, 52.6755] },
    }

    act(() =>
      leitung().simulateMessage({
        art: 'intent_erkannt',
        intent: 'analyze_region',
        confidence: 0.95,
        entities: { location: 'Berlin' },
        arguments: { location_name: 'Berlin' },
        spekulativ: true,
        geo_analysis: mockGeo,
      }),
    )

    expect(haken.result.current.intentErkannt).toEqual({
      intent: 'analyze_region',
      confidence: 0.95,
      entities: { location: 'Berlin' },
      arguments: { location_name: 'Berlin' },
      spekulativ: true,
      prefetchStatus: undefined,
      revision: undefined,
    })
    expect(haken.result.current.werkzeug).toBe('analyze_region')
    expect(haken.result.current.geoData).toMatchObject({
      status: 'success',
      location: 'Berlin, Deutschland',
      country: '',
      coordinates: mockGeo.coordinates,
    })
  })
})
