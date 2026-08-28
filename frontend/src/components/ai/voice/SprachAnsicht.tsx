import { useEffect, useRef, useState } from 'react'
import { FileText, Loader2, Mic, MicOff, Settings, ShieldAlert, Wrench, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { AiVoiceConfig } from '@/api/ai'
import { ActiveProcessesCard } from '../geo/ActiveProcessesCard'
import { RegionalAnalysisLayout } from '../geo/RegionalAnalysisLayout'
import { Sprachblase } from './Sprachblase'
import { useSprachsitzung, type Beleg, type Vorschlag } from './useSprachsitzung'

/**
 * Der Sprachmodus als eigener Modus — der Chat tritt zurück.
 *
 * Unterstützt sowohl die zentrierte Sprachansicht als auch das 3-Spalten-Kommandozentrum,
 * sobald eine regionale Analyse oder Satellitendaten aktiv sind.
 */
export function SprachAnsicht({
  konfiguration,
  aufChat,
  providerId,
}: {
  konfiguration: AiVoiceConfig | null
  aufChat: () => void
  providerId?: number | null
}) {
  const { t } = useTranslation()
  const { zustand, zeilen, werkzeug, fehler, belege, vorschlag, geoData, setGeoData, pegel, starten, beenden } =
    useSprachsitzung(providerId)
  const [einstellungenOffen, setEinstellungenOffen] = useState(false)
  const kasten = useRef<HTMLDivElement>(null)

  // Wer in den Sprachmodus wechselt, will sprechen.
  useEffect(() => {
    void starten()
  }, [starten])

  // ESC beendet das Gespräch.
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

  const [kommandozentraleGeschlossen, setKommandozentraleGeschlossen] = useState(false)
  const lastToolRef = useRef<string | null>(null)
  const kommandozentraleWarAktiv = useRef(false)

  // Wenn ein neues Werkzeug wie analyze_region anläuft, Schließungssperre aufheben
  useEffect(() => {
    if (werkzeug === 'analyze_region' && lastToolRef.current !== 'analyze_region') {
      setKommandozentraleGeschlossen(false)
      kommandozentraleWarAktiv.current = true
    }
    lastToolRef.current = werkzeug
  }, [werkzeug])

  // geoData allein reicht ebenfalls, um die Kommandozentrale zu aktivieren
  useEffect(() => {
    if (geoData) {
      kommandozentraleWarAktiv.current = true
    }
  }, [geoData])

  const laeuft = zustand !== 'aus'
  const hoert = zustand === 'hoert' || zustand === 'bereit'
  const beleg = belege.length > 0 ? belege[belege.length - 1] : null

  const istKommandozentraleAktiv = Boolean(
    !kommandozentraleGeschlossen && (geoData || werkzeug === 'analyze_region' || kommandozentraleWarAktiv.current),
  )

  // 1. DREI-SPALTEN-KOMMANDOZENTRALE BEI AKTIVER REGIONALANALYSE (sofort bei analyze_region oder geoData)
  if (istKommandozentraleAktiv) {
    return (
      <RegionalAnalysisLayout
        active={true}
        data={geoData}
        loading={!geoData || werkzeug === 'analyze_region' || zustand === 'denkt'}
        onClose={() => {
          setGeoData(null)
          setKommandozentraleGeschlossen(true)
          kommandozentraleWarAktiv.current = false
        }}
      >
        <div className="flex h-full w-full flex-col justify-between p-4 overflow-y-auto space-y-4">
          {/* Kopfbereich: Sprachblase und Status */}
          <div className="flex items-center gap-3 border-b border-outline-variant/20 pb-3 shrink-0">
            <div className="relative shrink-0">
              <Sprachblase zustand={zustand} pegel={pegel} />
              {zustand === 'verbindet' && (
                <Loader2
                  className="absolute inset-0 m-auto h-5 w-5 animate-spin text-on-surface-variant/70"
                  aria-hidden="true"
                />
              )}
            </div>
            <div className="min-w-0 flex-1">
              <h3 className="font-headline text-sm font-bold text-on-surface truncate">
                {fehler ? t(fehler) : t(`ai.voice.zustand.${zustand}`)}
              </h3>
              <p className="text-xs text-on-surface-variant truncate">
                {fehler ? t('ai.voice.hint.error') : t(`ai.voice.hint.${zustand}`)}
              </p>
            </div>
          </div>

          {/* Aktive Prozesse Card */}
          <ActiveProcessesCard />

          {/* Gesprächs-Transkript */}
          {zeilen.length > 0 && (
            <div
              ref={kasten}
              className="flex-1 max-h-48 overflow-y-auto rounded-xl border border-outline-variant/20 bg-surface-container-lowest/60 p-3 space-y-2 text-xs"
            >
              {zeilen.slice(-8).map((zeile, idx) => (
                <div
                  key={idx}
                  className={`flex flex-col ${
                    zeile.wer === 'ich' ? 'items-end' : 'items-start'
                  }`}
                >
                  <span className="text-[10px] font-semibold text-on-surface-variant/70 mb-0.5">
                    {zeile.wer === 'ich' ? t('ai.voice.you', 'Du') : 'MSM'}
                  </span>
                  <div
                    className={`rounded-lg px-2.5 py-1.5 leading-relaxed max-w-[88%] ${
                      zeile.wer === 'ich'
                        ? 'bg-primary/15 text-primary border border-primary/25'
                        : 'bg-surface-container-high text-on-surface'
                    }`}
                  >
                    {zeile.text}
                  </div>
                </div>
              ))}
            </div>
          )}

          {vorschlag && <Vorschlagskasten vorschlag={vorschlag} />}
          {beleg && <Belegkasten beleg={beleg} />}

          {/* Steuerungsleiste unten */}
          <div className="flex items-center justify-center gap-3 pt-2 border-t border-outline-variant/20 shrink-0">
            <RunderKnopf
              label={t(laeuft ? 'ai.voice.stop' : 'ai.voice.start')}
              aktiv={laeuft && hoert}
              onClick={laeuft ? beenden : starten}
            >
              {laeuft ? <Mic className="h-4 w-4" /> : <MicOff className="h-4 w-4" />}
            </RunderKnopf>

            <button
              type="button"
              onClick={() => {
                beenden()
                aufChat()
              }}
              className="msm-btn-secondary flex items-center gap-2 rounded-xl px-4 py-2 text-xs font-medium"
            >
              <X className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{t('ai.voice.end')}</span>
            </button>

            <RunderKnopf
              label={t('ai.voice.settings')}
              aktiv={einstellungenOffen}
              onClick={() => setEinstellungenOffen((offen) => !offen)}
            >
              <Settings className="h-4 w-4" />
            </RunderKnopf>
          </div>

          {einstellungenOffen && <Einstellungen konfiguration={konfiguration} />}
        </div>
      </RegionalAnalysisLayout>
    )
  }

  // 2. STANDARD-ZENTRIERTE SPRACHANSICHT
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

      {/* Zustand als Text */}
      <div className="-mt-4 flex flex-col items-center gap-2 text-center">
        <h2 className="font-headline text-headline-md text-on-surface" aria-live="polite">
          {fehler ? t(fehler) : t(`ai.voice.zustand.${zustand}`)}
        </h2>
        <p className="max-w-md text-sm text-on-surface-variant">
          {fehler ? t('ai.voice.hint.error') : t(`ai.voice.hint.${zustand}`)}
        </p>
        {werkzeug && (
          <div className="flex items-center gap-2 rounded-full border border-primary/30 bg-primary/10 px-3.5 py-1 text-xs font-medium text-primary animate-pulse">
            <Wrench className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            <span>{werkzeug}</span>
            <span className="h-1.5 w-1.5 rounded-full bg-primary animate-ping" />
          </div>
        )}
      </div>

      {vorschlag && <Vorschlagskasten vorschlag={vorschlag} />}
      {beleg && <Belegkasten beleg={beleg} />}

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

function Vorschlagskasten({ vorschlag }: { vorschlag: Vorschlag }) {
  const { t } = useTranslation()
  return (
    <section
      className="mt-4 w-full max-w-2xl rounded-xl border border-tertiary/40 bg-tertiary-container/20 px-4 py-3"
      aria-live="polite"
    >
      <div className="flex items-baseline gap-2">
        <ShieldAlert
          className="h-3.5 w-3.5 shrink-0 self-center text-tertiary"
          aria-hidden="true"
        />
        <h3 className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
          {t('ai.voice.vorschlag.heading')}
        </h3>
        <span className="ml-auto min-w-0 truncate text-sm font-medium text-on-surface">
          {t(`ai.actions.tools.${vorschlag.werkzeug}`, vorschlag.werkzeug)}
        </span>
      </div>
      {vorschlag.wirkung && (
        <p className="mt-2 text-sm leading-relaxed text-on-surface-variant">
          {vorschlag.wirkung}
        </p>
      )}
      <p className="mt-2 text-xs text-on-surface-variant/70">
        {t('ai.voice.vorschlag.hint')}
      </p>
    </section>
  )
}

function Belegkasten({ beleg }: { beleg: Beleg }) {
  const { t } = useTranslation()
  return (
    <section
      className="mt-4 w-full max-w-2xl overflow-hidden rounded-xl border border-outline-variant/40 bg-surface-container-low/50"
      aria-live="polite"
    >
      <div className="flex items-baseline gap-2 border-b border-outline-variant/30 px-4 py-2">
        <FileText className="h-3.5 w-3.5 shrink-0 self-center text-on-surface-variant/70" aria-hidden="true" />
        <h3 className="shrink-0 text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
          {t('ai.voice.beleg.heading')}
        </h3>
        <span className="ml-auto min-w-0 truncate font-mono text-xs text-on-surface-variant/70">
          {beleg.quelle}
        </span>
      </div>
      <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words px-4 py-3 font-mono text-xs leading-5 text-on-surface">
        {beleg.zeilen.join('\n')}
      </pre>
      <p className="flex gap-2 border-t border-outline-variant/30 px-4 py-2 text-[11px] leading-4 text-on-surface-variant/70">
        <ShieldAlert className="mt-px h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        {t('ai.voice.beleg.untrusted')}
      </p>
    </section>
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

function Einstellungen({ konfiguration }: { konfiguration: AiVoiceConfig | null }) {
  const { t } = useTranslation()
  const zeilen: [string, string][] = [
    ['ai.voice.info.model', konfiguration?.model ?? '—'],
    ['ai.voice.info.voice', konfiguration?.voice || '—'],
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
