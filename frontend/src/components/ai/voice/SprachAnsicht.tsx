import { useEffect, useRef, useState } from 'react'
import { Loader2, Mic, MicOff, Settings, Wrench, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { AiVoiceConfig } from '@/api/ai'
import { Sprachblase } from './Sprachblase'
import { useSprachsitzung } from './useSprachsitzung'

/**
 * Der Sprachmodus als eigener Modus — der Chat tritt zurück.
 *
 * **Warum nicht neben dem Chat.** Ein Sprachmodus neben einem Textfeld ist
 * beides halb: man weiss nicht, wohin man schaut, und das Textfeld verspricht
 * eine Eingabe, die gerade niemand benutzt. Wer spricht, schaut auf eine Sache.
 * Hier stand zuerst eine schmale Leiste unter dem Chat; sie war sparsam und
 * falsch.
 *
 * Der Chat ist einen Klick weit weg, nicht gelöscht — und das ist wichtiger als
 * es klingt: eine per Stimme angestossene Änderung erzeugt dieselbe
 * Vorschlagskarte wie immer, und die steht im Chat. Die KI liest deshalb vor,
 * was sie vorhat, und verweist für alles Unumkehrbare ausdrücklich auf die
 * Karte.
 */
export function SprachAnsicht({
  konfiguration,
  aufChat,
}: {
  konfiguration: AiVoiceConfig | null
  aufChat: () => void
}) {
  const { t } = useTranslation()
  const { zustand, zeilen, werkzeug, fehler, pegel, starten, beenden } = useSprachsitzung()
  const [einstellungenOffen, setEinstellungenOffen] = useState(false)
  const kasten = useRef<HTMLDivElement>(null)
  const gestartet = useRef(false)

  // Wer in den Sprachmodus wechselt, will sprechen. Ein zweiter Klick auf
  // „jetzt aber wirklich" wäre eine Tür hinter der Tür.
  useEffect(() => {
    if (gestartet.current) return
    gestartet.current = true
    starten()
  }, [starten])

  // ESC beendet das Gespräch. Es steht auch am Knopf — eine Tastenkombination,
  // die man nicht sieht, ist keine.
  useEffect(() => {
    const taste = (ereignis: KeyboardEvent) => {
      if (ereignis.key !== 'Escape') return
      if (einstellungenOffen) {
        setEinstellungenOffen(false)
        return
      }
      beenden()
      aufChat()
    }
    window.addEventListener('keydown', taste)
    return () => window.removeEventListener('keydown', taste)
  }, [beenden, aufChat, einstellungenOffen])

  useEffect(() => {
    const element = kasten.current
    if (element) element.scrollTop = element.scrollHeight
  }, [zeilen])

  const laeuft = zustand !== 'aus'
  const hoert = zustand === 'hoert' || zustand === 'bereit'

  return (
    <div className="relative flex min-h-0 flex-1 flex-col items-center justify-center overflow-hidden px-6 py-6">
      <div className="relative flex items-center justify-center">
        <Sprachblase zustand={zustand} pegel={pegel} />
        {zustand === 'verbindet' && (
          <Loader2
            className="absolute h-7 w-7 animate-spin text-on-surface-variant/70"
            aria-hidden="true"
          />
        )}
      </div>

      {/* Der Zustand als Text. Die Kugel ist schön, aber sie ist keine
          Auskunft: wer sie nicht sieht — kein Bild, kein Licht, kein Auge
          dafür —, muss hier lesen können, was gerade passiert. */}
      <div className="-mt-4 flex flex-col items-center gap-2 text-center">
        <h2 className="font-headline text-headline-md text-on-surface" aria-live="polite">
          {fehler ? t(fehler) : t(`ai.voice.zustand.${zustand}`)}
        </h2>
        <p className="max-w-md text-sm text-on-surface-variant">
          {fehler ? t('ai.voice.hint.error') : t(`ai.voice.hint.${zustand}`)}
        </p>
        {werkzeug && (
          <p className="flex items-center gap-1.5 text-xs text-on-surface-variant/70">
            <Wrench className="h-3 w-3 shrink-0" aria-hidden="true" />
            {/* Nur der Name. Argumente tragen Serverkennungen und Pfade und
                gehören nicht in eine Anzeige, die nebenbei mitläuft. */}
            {werkzeug}
          </p>
        )}
      </div>

      {zeilen.length > 0 && (
        <div
          ref={kasten}
          className="mt-6 max-h-32 w-full max-w-2xl space-y-1.5 overflow-y-auto rounded-xl border border-outline-variant/30 bg-surface-container-low/30 px-4 py-3"
          aria-live="polite"
        >
          {zeilen.map((zeile, index) => (
            <p
              key={index}
              className={`text-sm leading-relaxed ${
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

      <div className="mt-8 flex items-center gap-4">
        <RunderKnopf
          label={t(laeuft ? 'ai.voice.stop' : 'ai.voice.start')}
          aktiv={laeuft && hoert}
          onClick={laeuft ? beenden : starten}
        >
          {laeuft ? <Mic className="h-5 w-5" /> : <MicOff className="h-5 w-5" />}
        </RunderKnopf>

        <button
          type="button"
          onClick={() => {
            // Erst auflegen, dann umschalten. Andersherum bliebe für einen
            // Wimpernschlag ein offenes Mikrofon hinter einer Ansicht stehen,
            // die es nicht mehr anzeigt.
            beenden()
            aufChat()
          }}
          className="msm-btn-secondary flex flex-col items-center gap-0.5 rounded-xl px-8 py-2.5"
        >
          <span className="flex items-center gap-2 text-sm font-medium">
            <X className="h-4 w-4" aria-hidden="true" />
            {t('ai.voice.end')}
          </span>
          <span className="text-[11px] text-on-surface-variant/70">
            {t('ai.voice.endHint')}
          </span>
        </button>

        <RunderKnopf
          label={t('ai.voice.settings')}
          aktiv={einstellungenOffen}
          onClick={() => setEinstellungenOffen((offen) => !offen)}
        >
          <Settings className="h-5 w-5" />
        </RunderKnopf>
      </div>

      {einstellungenOffen && <Einstellungen konfiguration={konfiguration} />}
    </div>
  )
}

function RunderKnopf({
  label,
  aktiv,
  onClick,
  children,
}: {
  label: string
  aktiv: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-pressed={aktiv}
      className={[
        'flex h-11 w-11 items-center justify-center rounded-full border transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60',
        aktiv
          ? 'border-primary/50 bg-primary/15 text-primary'
          : 'border-outline-variant/60 bg-surface-container-low/50 text-on-surface-variant hover:text-on-surface',
      ].join(' ')}
    >
      {children}
    </button>
  )
}

/**
 * Was hinter dem Zahnrad steht — und was ausdrücklich nicht.
 *
 * Es sind **Angaben**, keine Regler. Am Sprachmodus lässt sich derzeit nichts
 * einstellen: Modell und Schlüssel gehören dem Betreiber, die Stimme ist eine
 * Konstante, und die Höchstdauer hängt an der Lebensdauer des Anmeldetokens.
 * Ein Zahnrad, das drei Schalter zeigt, die nichts tun, wäre schlimmer als
 * keines — hier steht, woran man gerade dran ist, und wo man es ändert.
 */
function Einstellungen({ konfiguration }: { konfiguration: AiVoiceConfig | null }) {
  const { t } = useTranslation()
  const zeilen: [string, string][] = [
    ['ai.voice.info.model', konfiguration?.model ?? '—'],
    ['ai.voice.info.sampleRate', `${(konfiguration?.sample_rate ?? 0) / 1000} kHz`],
    [
      'ai.voice.info.maxSession',
      t('ai.voice.info.minutes', {
        count: Math.round((konfiguration?.max_seconds ?? 0) / 60),
      }),
    ],
  ]
  return (
    <div className="mt-4 w-full max-w-sm rounded-xl border border-outline-variant/40 bg-surface-container-low/60 p-4">
      <dl className="space-y-1.5">
        {zeilen.map(([schluessel, wert]) => (
          <div key={schluessel} className="flex items-baseline justify-between gap-4">
            <dt className="text-xs uppercase tracking-wider text-on-surface-variant/70">
              {t(schluessel)}
            </dt>
            <dd className="truncate font-mono text-xs text-on-surface">{wert}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 border-t border-outline-variant/30 pt-3 text-xs leading-5 text-on-surface-variant">
        {t('ai.voice.info.hint')}
      </p>
    </div>
  )
}
