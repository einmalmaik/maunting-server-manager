/**
 * Eine Sprachsitzung, von „aus" bis „aus".
 *
 * Der Hook hält drei Dinge zusammen, die einzeln nichts nützen: den
 * WebSocket zum Panel, das Mikrofon und die Wiedergabe. Er hält sie in Refs
 * und nicht im State — es sind Geräte und keine Anzeigewerte, und ein
 * Neuzeichnen darf kein Mikrofon neu öffnen.
 *
 * **Der WebSocket wird hier von Hand geführt und nicht über `useWebSocket`.**
 * Der bestehende Hook ist für Textrahmen gebaut und liefert rohe Strings; hier
 * kommen Binärrahmen an, und sie sind die Hauptlast. Ihn dafür umzubauen hieße,
 * den Konsolen-Stream für einen Fall zu ändern, den er nicht hat.
 *
 * Kein automatischer Wiederverbindungs-Backoff. Eine Sprachsitzung, die sich
 * nach einem Abbruch von selbst wieder öffnet, nimmt ungefragt das Mikrofon in
 * Betrieb — bei einem Werkzeug, das mithört, ist das die falsche Voreinstellung.
 * Die einzige Ausnahme ist das planmäßige Ende nach der Höchstdauer: dort weiß
 * der Client, dass es weitergehen soll, und der Mensch hat nichts anderes
 * entschieden.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { aiApi, type AiRegionalAnalysis } from '@/api/ai'
import { wsProtokolle } from '@/api/client'
import { wsUrl } from '@/config/api'
import { voiceDebug, voiceWarn, voiceError } from '@/lib/voiceDebug'
import { applyGeoCameraCommand, normalizeRegionalAnalysis } from '../geo/regionalAnalysis'
import { AufnahmeAbbruch, starteAufnahme, type Aufnahme } from './audioAufnahme'
import { Wiedergabe } from './audioWiedergabe'
import { aktuelleVerarbeitung, ausgabeGeraetId, eingabeGeraetId } from './audioGeraete'

export type Sprachzustand =
  | 'aus'
  | 'verbindet'
  | 'bereit'
  | 'hoert'
  | 'denkt'
  | 'spricht'

export interface Sprachzeile {
  /** `ich` ist der Mensch, `ki` das Panel. */
  wer: 'ich' | 'ki'
  text: string
}

/**
 * Eine Stelle, die das Panel zum Mitlesen auf den Schirm gelegt hat.
 *
 * Der Betreiber wollte Logzeilen **nicht** vorgelesen bekommen: eine
 * Fehlermeldung aus einem Serverlog ist gesprochen unverständlich und dauert
 * zwanzig Sekunden. Sie erscheint deshalb, und die KI erklärt sie mündlich
 * daneben. Beides ist Fremdtext — `quelle` benennt das Modell, die `zeilen`
 * stammen aus einem Werkzeugergebnis. Hier wird nur gesammelt, nichts geprüft
 * und nichts ausgewertet; die Echtheitsschranke sitzt im Backend.
 */
export interface Beleg {
  /** Woher die Stelle stammt, in Worten des Modells: Datei, Werkzeug, Server. */
  quelle: string
  zeilen: string[]
}

/**
 * Eine Schreibaktion, die auf ein gesprochenes Ja wartet.
 *
 * **Ohne Knopf, und das ist der Punkt.** Im Chat steht hier eine Karte mit
 * „Ausführen"; im Sprachmodus fragt die KI, und der Mensch antwortet. Ein Knopf
 * daneben wäre ein zweiter Weg zum selben Ziel — und damit ein zweiter Zustand,
 * den beide Seiten auseinanderhalten müssten (geklickt, während gesprochen
 * wurde?). Gezeigt wird nur, *was* gleich passiert: welches Werkzeug, welcher
 * Server. Gesprochen ist das schwer zu behalten, gelesen ist es ein Blick.
 *
 * `wirkung` ist vom Modell verfasster Text und wird als reiner Text gezeichnet.
 */
export interface Vorschlag {
  /** Die Kennung des Werkzeugs, für `ai.actions.tools.<name>`. */
  werkzeug: string
  /** Was das Modell als Folge erwartet. Leer, wenn es nichts gesagt hat. */
  wirkung: string
}

/**
 * Eine während des Sprechens vorhersagend erkannte Werkzeug-Absicht.
 */
export interface IntentErkannt {
  intent: string
  confidence: number
  entities: Record<string, unknown>
  arguments: Record<string, unknown>
  spekulativ?: boolean
  prefetchStatus?: 'erkannt' | 'gestartet' | 'fertig' | 'abgebrochen' | 'fehler'
  revision?: number
}

export type RegionalTab = 'overview' | 'satellite' | 'news' | 'social' | 'traffic' | 'weather'

/** Der Bereich und, falls vorhanden, die Quelle, auf die die Stimme gerade verweist. */
export interface RegionalFocus {
  tab: RegionalTab
  sourceId?: string
  sceneId?: string
}

interface Ergebnis {
  zustand: Sprachzustand
  /** Der laufende Wortwechsel, für die Anzeige. */
  zeilen: Sprachzeile[]
  /** Welches Werkzeug gerade arbeitet — nur der Name, nie die Argumente. */
  werkzeug: string | null
  /** Was schiefging, als Übersetzungsschlüssel. `null`, wenn nichts. */
  fehler: string | null
  fehlerWerkzeug: string | null
  fehlerCode: string | null
  debugCode: string | null
  debugHint: string | null
  /**
   * Die gezeigten Stellen, die zuletzt gezeigte am Ende. Die Anzeige braucht
   * nur die letzte; die davor bleiben ein paar Schritte stehen, damit ein
   * Neuzeichnen mitten im Wechsel nicht ins Leere greift.
   */
  belege: Beleg[]
  /** Die Schreibaktion, auf die gerade ein Ja fehlt. `null`, wenn keine. */
  vorschlag: Vorschlag | null
  /** Spekulativ vorab erkannte Absicht des Nutzers. */
  intentErkannt: IntentErkannt | null
  /** Regionale Satelliten- und Geodaten, falls ein entsprechendes Werkzeug lief. */
  geoData: AiRegionalAnalysis | null
  /** Der von der KI gerade erklärte Bereich der Regionalansicht. */
  regionalFocus: RegionalFocus | null
  /** Ob der aktuelle Gesprächskontext noch eine Regionalansicht benötigt. */
  regionalContextActive: boolean
  setGeoData: React.Dispatch<React.SetStateAction<AiRegionalAnalysis | null>>
  /**
   * Der aktuelle Lautstärkepegel zwischen 0 und 1 — wer gerade redet, egal wer.
   *
   * Eine **Funktion** und kein Zustandswert. Die Blase liest ihn sechzigmal je
   * Sekunde; als `useState` wäre das sechzig Renderdurchläufe je Sekunde für
   * eine Zahl, die kein React-Element je anzeigt.
   */
  pegel: () => number
  /**
   * Async, weil vor dem Handshake das Bearer-Token der Desktop-App
   * aufgefrischt wird — ein WebSocket kennt keinen 401-Retry. Die Auflösung
   * heißt „Verbindungsaufbau angestoßen", nicht „verbunden".
   */
  starten: () => Promise<void>
  beenden: () => void
}

/** Wieviele Zeilen die Anzeige behält. Es ist ein Gespräch, kein Protokoll. */
const MAX_ZEILEN = 40

/**
 * Wieviele gezeigte Stellen aufgehoben werden. Gezeigt wird ohnehin nur die
 * letzte; mehr als eine Handvoll wäre ein Protokoll, das niemand liest, und
 * Logzeilen sind das Größte, was hier je über die Leitung kommt.
 */
const MAX_BELEGE = 5

function parseRegionalFocus(value: unknown): RegionalFocus | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const tab = raw.tab
  if (tab !== 'overview' && tab !== 'satellite' && tab !== 'news' && tab !== 'social' && tab !== 'traffic' && tab !== 'weather') return null
  const sourceId = typeof raw.source_id === 'string' && raw.source_id.length <= 512 ? raw.source_id : undefined
  const sceneId = typeof raw.scene_id === 'string' && raw.scene_id.length <= 128 ? raw.scene_id : undefined
  return { tab, sourceId, sceneId }
}

function webBelege(roh: unknown): Beleg[] {
  if (!Array.isArray(roh)) return []
  return roh.flatMap((eintrag): Beleg[] => {
    if (!eintrag || typeof eintrag !== 'object') return []
    const daten = eintrag as Record<string, unknown>
    const titel = typeof daten.title === 'string' ? daten.title.trim().slice(0, 240) : ''
    const url = typeof daten.url === 'string' ? daten.url.trim().slice(0, 2_000) : ''
    const beschreibung = typeof daten.description === 'string'
      ? daten.description.trim().slice(0, 1_000)
      : typeof daten.snippet === 'string'
        ? daten.snippet.trim().slice(0, 1_000)
        : ''
    const zeilen = [url, beschreibung].filter(Boolean)
    if (!titel && zeilen.length === 0) return []
    return [{ quelle: titel || url, zeilen }]
  }).slice(0, MAX_BELEGE)
}

/**
 * Zustände, deren Eintreffen beweist, dass die Leitung wieder trägt.
 *
 * Anlass: die Überschrift blieb dauerhaft auf „Sprachverbindung verloren"
 * stehen, obwohl Ton und Verbindung längst weiterliefen — ein einziger
 * unkritischer Anbieterfehler (siehe die Abbruchbedingung weiter unten) setzte
 * `fehler`, und nichts nahm ihn je zurück. Wer hört, dass es weitergeht, darf oben nicht
 * das Gegenteil lesen. `denkt` steht bewusst nicht dabei: dort schweigt die
 * Gegenstelle, und ein Fehler, der genau dann kam, ist noch keiner von gestern.
 *
 * `bereit` fehlt aus dem entgegengesetzten Grund: das Backend sendet in jedem
 * Fehlerpfad erst die Störung und unmittelbar danach `zustand=bereit` — stünde
 * `bereit` hier, löschte jede Störung sich selbst, bevor ein Mensch sie lesen
 * kann. Erst echtes Weiterleben (hören oder sprechen) beweist etwas.
 */
const LEITUNG_TRAEGT: ReadonlySet<string> = new Set(['hoert', 'spricht'])

/**
 * Warum kam der Handshake nicht durch? Ein Browser-WebSocket verrät es nicht
 * (kein Statuscode, kein Grund) — aber der Config-Endpunkt desselben Backends
 * ist per HTTP erreichbar und trägt seit dem App-Sprachmodus den Marker
 * `bearer_ws`. Fehlt er, ist das Panel schlicht zu alt für die App, und genau
 * das soll dastehen — nicht „Verbindung verloren", mit dem niemand etwas
 * anfangen kann. Nur die Desktop-App fragt (im Panel trägt der Cookie, dort
 * ist ein alter Server kein Handshake-Problem).
 */
async function handshakeErklaeren(): Promise<string> {
  try {
    const konfig = await aiApi.getVoiceConfig()
    return konfig.bearer_ws === true
      ? 'ai.voice.errors.connection'
      : 'ai.voice.errors.veraltet'
  } catch {
    // Nicht einmal HTTP kommt durch — dann ist es wirklich die Verbindung.
    return 'ai.voice.errors.connection'
  }
}

function adresse(providerId?: number | null): string {
  // `wsUrl` statt eigener Ableitung: es kennt als Einziges auch den
  // Laufzeit-Override der Desktop-App (`config/api.ts`) — die eigene Lesart
  // von VITE_API_URL hier hätte in der App stumm auf `tauri.localhost` gezeigt.
  const base = wsUrl('/api/ai/voice/ws')
  return providerId ? `${base}?provider_id=${providerId}` : base
}

export function useSprachsitzung(
  providerId?: number | null,
  modus: 'legacy' | 'openai_realtime' = 'legacy',
): Ergebnis {
  const [zustand, setZustand] = useState<Sprachzustand>('aus')
  const [zeilen, setZeilen] = useState<Sprachzeile[]>([])
  const [werkzeug, setWerkzeug] = useState<string | null>(null)
  const [fehlerWerkzeug, setFehlerWerkzeug] = useState<string | null>(null)
  const [fehlerCode, setFehlerCode] = useState<string | null>(null)
  const [debugCode, setDebugCode] = useState<string | null>(null)
  const [debugHint, setDebugHint] = useState<string | null>(null)
  const [fehler, setFehler] = useState<string | null>(null)
  const [belege, setBelege] = useState<Beleg[]>([])
  const [vorschlag, setVorschlag] = useState<Vorschlag | null>(null)
  const [intentErkannt, setIntentErkannt] = useState<IntentErkannt | null>(null)
  const [geoData, setGeoData] = useState<AiRegionalAnalysis | null>(null)
  const [regionalFocus, setRegionalFocus] = useState<RegionalFocus | null>(null)
  const [regionalContextActive, setRegionalContextActive] = useState(true)
  const intentRevision = useRef(0)

  const ws = useRef<WebSocket | null>(null)
  const mikro = useRef<Aufnahme | null>(null)
  const lautsprecher = useRef<Wiedergabe | null>(null)
  const rtc = useRef<RTCPeerConnection | null>(null)
  const rtcMikro = useRef<MediaStream | null>(null)
  const rtcAudio = useRef<HTMLAudioElement | null>(null)
  const rtcKontext = useRef<AudioContext | null>(null)
  const rtcMesser = useRef<AnalyserNode | null>(null)
  const rtcProbe = useRef<Uint8Array<ArrayBuffer> | null>(null)
  /** Ob der letzte Abbruch planmäßig war — dann wird neu verbunden. */
  const planmaessig = useRef(false)
  /** Verhindert, dass ein Neustart eine bereits beendete Sitzung wiederbelebt. */
  const gewollt = useRef(false)

  const aufraeumen = useCallback(() => {
    mikro.current?.beenden()
    mikro.current = null
    lautsprecher.current?.schliessen()
    lautsprecher.current = null
    rtc.current?.close()
    rtc.current = null
    rtcMikro.current?.getTracks().forEach((spur) => spur.stop())
    rtcMikro.current = null
    rtcAudio.current?.pause()
    rtcAudio.current = null
    void rtcKontext.current?.close().catch(() => undefined)
    rtcKontext.current = null
    rtcMesser.current = null
    rtcProbe.current = null
    const offen = ws.current
    ws.current = null
    if (offen && offen.readyState <= WebSocket.OPEN) offen.close()
  }, [])

  const beenden = useCallback(() => {
    gewollt.current = false
    planmaessig.current = false
    aufraeumen()
    setZustand('aus')
    setWerkzeug(null)
    setVorschlag(null)
    setIntentErkannt(null)
    setRegionalFocus(null)
    setRegionalContextActive(true)
    intentRevision.current = 0
    voiceDebug('VOICE_BEENDET', { zustand: 'aus' })
  }, [aufraeumen])

  const zeileAnhaengen = useCallback((wer: Sprachzeile['wer'], text: string) => {
    setZeilen((bisher) => {
      const letzte = bisher[bisher.length - 1]
      // Die KI schickt ihr Transkript stückweise. Zwei aufeinanderfolgende
      // Stücke desselben Sprechers sind ein Satz und nicht zwei Zeilen.
      if (letzte && letzte.wer === wer && wer === 'ki') {
        const kopie = bisher.slice(0, -1)
        return [...kopie, { wer, text: letzte.text + text }].slice(-MAX_ZEILEN)
      }
      // Wenn der Nutzer weiterspricht (Turn-Merge oder Korrektur), wird die
      // letzte eigene Zeile nahtlos aktualisiert statt dupliziert dargestellt.
      if (letzte && letzte.wer === wer && wer === 'ich') {
        const kopie = bisher.slice(0, -1)
        return [...kopie, { wer, text }].slice(-MAX_ZEILEN)
      }
      return [...bisher, { wer, text }].slice(-MAX_ZEILEN)
    })
  }, [])

  const starten = useCallback(async () => {
    if (ws.current !== null) return
    gewollt.current = true
    setFehler(null)
    setFehlerCode(null)
    setDebugCode(null)
    setDebugHint(null)
    setZustand('verbindet')
    voiceDebug('VOICE_START', { providerId, modus })

    const istRealtime = modus === 'openai_realtime'

    // Audio-Wiedergabe direkt bei der Nutzergeste (Klick) initialisieren,
    // um den AudioContext sofort im Zustand 'running' zu haben.
    if (!istRealtime) {
      try {
        const spiele = new Wiedergabe()
        spiele.bereitMachen()
        lautsprecher.current = spiele
      } catch (e) {
        console.warn('AudioContext-Initialisierung fehlgeschlagen:', e)
      }
    }

    // Im Panel `undefined` (Cookie im Handshake); in der Desktop-App trägt das
    // Subprotokoll das Bearer-Token — der eine Header, den ein Browser-WebSocket
    // erreicht. Der Server spiegelt es in accept() (routers/ai_voice.py).
    let protokolle: string[] | undefined
    try {
      protokolle = await wsProtokolle()
    } catch (e) {
      console.warn('wsProtokolle Fehler:', e)
    }

    // Während des Wartens beendet oder anderweitig verbunden? Kein zweiter
    // Socket — derselbe Grund wie die Sperre am Funktionsanfang.
    if (!gewollt.current || ws.current !== null) return

    let verbindung: WebSocket
    try {
      verbindung = new WebSocket(adresse(providerId), protokolle)
      verbindung.binaryType = 'arraybuffer'
      ws.current = verbindung
     } catch (e) {
      voiceError('VOICE_WS_AUFBAU_FEHLER', { error: String(e) })
      setFehler('ai.voice.errors.connection')
      setFehlerCode('WS_SETUP_FAILED')
      setZustand('aus')
      return
    }

    // Ob der Handshake je durchkam — entscheidet in `onclose`, ob eine
    // Erklärung gesucht wird (nur die App, nur beim Scheitern vor `onopen`).
    let verbunden = false

    const verbindungTimeout = window.setTimeout(() => {
      if (!verbunden && ws.current === verbindung) {
        voiceWarn('VOICE_WS_TIMEOUT', { timeoutMs: 10000 })
        try {
          verbindung.close()
        } catch {}
        setFehler('ai.voice.errors.connection')
        setFehlerCode('WS_TIMEOUT')
        setZustand('aus')
      }
    }, 10_000)

    verbindung.onopen = () => {
      verbunden = true
      window.clearTimeout(verbindungTimeout)
      voiceDebug('VOICE_WS_OPEN', { modus })
      if (istRealtime) {
        void (async () => {
          const verarbeitung = aktuelleVerarbeitung()
          const geraet = await eingabeGeraetId().catch(() => null)
          const strom = await navigator.mediaDevices.getUserMedia({
            audio: {
              channelCount: 1,
              echoCancellation: verarbeitung.echo,
              noiseSuppression: verarbeitung.rauschen,
              autoGainControl: verarbeitung.autogain,
              ...(geraet ? { deviceId: { ideal: geraet } } : {}),
            },
          })
          if (!gewollt.current || ws.current !== verbindung) {
            strom.getTracks().forEach((spur) => spur.stop())
            return
          }
          rtcMikro.current = strom
          const peer = new RTCPeerConnection()
          rtc.current = peer
          const kontext = new AudioContext()
          const quelle = kontext.createMediaStreamSource(strom)
          const gain = kontext.createGain()
          gain.gain.value = verarbeitung.verstaerkung
          const messer = kontext.createAnalyser()
          messer.fftSize = 256
          const ziel = kontext.createMediaStreamDestination()
          quelle.connect(gain)
          gain.connect(messer)
          messer.connect(ziel)
          rtcKontext.current = kontext
          rtcMesser.current = messer
          rtcProbe.current = new Uint8Array(messer.fftSize)
          ziel.stream.getTracks().forEach((spur) => peer.addTrack(spur, ziel.stream))
          peer.createDataChannel('oai-events')
          peer.ontrack = (event) => {
            const audio = new Audio()
            audio.autoplay = true
            audio.srcObject = event.streams[0] ?? new MediaStream([event.track])
            rtcAudio.current = audio
            void ausgabeGeraetId().then((sink) => {
              const mitSink = audio as HTMLAudioElement & { setSinkId?: (id: string) => Promise<void> }
              if (sink && mitSink.setSinkId) return mitSink.setSinkId(sink)
            }).then(() => audio.play()).catch(() => setFehler('ai.voice.errors.audio'))
          }
          const offer = await peer.createOffer()
          await peer.setLocalDescription(offer)
          // OpenAIs WebRTC-Endpunkt übernimmt die ICE-Aushandlung. Auf ein
          // lokales `complete` zu warten fügte bei manchen Browsern bis zu drei
          // Sekunden hinzu, ohne den Vertrag des Endpunkts zu verbessern.
          if (verbindung.readyState === WebSocket.OPEN && peer.localDescription?.sdp) {
            verbindung.send(JSON.stringify({ art: 'webrtc_offer', sdp: peer.localDescription.sdp }))
          }
        })().catch((fehler: unknown) => {
          const name = (fehler as { name?: string })?.name ?? ''
          voiceError('VOICE_RTC_FEHLER', { name, error: String(fehler) })
          setFehler(name === 'NotAllowedError' ? 'ai.voice.errors.microphone' : 'ai.voice.errors.audio')
          setFehlerCode(name || 'RTC_FAILED')
          beenden()
        })
        return
      }
      lautsprecher.current?.bereitMachen()
      void starteAufnahme((paket) => {
        // Nur senden, wenn die Leitung wirklich offen ist. Ein Paket auf einen
        // schliessenden Socket wirft, und das mitten im Sprechen.
        if (verbindung.readyState === WebSocket.OPEN) verbindung.send(paket)
      })
        .then((laufend) => {
          // `gewollt` allein genügt nicht: reißt die Verbindung ab, während
          // getUserMedia noch auf die Freigabe wartet, bleibt es wahr — das
          // Mikrofon liefe dann bei Zustand „aus" weiter, und der nächste
          // starten() überschriebe den Stream kommentarlos. Nur die Verbindung,
          // für die aufgenommen wurde, darf den Stream übernehmen.
          if (!gewollt.current || ws.current !== verbindung) {
            laufend.beenden()
            return
          }
          mikro.current = laufend
        })
        .catch((fehler: unknown) => {
          // Der Grund kommt aus der Aufnahme und wird hier nicht geraten. Ein
          // pauschales „kein Zugriff auf das Mikrofon" schickte den Menschen in
          // die Browsereinstellungen, wo nichts zu finden war — die Freigabe
          // war erteilt, die Audiokette dahinter gescheitert.
          const grund = fehler instanceof AufnahmeAbbruch ? fehler.grund : 'audio'
          setFehler(
            grund === 'verweigert'
              ? 'ai.voice.errors.microphone'
              : 'ai.voice.errors.audio',
          )
          // Der Wortlaut bleibt in der Konsole. Ohne ihn ist ein Browserfehler
          // in dieser Kette nicht auffindbar — er sieht von aussen aus wie ein
          // Mikrofon, das nicht will.
          voiceError('VOICE_AUFNAHME_FEHLER', { error: String(fehler) })
          beenden()
        })
    }

    verbindung.onmessage = (ereignis) => {
      if (ereignis.data instanceof ArrayBuffer) {
        lautsprecher.current?.spiele(ereignis.data)
        return
      }
      if (typeof Blob !== 'undefined' && ereignis.data instanceof Blob) {
        void ereignis.data.arrayBuffer().then((buffer) => {
          lautsprecher.current?.spiele(buffer)
        })
        return
      }
      let nachricht: Record<string, unknown>
      try {
        nachricht = JSON.parse(String(ereignis.data))
      } catch {
        return
      }
      switch (nachricht.art) {
        case 'webrtc_answer':
          if (istRealtime && rtc.current && typeof nachricht.sdp === 'string') {
            void rtc.current.setRemoteDescription({ type: 'answer', sdp: nachricht.sdp })
              .catch(() => setFehler('ai.voice.errors.provider'))
          }
          break
        case 'bereit':
          voiceDebug('VOICE_BEREIT')
          setFehler(null)
          setZustand('bereit')
          break
        case 'debug': {
          const code = typeof nachricht.code === 'string' ? nachricht.code : null
          const hint = typeof nachricht.hint === 'string' ? nachricht.hint : null
          if (code) {
            setDebugCode(code)
            setDebugHint(hint)
            voiceDebug(code, { hint: hint ?? undefined })
            if (code === 'REALTIME_TOOL_TIMEOUT' || code === 'REALTIME_TOOL_FAILED') {
              setFehlerCode(code)
              setFehlerWerkzeug(typeof nachricht.hint === 'string' ? nachricht.hint : null)
            }
          }
          break
        }
        case 'zustand': {
          const neu = String(nachricht.zustand)
          if (neu === 'hoert' && lautsprecher.current?.spricht) {
            // Der Mensch hat angefangen zu reden, **während** die KI redet —
            // was sie gerade sagt, ist die Antwort auf die vorige Frage und
            // damit falsch.
            //
            // Die Bedingung stand hier nicht: unterbrochen wurde bedingungslos,
            // bei jedem `hoert`. Nur redet ein Mensch meistens dann, wenn die
            // KI schweigt — es gab also nichts abzubrechen. Die damalige
            // Gegenstelle (OpenAIs Realtime-API, seit dem 16.08.2026 nicht mehr
            // im Haus) beantwortete den Abbruch ins Leere mit einem Fehler, und
            // der Sprechende las bei jedem Satz „Der Sprachanbieter hat die
            // Sitzung abgebrochen" — doppelt tückisch, weil `hoert` unmittelbar
            // davor die Fehleranzeige zurücksetzt.
            //
            // Die Bedingung bleibt, obwohl die Brücke `unterbrechen` heute
            // klaglos schluckt (`ai_voice_bridge._abwuergen`). Ein Abbruch ins
            // Leere ist auch ohne Fehlermeldung falsch: er meldet dem Backend
            // ein Dazwischenreden, das nicht stattgefunden hat.
            lautsprecher.current.abbrechen()
            if (verbindung.readyState === WebSocket.OPEN) {
              verbindung.send(JSON.stringify({ art: 'unterbrechen' }))
            }
          }
          if (neu === 'bereit' && !geoData) setWerkzeug(null)
          // Nur ein Zustand, in dem wirklich gesprochen oder gehört wird,
          // beweist, dass die Leitung trägt — ein `bereit` folgt auch auf jede
          // Störung und darf sie deshalb nicht wegräumen (siehe
          // LEITUNG_TRAEGT). Eine Fehlermeldung neben laufendem Ton dagegen
          // widerspricht dem, was der Mensch gerade hört.
          if (LEITUNG_TRAEGT.has(neu)) setFehler(null)
          setZustand(neu as Sprachzustand)
          break
        }
        case 'gehoert':
          // Wer spricht, hat entschieden — ja, nein oder etwas ganz anderes.
          // Die Brücke räumt ihre offenen Vorschläge auf jedem dieser drei
          // Wege weg (`_entscheidung`), also verschwindet die Karte hier
          // genauso bedingungslos. Sie stehen zu lassen, bis eine Antwort
          // eintrifft, hiesse: sie steht noch da, während die Löschung läuft.
          setVorschlag(null)
          zeileAnhaengen('ich', String(nachricht.text ?? ''))
          break
        case 'antworttext':
          {
            const text = String(nachricht.text ?? '')
            zeileAnhaengen('ki', text)
          }
          break
        case 'region_ui': {
          if (nachricht.leave === true) {
            setRegionalContextActive(false)
            setRegionalFocus(null)
            break
          }
          const focus = parseRegionalFocus(nachricht.focus)
          if (focus) {
            setRegionalContextActive(true)
            setRegionalFocus(focus)
          }
          break
        }
        case 'intent_erkannt': {
          const intent = String(nachricht.intent ?? '')
          const confidence = Number(nachricht.confidence ?? 1.0)
          if (!/^[a-z0-9_]{1,64}$/.test(intent) || !Number.isFinite(confidence)) break
          const revision = Number(nachricht.revision ?? 0)
          if (Number.isFinite(revision) && revision > 0 && revision < intentRevision.current) break
          if (Number.isFinite(revision) && revision > 0) intentRevision.current = revision
          const entities =
            nachricht.entities && typeof nachricht.entities === 'object'
              ? (nachricht.entities as Record<string, unknown>)
              : {}
          const args =
            nachricht.arguments && typeof nachricht.arguments === 'object'
              ? (nachricht.arguments as Record<string, unknown>)
              : {}
          setIntentErkannt({
            intent,
            confidence,
            entities,
            arguments: args,
            spekulativ: Boolean(nachricht.spekulativ ?? true),
            prefetchStatus:
              nachricht.prefetch_status === 'erkannt' ||
              nachricht.prefetch_status === 'gestartet' ||
              nachricht.prefetch_status === 'fertig' ||
              nachricht.prefetch_status === 'abgebrochen' ||
              nachricht.prefetch_status === 'fehler'
                ? nachricht.prefetch_status
                : undefined,
            revision: Number.isFinite(revision) && revision > 0 ? revision : undefined,
          })
          if (intent) {
            setWerkzeug(intent)
          }
          const analysis = normalizeRegionalAnalysis(nachricht.geo_analysis)
          if (analysis) {
            setGeoData(analysis)
          } else if (
            nachricht.geo_target &&
            typeof nachricht.geo_target === 'object' &&
            typeof (nachricht.geo_target as Record<string, unknown>).latitude === 'number' &&
            typeof (nachricht.geo_target as Record<string, unknown>).longitude === 'number'
          ) {
            const target = nachricht.geo_target as Record<string, unknown>
            const targetAnalysis = normalizeRegionalAnalysis({
              location: String(target.location ?? entities.location ?? ''),
              coordinates: {
                latitude: target.latitude,
                longitude: target.longitude,
                bbox: target.bbox,
              },
            })
            if (targetAnalysis) setGeoData(targetAnalysis)
          }
          break
        }
        case 'werkzeug_gestartet':
        case 'tool_start': {
          const name = String(nachricht.name || nachricht.tool_name || '')
          if (name) {
            voiceDebug('VOICE_TOOL_START', { name })
            setWerkzeug(name)
            const analysis = normalizeRegionalAnalysis(nachricht.geo_analysis)
            if (analysis) {
              setGeoData(analysis)
              setRegionalContextActive(true)
            }
            if (nachricht.geo_camera) {
              setGeoData((current) => applyGeoCameraCommand(current, nachricht.geo_camera))
            }
          }
          break
        }
        case 'werkzeug':
        case 'tool': {
          const name = String(nachricht.name || nachricht.tool_name || '')
          if (name) {
            setWerkzeug(name)
          }
          if (nachricht.failed) {
            setFehlerWerkzeug(name || null)
            setFehlerCode(typeof nachricht.code === 'string' ? nachricht.code : 'TOOL_FAILED')
            voiceWarn('VOICE_TOOL_FAILED', { name, code: nachricht.code })
          } else {
            voiceDebug('VOICE_TOOL_OK', { name })
          }
          const analysis = normalizeRegionalAnalysis(nachricht.geo_analysis)
          if (analysis) {
            setGeoData(analysis)
            setRegionalContextActive(true)
          }
          if (nachricht.geo_camera) {
            setGeoData((current) => applyGeoCameraCommand(current, nachricht.geo_camera))
          }
          const quellen = webBelege(nachricht.web_results)
          if (quellen.length > 0) {
            setBelege((bisher) => [...bisher, ...quellen].slice(-MAX_BELEGE))
          }
          break
        }
        case 'beleg': {
          // Der Rahmen kommt aus unserem Backend, sein Inhalt aber aus einem
          // Werkzeugergebnis. Deshalb wird hier nichts geglaubt, was nicht
          // dasteht: keine Liste, keine Anzeige — und `String` auf jede Zeile,
          // damit eine Zahl im Array später nicht als `undefined` erscheint.
          const roh = nachricht.zeilen
          const gezeigt = Array.isArray(roh) ? roh.map((zeile) => String(zeile)) : []
          if (gezeigt.length === 0) break
          setBelege((bisher) =>
            [...bisher, { quelle: String(nachricht.quelle ?? ''), zeilen: gezeigt }]
              .slice(-MAX_BELEGE),
          )
          break
        }
        case 'vorschlag': {
          // Fremdinhalt in einem eigenen Rahmen: `tool_name` ist eine Kennung
          // aus der Werkzeugliste des Backends und wird gleich als
          // Übersetzungsschlüssel benutzt — deshalb wird nur übernommen, was
          // wie eine solche Kennung aussieht. Ohne diese Prüfung liesse ein
          // Feld voller Punkte den Menschen in `de.json` spazieren gehen.
          const daten = nachricht.vorschlag
          if (daten === null) {
            setVorschlag(null)
            break
          }
          if (typeof daten !== 'object') break
          const roh = daten as Record<string, unknown>
          const werkzeug = String(roh.tool_name ?? '')
          if (!/^[a-z0-9_]{1,64}$/.test(werkzeug)) break
          setVorschlag({
            werkzeug,
            wirkung: String(roh.expected_effect ?? '').slice(0, 400),
          })
          break
        }
        case 'abgelaufen':
          // Planmäßiges Ende nach der Höchstdauer. Der Server schließt gleich;
          // `onclose` verbindet dann neu.
          planmaessig.current = true
          break
        case 'fehler': {
          const code = typeof nachricht.code === 'string' ? nachricht.code : 'UNKNOWN'
          voiceWarn('VOICE_FEHLER', { code })
          setFehler('ai.voice.errors.provider')
          setFehlerCode(code)
          break
        }
        case 'stoerung': {
          const grund = typeof nachricht.grund === 'string' ? nachricht.grund : 'unknown'
          voiceWarn('VOICE_STOERUNG', { grund })
          setDebugCode(grund)
          setFehler(
            grund === 'realtime_kontingent'
              ? 'ai.voice.errors.realtimeQuota'
              : grund === 'kontingent'
              ? 'ai.voice.errors.quota'
              : grund === 'leere_antwort'
              ? 'ai.voice.errors.provider'
              : grund === 'realtime_response'
              ? 'ai.voice.errors.provider'
              : 'ai.voice.errors.provider',
          )
          setFehlerCode(grund === 'leere_antwort' ? 'REALTIME_LEERE_ANTWORT' : grund === 'realtime_response' ? 'REALTIME_RESPONSE_FAILED' : null)
          break
        }
        default:
          break
      }
    }

    verbindung.onerror = () => {
      voiceError('VOICE_WS_ERROR')
      setFehler('ai.voice.errors.connection')
      setFehlerCode('WS_ERROR')
    }

    verbindung.onclose = () => {
      window.clearTimeout(verbindungTimeout)
      mikro.current?.beenden()
      mikro.current = null
      lautsprecher.current?.schliessen()
      lautsprecher.current = null
      rtc.current?.close()
      rtc.current = null
      rtcMikro.current?.getTracks().forEach((spur) => spur.stop())
      rtcMikro.current = null
      rtcAudio.current?.pause()
      rtcAudio.current = null
      void rtcKontext.current?.close().catch(() => undefined)
      rtcKontext.current = null
      rtcMesser.current = null
      rtcProbe.current = null
      ws.current = null
      setWerkzeug(null)
      // Die offenen Vorschläge der Brücke leben in der Sitzung und nicht in
      // der Datenbank. Nach dem Neuverbinden nimmt kein gesprochenes Ja sie
      // mehr an — eine Karte, die stehen bliebe, versprächt das Gegenteil.
      setVorschlag(null)

      if (planmaessig.current && gewollt.current) {
        voiceDebug('VOICE_ABGELAUFEN_RECONNECT')
        planmaessig.current = false
        setZustand('verbindet')
        window.setTimeout(() => {
          if (gewollt.current) void starten()
        }, 250)
        return
      }
      // Der Handshake selbst ist gescheitert (nie `onopen`): in der App
      // (`protokolle` gesetzt) beim Backend nachfragen, ob es den App-Weg
      // überhaupt kennt — die Antwort ersetzt das nichtssagende
      // „Verbindung verloren" aus `onerror`.
      if (!verbunden && gewollt.current && protokolle) {
        void handshakeErklaeren().then(setFehler)
      }
      setZustand('aus')
    }
  }, [beenden, modus, providerId, zeileAnhaengen])

  // Wer die Seite verlässt, lässt kein offenes Mikrofon zurück.
  useEffect(() => () => {
    gewollt.current = false
    aufraeumen()
  }, [aufraeumen])

  // Den Zustand nach draussen melden — als DOM-Ereignis, nicht als Callback.
  // Die Desktop-App (MSS) hängt daran Tray-Farbe und Audio-Ducking; im Panel
  // hört niemand zu, und das ist in Ordnung. Ein Callback müsste durch
  // SprachAnsicht und pages/Ai durchgereicht werden, nur damit eine Hülle ihn
  // je nach Bau anders füllt.
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('msm:sprachzustand', { detail: { zustand } }))
  }, [zustand])

  // Wer gerade redet, bestimmt die Quelle: beim Zuhören das Mikrofon, sonst
  // die Stimme der KI. Ein Maximum über beide wäre bequemer und falsch — dann
  // atmete die Blase auch dann, wenn nur ein Lüfter neben dem Mikrofon steht.
  const pegel = useCallback(
    () => {
      if (zustand === 'hoert' && rtcMesser.current && rtcProbe.current) {
        rtcMesser.current.getByteTimeDomainData(rtcProbe.current)
        let summe = 0
        for (const sample of rtcProbe.current) {
          const wert = (sample - 128) / 128
          summe += wert * wert
        }
        return Math.min(1, Math.sqrt(summe / rtcProbe.current.length) * 4)
      }
      return (zustand === 'hoert' ? mikro.current?.pegel() : lautsprecher.current?.pegel()) ?? 0
    },
    [zustand],
  )

  return {
    zustand,
    zeilen,
    werkzeug,
    fehlerWerkzeug,
    fehlerCode,
    debugCode,
    debugHint,
    fehler,
    belege,
    vorschlag,
    intentErkannt,
    geoData,
    regionalFocus,
    regionalContextActive,
    setGeoData,
    pegel,
    starten,
    beenden,
  }
}
