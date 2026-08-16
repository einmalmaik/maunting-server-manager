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
  /**
   * Der aktuelle Lautstärkepegel zwischen 0 und 1 — wer gerade redet, egal wer.
   *
   * Eine **Funktion** und kein Zustandswert. Die Blase liest ihn sechzigmal je
   * Sekunde; als `useState` wäre das sechzig Renderdurchläufe je Sekunde für
   * eine Zahl, die kein React-Element je anzeigt.
   */
  pegel: () => number
  starten: () => void
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
 * unkritischer Anbieterfehler (der Client schickt `response.cancel` beim
 * Dazwischenreden auch dann, wenn gerade keine Antwort läuft) setzte `fehler`,
 * und nichts nahm ihn je zurück. Wer hört, dass es weitergeht, darf oben nicht
 * das Gegenteil lesen. `denkt` steht bewusst nicht dabei: dort schweigt die
 * Gegenstelle, und ein Fehler, der genau dann kam, ist noch keiner von gestern.
 */
const LEITUNG_TRAEGT: ReadonlySet<string> = new Set(['bereit', 'hoert', 'spricht'])

function adresse(): string {
  const basis = import.meta.env.VITE_API_URL || window.location.origin
  return `${basis.replace(/^http/, 'ws').replace(/\/$/, '')}/api/ai/voice/ws`
}

export function useSprachsitzung(): Ergebnis {
  const [zustand, setZustand] = useState<Sprachzustand>('aus')
  const [zeilen, setZeilen] = useState<Sprachzeile[]>([])
  const [werkzeug, setWerkzeug] = useState<string | null>(null)
  const [fehler, setFehler] = useState<string | null>(null)
  const [belege, setBelege] = useState<Beleg[]>([])

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
      return [...bisher, { wer, text }].slice(-MAX_ZEILEN)
    })
  }, [])

  const starten = useCallback(() => {
    if (ws.current !== null) return
    gewollt.current = true
    setFehler(null)
    setZustand('verbindet')

    const verbindung = new WebSocket(adresse())
    verbindung.binaryType = 'arraybuffer'
    ws.current = verbindung

    verbindung.onopen = () => {
      lautsprecher.current = new Wiedergabe()
      void starteAufnahme((paket) => {
        // Nur senden, wenn die Leitung wirklich offen ist. Ein Paket auf einen
        // schliessenden Socket wirft, und das mitten im Sprechen.
        if (verbindung.readyState === WebSocket.OPEN) verbindung.send(paket)
      })
        .then((laufend) => {
          if (!gewollt.current) {
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
            // Die Bedingung ist neu und stand hier nicht: unterbrochen wurde
            // bedingungslos, bei jedem `hoert`. Nur redet ein Mensch meistens
            // dann, wenn die KI schweigt — es gab also nichts abzubrechen, und
            // das `response.cancel` traf keine laufende Antwort. Die
            // Gegenstelle antwortete darauf mit `response_cancel_not_active`,
            // und der Sprechende las währenddessen „Der Sprachanbieter hat die
            // Sitzung abgebrochen". Bei jedem Satz.
            //
            // Der Fehler war doppelt tückisch, weil `hoert` unmittelbar davor
            // die Fehleranzeige zurücksetzt: die Meldung erschien immer genau
            // nach dem Zustand, der sie hätte löschen sollen.
            lautsprecher.current.abbrechen()
            if (verbindung.readyState === WebSocket.OPEN) {
              verbindung.send(JSON.stringify({ art: 'unterbrechen' }))
            }
          }
          if (neu === 'bereit') setWerkzeug(null)
          // Ein regulärer Zustand beweist, dass die Leitung trägt. Eine
          // Fehlermeldung, die daneben stehen bleibt, widerspricht dem, was der
          // Mensch gerade hört.
          if (LEITUNG_TRAEGT.has(neu)) setFehler(null)
          setZustand(neu as Sprachzustand)
          break
        }
        case 'gehoert':
          zeileAnhaengen('ich', String(nachricht.text ?? ''))
          break
        case 'antworttext':
          zeileAnhaengen('ki', String(nachricht.text ?? ''))
          break
        case 'werkzeug':
          setWerkzeug(String(nachricht.name ?? ''))
          break
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
        case 'abgelaufen':
          // Planmäßiges Ende nach der Höchstdauer. Der Server schließt gleich;
          // `onclose` verbindet dann neu.
          planmaessig.current = true
          break
        case 'stoerung':
          setFehler('ai.voice.errors.provider')
          break
        default:
          break
      }
    }

    verbindung.onerror = () => {
      setFehler('ai.voice.errors.connection')
    }

    verbindung.onclose = () => {
      mikro.current?.beenden()
      mikro.current = null
      lautsprecher.current?.schliessen()
      lautsprecher.current = null
      ws.current = null
      setWerkzeug(null)

      if (planmaessig.current && gewollt.current) {
        // Die 15 Minuten sind um. Neu verbinden heißt: erneut anmelden — genau
        // deshalb gibt es die Grenze.
        planmaessig.current = false
        setZustand('verbindet')
        window.setTimeout(() => {
          if (gewollt.current) starten()
        }, 250)
        return
      }
      setZustand('aus')
    }
  }, [beenden, zeileAnhaengen])

  // Wer die Seite verlässt, lässt kein offenes Mikrofon zurück.
  useEffect(() => () => {
    gewollt.current = false
    aufraeumen()
  }, [aufraeumen])

  // Wer gerade redet, bestimmt die Quelle: beim Zuhören das Mikrofon, sonst
  // die Stimme der KI. Ein Maximum über beide wäre bequemer und falsch — dann
  // atmete die Blase auch dann, wenn nur ein Lüfter neben dem Mikrofon steht.
  const pegel = useCallback(
    () => (zustand === 'hoert' ? mikro.current?.pegel() : lautsprecher.current?.pegel()) ?? 0,
    [zustand],
  )

  return { zustand, zeilen, werkzeug, fehler, belege, pegel, starten, beenden }
}
