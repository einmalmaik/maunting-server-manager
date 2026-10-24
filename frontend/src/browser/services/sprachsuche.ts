/**
 * Sprachsuche (Speech-to-Text) für die Browsersuche.
 *
 * Primär: Lokales On-Device Whisper-Tiny via WebGPU / WebAssembly (Transformers.js).
 * - 0 Byte Audiodaten verlassen das Gerät (keine US-Server, vollständiger Datenschutz).
 * - Hohe Erkennungsqualität auch bei Nebengeräuschen und einfacher Mikrofon-Hardware.
 *
 * Fallback: Native Web Speech API / On-Device Spracherkennung, falls WebAudio
 * oder MediaDevices nicht bereitstehen.
 *
 * Wichtig: Das erkannte Ergebnis wird NIE automatisch abgeschickt, sondern
 * in die Eingabezeile eingesetzt, damit der Nutzer den transkribierten Text
 * in Ruhe prüfen, korrigieren und manuell absenden kann.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from '@/stores/toastStore'
import {
  istLokalesWhisperMoeglich,
  ladeWhisperModell,
  starteAudioAufnahme,
  transkribiereAudioMitWhisper,
  type AudioAufnahmeHandle,
} from './lokalesWhisper'
import { istTauri, nativ } from './nativ'
import { istAndroid } from './plattform'

export type SprachsucheZustand = 'bereit' | 'laedt_modell' | 'zuhoeren' | 'transkribiert'

export interface SprachsucheOptionen {
  onErgebnis: (text: string) => void
  onZwischenergebnis?: (text: string) => void
  sprache?: string
}

/**
 * Kurztext eines Transkriptionsfehlers für die Anzeige: Name plus gekürzte
 * Meldung in einer Zeile. Die Startseite hat keine Konsole, deshalb muss der
 * Toast die Ursache nennen — ein generisches „fehlgeschlagen“ lässt den
 * Fehler (etwa blockierter Modell-Download) nicht eingrenzen.
 */
export function fehlerKurztext(fehler: unknown, hoechstens = 160): string {
  const name = fehler instanceof Error && fehler.name ? fehler.name : 'Fehler'
  const meldung = (fehler instanceof Error ? fehler.message : String(fehler ?? ''))
    .replace(/\s+/g, ' ')
    .trim()
  const text = meldung ? `${name}: ${meldung}` : name
  return text.length > hoechstens ? `${text.slice(0, hoechstens - 1)}…` : text
}

interface IRecognitionResultItem {
  transcript: string
}

interface IRecognitionResult {
  isFinal: boolean
  [index: number]: IRecognitionResultItem
}

interface IRecognitionEvent {
  results: {
    length: number
    [index: number]: IRecognitionResult
  }
}

interface IRecognitionErrorEvent {
  error: string
}

interface ISpeechRecognition {
  continuous: boolean
  interimResults: boolean
  lang: string
  start: () => void
  stop: () => void
  abort: () => void
  onstart: (() => void) | null
  onend: (() => void) | null
  onerror: ((event: IRecognitionErrorEvent) => void) | null
  onresult: ((event: IRecognitionEvent) => void) | null
}

function getSpeechRecognitionConstructor(): (new () => ISpeechRecognition) | null {
  if (typeof window === 'undefined') return null
  const w = window as unknown as {
    SpeechRecognition?: new () => ISpeechRecognition
    webkitSpeechRecognition?: new () => ISpeechRecognition
  }
  return w.SpeechRecognition || w.webkitSpeechRecognition || null
}

export function istSprachsucheMoeglich(): boolean {
  // Android-WebView hat kein Web Speech; die System-Spracherkennung kommt aus
  // der App selbst und ist dort immer der richtige Weg.
  if (istAndroid()) return istTauri()
  return istLokalesWhisperMoeglich() || getSpeechRecognitionConstructor() !== null
}

export function useSprachsuche({ onErgebnis, onZwischenergebnis, sprache }: SprachsucheOptionen) {
  const { t, i18n } = useTranslation()
  const [zustand, setZustand] = useState<SprachsucheZustand>('bereit')
  const [unterstuetzt] = useState(() => istSprachsucheMoeglich())
  const aufnahmeHandleRef = useRef<AudioAufnahmeHandle | null>(null)
  const recognizerRef = useRef<ISpeechRecognition | null>(null)
  const aktivRef = useRef(false)
  /** Zählt native Aufrufe; ein spätes Ergebnis nach Abbruch/Unmount wird verworfen. */
  const nativeLaufRef = useRef(0)

  const stoppen = useCallback(async () => {
    // Android: die System-Activity lässt sich von hier nicht abbrechen. Wir
    // lösen nur den Zustand und verwerfen ihr späteres Ergebnis.
    if (istAndroid()) {
      nativeLaufRef.current++
      aktivRef.current = false
      setZustand('bereit')
      return
    }

    // 1. Lokales Whisper beenden und auswerten
    if (aufnahmeHandleRef.current) {
      const handle = aufnahmeHandleRef.current
      aufnahmeHandleRef.current = null
      aktivRef.current = false
      setZustand('transkribiert')

      try {
        const audio = await handle.stoppen()
        if (audio.length > 0) {
          const transkription = await transkribiereAudioMitWhisper(
            audio,
            sprache || i18n.language
          )
          if (transkription) {
            onErgebnis(transkription)
          }
        }
      } catch (err) {
        console.warn('[Sprachsuche] Whisper-Transkription fehlgeschlagen:', err)
        // Ohne Netz kann das Modell nicht geladen werden — eigene Ansage statt
        // technischem Fetch-Fehler. Sonst steht die Ursache im Toast, weil die
        // Startseite keine Konsole zum Nachsehen hat.
        if (typeof navigator !== 'undefined' && navigator.onLine === false) {
          toast.error(t('browser.sprachsuche.offline'))
        } else {
          toast.error(`${t('browser.sprachsuche.fehler')} ${fehlerKurztext(err)}`)
        }
      } finally {
        setZustand('bereit')
      }
      return
    }

    // 2. Web Speech API Fallback stoppen
    if (recognizerRef.current) {
      try {
        recognizerRef.current.stop()
      } catch {
        // Ignorieren falls bereits beendet
      }
    }
    aktivRef.current = false
    setZustand('bereit')
  }, [i18n.language, onErgebnis, sprache, t])

  const starten = useCallback(async () => {
    if (aktivRef.current) return

    // Android: die eingerichtete System-Spracherkennung öffnen, wie beim
    // Widget. Kein Whisper, kein Web-Speech; der Text wird nur eingesetzt.
    if (istAndroid()) {
      const lauf = ++nativeLaufRef.current
      aktivRef.current = true
      setZustand('zuhoeren')
      try {
        const antwort = await nativ.sprachEingabe()
        if (nativeLaufRef.current !== lauf) return
        if (antwort?.text) {
          onErgebnis(antwort.text)
        } else if (antwort?.fehler === 'kein_erkenner') {
          toast.error(t('browser.sprachsuche.keinErkenner'))
        } else if (antwort?.fehler && antwort.fehler !== 'laeuft') {
          toast.error(t('browser.sprachsuche.fehler'))
        }
      } catch (err) {
        if (nativeLaufRef.current === lauf) {
          toast.error(`${t('browser.sprachsuche.fehler')} ${fehlerKurztext(err)}`)
        }
      } finally {
        if (nativeLaufRef.current === lauf) {
          aktivRef.current = false
          setZustand('bereit')
        }
      }
      return
    }

    // 1. Primär: Lokales On-Device Whisper via WebGPU/WASM
    if (istLokalesWhisperMoeglich()) {
      try {
        const handle = await starteAudioAufnahme()
        aufnahmeHandleRef.current = handle
        aktivRef.current = true
        setZustand('zuhoeren')

        // Im Hintergrund das Modell vorwärmen, damit es direkt bereit ist
        void ladeWhisperModell().catch(() => {
          // Fehler wird beim Transkribieren behandelt
        })
        return
      } catch (err: unknown) {
        const fehlerName = err instanceof Error ? err.name : ''
        if (fehlerName === 'NotAllowedError' || fehlerName === 'PermissionDeniedError') {
          toast.error(t('browser.sprachsuche.keineBerechtigung'))
          setZustand('bereit')
          return
        }
        console.warn('[Sprachsuche] Lokale Audioaufnahme fehlgeschlagen, versuche Fallback:', err)
        // Weiterleitung auf Web Speech API Fallback
      }
    }

    // 2. Fallback: Web Speech API
    const Ctor = getSpeechRecognitionConstructor()
    if (!Ctor) {
      toast.info(t('browser.sprachsuche.nichtUnterstuetzt'))
      return
    }

    try {
      const recognizer = new Ctor()
      recognizer.continuous = false
      recognizer.interimResults = true
      recognizer.lang = sprache || i18n.language || 'de-DE'

      recognizer.onstart = () => {
        aktivRef.current = true
        setZustand('zuhoeren')
      }

      recognizer.onresult = (event: IRecognitionEvent) => {
        let finalerText = ''
        let zwischenText = ''

        for (let i = 0; i < event.results.length; i++) {
          const item = event.results[i]
          if (item && item[0]) {
            if (item.isFinal) {
              finalerText += item[0].transcript
            } else {
              zwischenText += item[0].transcript
            }
          }
        }

        if (zwischenText && onZwischenergebnis) {
          onZwischenergebnis(zwischenText)
        }

        if (finalerText) {
          onErgebnis(finalerText.trim())
        }
      }

      recognizer.onerror = (ev: IRecognitionErrorEvent) => {
        if (ev.error === 'no-speech') {
          setZustand('bereit')
          return
        }
        if (ev.error === 'not-allowed' || ev.error === 'service-not-allowed') {
          toast.error(t('browser.sprachsuche.keineBerechtigung'))
        } else {
          toast.error(t('browser.sprachsuche.fehler'))
        }
        setZustand('bereit')
        aktivRef.current = false
      }

      recognizer.onend = () => {
        aktivRef.current = false
        setZustand('bereit')
      }

      recognizerRef.current = recognizer
      recognizer.start()
    } catch {
      toast.error(t('browser.sprachsuche.fehler'))
      setZustand('bereit')
      aktivRef.current = false
    }
  }, [i18n.language, onErgebnis, onZwischenergebnis, sprache, t])

  const umschalten = useCallback(() => {
    if (zustand === 'zuhoeren' || aktivRef.current) {
      void stoppen()
    } else {
      void starten()
    }
  }, [starten, stoppen, zustand])

  useEffect(() => {
    return () => {
      nativeLaufRef.current++
      if (aufnahmeHandleRef.current) {
        aufnahmeHandleRef.current.abbrechen()
        aufnahmeHandleRef.current = null
      }
      if (recognizerRef.current) {
        try {
          recognizerRef.current.abort()
        } catch {
          // Ignorieren
        }
        recognizerRef.current = null
      }
    }
  }, [])

  return {
    zustand,
    unterstuetzt,
    starten,
    stoppen,
    umschalten,
    laeuft: zustand === 'zuhoeren',
    transkribiert: zustand === 'transkribiert',
  }
}
