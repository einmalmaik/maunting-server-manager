import { useEffect, useRef } from 'react'
import { Loader2, Mic, MicOff, Wrench } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { useSprachsitzung, type Sprachzustand } from './useSprachsitzung'

/**
 * Die Sprachleiste unter dem Chat.
 *
 * **Kein zweites Layout.** Der Chat, die Vorschlagskarten und der Kontext-Ring
 * bleiben, wo sie sind — gesprochen wird *daneben*, nicht *stattdessen*. Das
 * ist nicht nur Sparsamkeit: eine Änderung, die per Stimme angestoßen wird,
 * erzeugt dieselbe Karte wie immer, und die muss sichtbar sein, während geredet
 * wird. Ein Vollbild-Sprachmodus hätte sie verdeckt.
 *
 * Der Ring um das Mikrofon ist der ganze Zustandsanzeiger. Nach der Design-DNA:
 * Ruhe statt Alarm, kurze weiche Bewegung, Cyan als vertrauensbildender Akzent,
 * Grün nur, wenn wirklich gehört wird. Kein pulsierendes Rot — das Panel
 * schreit nicht.
 */
export function SprachLeiste() {
  const { t } = useTranslation()
  const { zustand, zeilen, werkzeug, fehler, starten, beenden } = useSprachsitzung()
  const kasten = useRef<HTMLDivElement>(null)

  // Wie im Chat (`AiChat.tsx`): der Kasten wird nach unten gerollt, statt einen
  // Anker anzuspringen. Das ist hier das gewohnte Mittel und braucht kein
  // zusätzliches leeres Element am Ende der Liste.
  useEffect(() => {
    const element = kasten.current
    if (element) element.scrollTop = element.scrollHeight
  }, [zeilen])

  const laeuft = zustand !== 'aus'

  return (
    <div className="shrink-0 border-t border-outline-variant/40">
      <div className="flex items-center gap-3 px-4 py-2.5">
        <button
          type="button"
          onClick={laeuft ? beenden : starten}
          aria-pressed={laeuft}
          aria-label={t(laeuft ? 'ai.voice.stop' : 'ai.voice.start')}
          className={[
            'relative flex h-9 w-9 shrink-0 items-center justify-center rounded-full',
            'transition-colors duration-200 focus-visible:outline-none',
            'focus-visible:ring-2 focus-visible:ring-primary/60',
            laeuft
              ? 'bg-primary/15 text-primary'
              : 'text-on-surface-variant hover:bg-surface-variant/60 hover:text-on-surface',
          ].join(' ')}
        >
          <Ring zustand={zustand} />
          {laeuft ? <Mic className="h-4 w-4" aria-hidden="true" /> : <MicOff className="h-4 w-4" aria-hidden="true" />}
        </button>

        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium text-on-surface-variant">
            {fehler ? t(fehler) : t(`ai.voice.zustand.${zustand}`)}
          </p>
          {werkzeug && (
            <p className="flex items-center gap-1.5 truncate text-xs text-on-surface-variant/70">
              <Wrench className="h-3 w-3 shrink-0" aria-hidden="true" />
              {/* Nur der Name. Argumente tragen Serverkennungen und Pfade und
                  gehören nicht in eine Anzeige, die nebenbei mitläuft. */}
              {werkzeug}
            </p>
          )}
        </div>

        {zustand === 'verbindet' && (
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-on-surface-variant" aria-hidden="true" />
        )}
      </div>

      {laeuft && zeilen.length > 0 && (
        <div
          ref={kasten}
          className="max-h-28 space-y-1 overflow-y-auto border-t border-outline-variant/30 px-4 py-2"
          // Der Wortwechsel läuft mit. Für jemanden, der nicht hört — weil der
          // Ton aus ist oder weil er nicht hören kann —, ist das die Antwort.
          aria-live="polite"
        >
          {zeilen.map((zeile, index) => (
            <p
              key={index}
              className={`text-xs leading-relaxed ${
                zeile.wer === 'ich' ? 'text-on-surface-variant/70' : 'text-on-surface'
              }`}
            >
              <span className="mr-1.5 font-medium text-on-surface-variant/50">
                {t(zeile.wer === 'ich' ? 'ai.voice.you' : 'ai.voice.assistant')}
              </span>
              {zeile.text}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * Der Zustandsring.
 *
 * Vier Zustände, drei Farben, eine Bewegung. Grün heißt „ich höre dich gerade"
 * und ist der einzige Zustand, in dem der Mensch etwas tut; Cyan heißt „ich
 * arbeite"; ohne Ring heißt „bereit". Die Bewegung ist ein weiches Pulsieren
 * und kein Blinken — nach der Design-DNA strahlt das Panel Kontrolle aus, nicht
 * Alarm.
 */
function Ring({ zustand }: { zustand: Sprachzustand }) {
  if (zustand === 'aus' || zustand === 'bereit') return null

  const farbe =
    zustand === 'hoert'
      ? 'ring-status-success/50'
      : zustand === 'spricht'
        ? 'ring-primary/60'
        : 'ring-primary/35'

  return (
    <span
      aria-hidden="true"
      className={[
        'pointer-events-none absolute inset-0 rounded-full ring-2',
        farbe,
        // Nur beim Zuhören und beim Sprechen bewegt sich etwas. „Denkt" ist ein
        // ruhiger Ring: es passiert etwas, aber niemand muss reagieren.
        zustand === 'denkt' ? '' : 'animate-pulse',
      ].join(' ')}
    />
  )
}
