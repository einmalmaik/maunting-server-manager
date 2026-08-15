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

import { starteAufnahme, type Aufnahme } from './audioAufnahme'
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

interface Ergebnis {
  zustand: Sprachzustand
  /** Der laufende Wortwechsel, für die Anzeige. */
  zeilen: Sprachzeile[]
  /** Welches Werkzeug gerade arbeitet — nur der Name, nie die Argumente. */
  werkzeug: string | null
  /** Was schiefging, als Übersetzungsschlüssel. `null`, wenn nichts. */
  fehler: string | null
  starten: () => void
  beenden: () => void
}

/** Wieviele Zeilen die Anzeige behält. Es ist ein Gespräch, kein Protokoll. */
const MAX_ZEILEN = 40

function adresse(): string {
  const basis = import.meta.env.VITE_API_URL || window.location.origin
  return `${basis.replace(/^http/, 'ws').replace(/\/$/, '')}/api/ai/voice/ws`
}

export function useSprachsitzung(): Ergebnis {
  const [zustand, setZustand] = useState<Sprachzustand>('aus')
  const [zeilen, setZeilen] = useState<Sprachzeile[]>([])
  const [werkzeug, setWerkzeug] = useState<string | null>(null)
  const [fehler, setFehler] = useState<string | null>(null)

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
        .catch(() => {
          setFehler('ai.voice.errors.microphone')
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
          setZustand('bereit')
          break
        case 'zustand': {
          const neu = String(nachricht.zustand)
          if (neu === 'hoert') {
            // Der Mensch hat angefangen zu reden — was die KI gerade sagt, ist
            // die Antwort auf die vorige Frage und damit falsch.
            lautsprecher.current?.abbrechen()
            if (verbindung.readyState === WebSocket.OPEN) {
              verbindung.send(JSON.stringify({ art: 'unterbrechen' }))
            }
          }
          if (neu === 'bereit') setWerkzeug(null)
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

  return { zustand, zeilen, werkzeug, fehler, starten, beenden }
}
