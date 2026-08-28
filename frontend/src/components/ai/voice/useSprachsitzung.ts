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
import { AufnahmeAbbruch, starteAufnahme, type Aufnahme } from './audioAufnahme'
import { Wiedergabe } from './audioWiedergabe'

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

interface Ergebnis {
  zustand: Sprachzustand
  /** Der laufende Wortwechsel, für die Anzeige. */
  zeilen: Sprachzeile[]
  /** Welches Werkzeug gerade arbeitet — nur der Name, nie die Argumente. */
  werkzeug: string | null
  /** Was schiefging, als Übersetzungsschlüssel. `null`, wenn nichts. */
  fehler: string | null
  /**
   * Die gezeigten Stellen, die zuletzt gezeigte am Ende. Die Anzeige braucht
   * nur die letzte; die davor bleiben ein paar Schritte stehen, damit ein
   * Neuzeichnen mitten im Wechsel nicht ins Leere greift.
   */
  belege: Beleg[]
  /** Die Schreibaktion, auf die gerade ein Ja fehlt. `null`, wenn keine. */
  vorschlag: Vorschlag | null
  /** Regionale Satelliten- und Geodaten, falls ein entsprechendes Werkzeug lief. */
  geoData: AiRegionalAnalysis | null
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

export function useSprachsitzung(providerId?: number | null): Ergebnis {
  const [zustand, setZustand] = useState<Sprachzustand>('aus')
  const [zeilen, setZeilen] = useState<Sprachzeile[]>([])
  const [werkzeug, setWerkzeug] = useState<string | null>(null)
  const [fehler, setFehler] = useState<string | null>(null)
  const [belege, setBelege] = useState<Beleg[]>([])
  const [vorschlag, setVorschlag] = useState<Vorschlag | null>(null)
  const [geoData, setGeoData] = useState<AiRegionalAnalysis | null>(null)

  const ws = useRef<WebSocket | null>(null)
  const mikro = useRef<Aufnahme | null>(null)
  const lautsprecher = useRef<Wiedergabe | null>(null)
  /** Ob der letzte Abbruch planmäßig war — dann wird neu verbunden. */
  const planmaessig = useRef(false)
  /** Verhindert, dass ein Neustart eine bereits beendete Sitzung wiederbelebt. */
  const gewollt = useRef(false)

  const aufraeumen = useCallback(() => {
    mikro.current?.beenden()
    mikro.current = null
    lautsprecher.current?.schliessen()
    lautsprecher.current = null
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
    setZustand('verbindet')

    // Audio-Wiedergabe direkt bei der Nutzergeste (Klick) initialisieren,
    // um den AudioContext sofort im Zustand 'running' zu haben.
    try {
      const spiele = new Wiedergabe()
      spiele.bereitMachen()
      lautsprecher.current = spiele
    } catch (e) {
      console.warn('AudioContext-Initialisierung fehlgeschlagen:', e)
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
      console.error('WebSocket-Aufbau fehlgeschlagen:', e)
      setFehler('ai.voice.errors.connection')
      setZustand('aus')
      return
    }

    // Ob der Handshake je durchkam — entscheidet in `onclose`, ob eine
    // Erklärung gesucht wird (nur die App, nur beim Scheitern vor `onopen`).
    let verbunden = false

    const verbindungTimeout = window.setTimeout(() => {
      if (!verbunden && ws.current === verbindung) {
        try {
          verbindung.close()
        } catch {}
        setFehler('ai.voice.errors.connection')
        setZustand('aus')
      }
    }, 10_000)

    verbindung.onopen = () => {
      verbunden = true
      window.clearTimeout(verbindungTimeout)
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
          console.error('Sprachmodus: Aufnahme nicht moeglich', fehler)
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
        case 'bereit':
          setFehler(null)
          setZustand('bereit')
          break
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
          zeileAnhaengen('ki', String(nachricht.text ?? ''))
          break
        case 'werkzeug_gestartet':
        case 'tool_start': {
          const name = String(nachricht.name || nachricht.tool_name || '')
          if (name) {
            setWerkzeug(name)
            if (nachricht.geo_analysis && typeof nachricht.geo_analysis === 'object') {
              setGeoData(nachricht.geo_analysis as AiRegionalAnalysis)
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
          if (nachricht.geo_analysis && typeof nachricht.geo_analysis === 'object') {
            setGeoData(nachricht.geo_analysis as AiRegionalAnalysis)
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
          if (!daten || typeof daten !== 'object') break
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
        case 'stoerung':
          // `grund` wird nicht als Schluessel durchgereicht, nur der eine
          // bekannte Wert waehlt die eigene Meldung: „warte kurz" ist eine
          // andere Auskunft als „etwas ist kaputt", und ein unbekannter Grund
          // soll nicht in `de.json` spazieren gehen.
          setFehler(
            nachricht.grund === 'kontingent'
              ? 'ai.voice.errors.quota'
              : 'ai.voice.errors.provider',
          )
          break
        default:
          break
      }
    }

    verbindung.onerror = () => {
      setFehler('ai.voice.errors.connection')
    }

    verbindung.onclose = () => {
      window.clearTimeout(verbindungTimeout)
      mikro.current?.beenden()
      mikro.current = null
      lautsprecher.current?.schliessen()
      lautsprecher.current = null
      ws.current = null
      setWerkzeug(null)
      // Die offenen Vorschläge der Brücke leben in der Sitzung und nicht in
      // der Datenbank. Nach dem Neuverbinden nimmt kein gesprochenes Ja sie
      // mehr an — eine Karte, die stehen bliebe, versprächt das Gegenteil.
      setVorschlag(null)

      if (planmaessig.current && gewollt.current) {
        // Die 15 Minuten sind um. Neu verbinden heißt: erneut anmelden — das
        // erledigt `starten` selbst (frisches Token vor dem Handshake); die
        // Sitzungshöchstdauer ist exakt die Token-Laufzeit, ohne Erneuerung
        // käme dieser Reconnect also immer mit einem toten Token an.
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
  }, [beenden, providerId, zeileAnhaengen])

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
    () => (zustand === 'hoert' ? mikro.current?.pegel() : lautsprecher.current?.pegel()) ?? 0,
    [zustand],
  )

  return {
    zustand,
    zeilen,
    werkzeug,
    fehler,
    belege,
    vorschlag,
    geoData,
    setGeoData,
    pegel,
    starten,
    beenden,
  }
}
