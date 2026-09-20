import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { X, Check } from 'lucide-react'
import {
  calculateProgressRingOffset,
  videoNotizBitraten,
  videoNotizBudgetBytes,
  MAX_VIDEO_NOTE_DURATION_SEC,
} from '@/lib/videoNotiz'
import { getAudioTrackConstraints } from '@/lib/audioSettings'
import { getVideoNoteConstraints, VIDEONOTIZ_KANTE } from '@/lib/videoSettings'
import { toast } from '@/stores/toastStore'

/**
 * Die fertige Aufnahme, noch ohne Anhangdaten.
 *
 * Verschlüsselt und hochgeladen wird erst im Sendepfad: dort steht fest, in
 * welche Mailbox der Anhang gehört, und genau das bindet DIS in die
 * gebundenen Daten. Der Rekorder hat diese Angaben nicht und soll sie auch
 * nicht bekommen — vorher verschlüsselte er hier selbst und warf den Umschlag
 * anschließend weg.
 */
export interface VideoNoteAufnahme {
  blob: Blob
  durationSeconds: number
  width: number
  height: number
  mimeType: string
}

export interface CircularVideoNoteRecorderProps {
  onCancel: () => void
  onComplete: (aufnahme: VideoNoteAufnahme) => void
}

/**
 * Aufnahmeformate in absteigender Güte, erster Treffer gewinnt.
 *
 * H.264 zuerst, weil Android es in Hardware kodiert und VP8 dort in Software
 * läuft: bei 720×720 und 60 Bildern je Sekunde war genau das der Grund, warum
 * die Vorschau während der Aufnahme stockte. Safari kann ohnehin nur MP4
 * aufnehmen. Die Anzeige beim Empfänger ist ein `<video>`-Element und nimmt
 * beides.
 */
const FORMATE = [
  'video/mp4;codecs=h264,mp4a.40.2',
  'video/mp4',
  'video/webm;codecs=vp9,opus',
  'video/webm;codecs=vp8,opus',
  'video/webm',
] as const

export function waehleAufnahmeFormat(): string | undefined {
  if (typeof MediaRecorder === 'undefined' || !MediaRecorder.isTypeSupported) return undefined
  return FORMATE.find((f) => MediaRecorder.isTypeSupported(f))
}

export const CircularVideoNoteRecorder: React.FC<CircularVideoNoteRecorderProps> = ({
  onCancel,
  onComplete,
}) => {
  const { t } = useTranslation()

  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [isRecording, setIsRecording] = useState(true)

  const videoRef = useRef<HTMLVideoElement | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  /** Mitgezählte Stückgrößen — der Wächter gegen einen Anhang, der nicht passt. */
  const bytesRef = useRef(0)

  /** Einmal beenden. Zeitgeber, Wächter und Knopf können sonst alle zuschlagen. */
  const beendet = useRef(false)

  /**
   * Die veränderlichen Dinge liegen in Refs, damit `handleStopAndFinish` eine
   * feste Identität behält.
   *
   * Der Rekorder reicht die Funktion an `ondataavailable` weiter, und dieser
   * Rückruf hält für immer die Fassung, die beim Start galt. Läse sie
   * `elapsedSeconds` oder `onComplete` direkt, bekäme jede so beendete Notiz
   * eine Dauer von einer Sekunde und einen veralteten Empfänger. Genau daran
   * ist das Zeitlimit schon einmal gescheitert.
   */
  const elapsedRef = useRef(0)
  const onCompleteRef = useRef(onComplete)
  const onCancelRef = useRef(onCancel)
  useEffect(() => {
    onCompleteRef.current = onComplete
    onCancelRef.current = onCancel
  })

  /**
   * Beendet die Aufnahme und gibt sie vollständig weiter.
   *
   * Der Blob entsteht in `onstop` und nirgends sonst. Hier stand eine Frist von
   * 200 ms, und die Kamera ging im selben Atemzug aus: `stop()` schreibt den
   * letzten Block aber erst danach, und bei längeren Aufnahmen kam er zu spät.
   * Heraus kam eine Datei ohne Abschluss, die beim Empfänger nicht lief — je
   * länger die Notiz, desto wahrscheinlicher.
   */
  const handleStopAndFinish = useCallback(async () => {
    if (beendet.current) return
    beendet.current = true
    setIsRecording(false)

    const recorder = mediaRecorderRef.current
    const masse = streamRef.current?.getVideoTracks()[0]?.getSettings()

    if (recorder && recorder.state !== 'inactive') {
      await new Promise<void>((fertig) => {
        // Ein Rekorder, der nie meldet, darf die Notiz nicht verschlucken.
        // Nach drei Sekunden geht, was da ist.
        const notbremse = setTimeout(fertig, 3000)
        recorder.onstop = () => {
          clearTimeout(notbremse)
          fertig()
        }
        recorder.stop()
      })
    }

    // Erst jetzt. Eine gestoppte Spur kann den Abschluss nicht mehr liefern.
    streamRef.current?.getTracks().forEach((spur) => spur.stop())

    const mimeType = recorder?.mimeType || 'video/webm'
    onCompleteRef.current({
      blob: new Blob(chunksRef.current, { type: mimeType }),
      durationSeconds: Math.max(1, elapsedRef.current),
      // Die echten Maße der Kamera statt geratener: die Anzeige rechnet damit
      // ihr Seitenverhältnis, und hier standen feste 360, während 480 angefragt
      // wurde.
      width: masse?.width ?? VIDEONOTIZ_KANTE,
      height: masse?.height ?? VIDEONOTIZ_KANTE,
      mimeType,
    })
  }, [])

  // Start camera and recording
  useEffect(() => {
    let active = true

    navigator.mediaDevices.getUserMedia({
      // Dieselben Bedingungen wie Sprachnachricht, Anruf und Mikrofontest.
      // Hier stand `audio: true`, und damit war jede Einstellung aus dem Profil
      // — Gerätewahl, Echounterdrückung, Rauschsperre, Pegelanpassung — für
      // Videonotizen wirkungslos.
      audio: getAudioTrackConstraints(),
      video: getVideoNoteConstraints(),
    }).then((stream) => {
      if (!active) {
        stream.getTracks().forEach((spur) => spur.stop())
        return
      }
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
      }

      const mimeType = waehleAufnahmeFormat()
      const bitraten = videoNotizBitraten()
      const recorder = new MediaRecorder(stream, {
        ...(mimeType ? { mimeType } : {}),
        videoBitsPerSecond: bitraten.video,
        audioBitsPerSecond: bitraten.audio,
      })
      mediaRecorderRef.current = recorder
      chunksRef.current = []
      bytesRef.current = 0

      const budget = videoNotizBudgetBytes()
      recorder.ondataavailable = (e) => {
        if (!e.data || e.data.size === 0) return
        chunksRef.current.push(e.data)
        bytesRef.current += e.data.size
        // Die Bitrate ist ein Mittelwert, und ein bewegtes Bild zieht darüber —
        // gemessen bis auf das Doppelte. Eine kurze Notiz, die ankommt, ist
        // besser als eine lange, die der Server abweist.
        //
        // Wie früh das greift, hängt am Format: WebM liefert alle 250 ms, MP4
        // sammelt und gibt den Großteil erst beim Stoppen heraus. Dort sieht
        // der Wächter also erst spät genug Bytes, und die Prüfung im Sendepfad
        // (`Messenger.tsx`) ist das Netz dahinter.
        if (bytesRef.current >= budget && !beendet.current) {
          toast.info(t('social.videoNote.sizeLimitReached'))
          void handleStopAndFinish()
        }
      }

      recorder.start(250)
    }).catch(() => {
      toast.error(t('social.videoNote.cameraFailed'))
      onCancelRef.current()
    })

    return () => {
      active = false
      streamRef.current?.getTracks().forEach((spur) => spur.stop())
    }
    // Genau einmal beim Öffnen. Die Rückrufe von aussen sind Inline-Funktionen
    // und damit bei jedem Render neu; stünden sie hier, ginge die Kamera
    // mitten in der Aufnahme aus und wieder an.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Duration timer
  useEffect(() => {
    if (!isRecording) return
    const interval = setInterval(() => {
      setElapsedSeconds((prev) => {
        const naechste = Math.min(prev + 1, MAX_VIDEO_NOTE_DURATION_SEC)
        elapsedRef.current = naechste
        return naechste
      })
    }, 1000)
    return () => clearInterval(interval)
  }, [isRecording])

  /**
   * Das Zeitlimit beendet die Aufnahme — hier, nicht im Zustandsaktualisierer.
   *
   * Dort stand es bis 09/2026, und das kostete zweimal: ein Aktualisierer muss
   * rein sein, React ruft ihn in der Entwicklung bewusst doppelt auf, und so
   * ging jede auslaufende Notiz **zweimal** raus (zwei Uploads, zwei Zeilen im
   * Verlauf). Ausserdem sah der Intervall-Rückruf `elapsedSeconds` aus dem
   * Render, in dem er entstand — also 0 —, und `Math.max(1, 0)` schrieb jeder
   * so beendeten Notiz eine Dauer von **1 s** zu, egal wie lang sie war.
   */
  useEffect(() => {
    if (elapsedSeconds >= MAX_VIDEO_NOTE_DURATION_SEC) void handleStopAndFinish()
  }, [elapsedSeconds, handleStopAndFinish])

  const { circumference, strokeDashoffset } = calculateProgressRingOffset(elapsedSeconds)

  return (
    <div
      /*
       * Deckend statt `backdrop-blur-md`: Ein Weichzeichner über dem ganzen
       * Schirm wird bei jedem Bild neu gerechnet, und daneben läuft die
       * Kameravorschau. Auf dem Telefon kostete das genau die Bilder, die der
       * Vorschau fehlten.
       */
      className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/90 select-none animate-fade-in"
    >
      {/* Circular Camera Preview with SVG Progress Ring */}
      <div className="relative w-64 h-64 sm:w-80 sm:h-80 flex items-center justify-center">
        {/* SVG Duration Ring */}
        <svg className="absolute inset-0 w-full h-full -rotate-90 pointer-events-none">
          <circle
            cx="50%"
            cy="50%"
            r="48%"
            fill="none"
            stroke="rgba(255,255,255,0.15)"
            strokeWidth="4"
          />
          <circle
            cx="50%"
            cy="50%"
            r="48%"
            fill="none"
            stroke="#10b981"
            strokeWidth="4"
            strokeDasharray={circumference}
            strokeDashoffset={strokeDashoffset}
            strokeLinecap="round"
            // Nur den einen Wert übergehen lassen. `transition-all` bezieht
            // jede Eigenschaft ein, auch die, die sich nie ändert.
            className="transition-[stroke-dashoffset] duration-1000 ease-linear"
          />
        </svg>

        {/* Circular Video Container */}
        <div className="w-[92%] h-[92%] rounded-full overflow-hidden border-2 border-status-success/50 bg-surface-container-high shadow-2xl relative">
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted
            className="w-full h-full object-cover -scale-x-100"
          />
          <div className="absolute top-4 left-1/2 -translate-x-1/2 bg-black/60 px-2.5 py-0.5 rounded-full text-xs font-mono text-white flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-status-destructive animate-pulse" />
            <span>{elapsedSeconds}s / {MAX_VIDEO_NOTE_DURATION_SEC}s</span>
          </div>
        </div>
      </div>

      {/*
       * Abbrechen und Senden, sonst nichts.
       *
       * Hier hingen Wischgesten am Rahmen darüber: Der Finger auf dem Häkchen
       * galt als Gestenbeginn, die Knopfzeile verschwand darunter, und gesendet
       * wurde erst beim Loslassen. Gebaut war das für ein Drücken-und-Halten,
       * das es nie gab — geöffnet wird der Rekorder per Klick.
       */}
      <div className="mt-8 flex items-center gap-6">
        <button
          type="button"
          onClick={onCancel}
          className="w-12 h-12 rounded-full bg-white/10 hover:bg-white/20 text-white flex items-center justify-center transition-transform hover:scale-105"
          title={t('common.cancel')}
          aria-label={t('common.cancel')}
        >
          <X className="w-6 h-6" />
        </button>
        <button
          type="button"
          onClick={() => void handleStopAndFinish()}
          className="w-14 h-14 rounded-full bg-status-success hover:bg-status-success/90 text-on-surface flex items-center justify-center shadow-lg shadow-status-success/30 transition-transform hover:scale-105"
          title={t('social.videoNote.send')}
          aria-label={t('social.videoNote.send')}
        >
          <Check className="w-7 h-7" />
        </button>
      </div>
    </div>
  )
}
