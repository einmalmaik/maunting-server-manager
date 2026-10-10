/**
 * Unter der Startseite: was das Schild geblockt hat, seit der Browser zählt.
 * Die Zahlen kommen aus Rust (`schild/gesamt.rs`) und bleiben auf dem Gerät;
 * private Tabs zählen nicht mit. Gesparte Daten und Zeit sind Schätzungen
 * (`schaetzung.ts`), und die Seite sagt das samt Quelle.
 *
 * Kommt die Statistik ins Bild, gleiten die Karten nacheinander herein und
 * die Zahlen zählen hoch; bei „Bewegung reduzieren“ stehen sie gleich da.
 */
import { useEffect, useRef, useState } from 'react'
import { Clock, Cookie, Gauge, Globe, Megaphone, Radar, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { nativ, type SchildGesamt } from '../../services/nativ'
import { seiteOeffnen } from '../../services/tabsStore'
import { datenText, geschaetzt, QUELLE, zeitText } from './schaetzung'

const HOCHZAEHLEN_MS = 1100

export function bewegungReduziert(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** Läuft von 0 bis 1, sobald `los` gilt; ohne Bewegung gleich 1. */
function useAnteil(los: boolean): number {
  const [anteil, setAnteil] = useState(0)
  useEffect(() => {
    if (!los) return
    if (bewegungReduziert()) {
      setAnteil(1)
      return
    }
    let bild = 0
    const start = performance.now()
    const schritt = (jetzt: number) => {
      const t = Math.min(1, (jetzt - start) / HOCHZAEHLEN_MS)
      setAnteil(1 - (1 - t) ** 3)
      if (t < 1) bild = requestAnimationFrame(schritt)
    }
    bild = requestAnimationFrame(schritt)
    return () => cancelAnimationFrame(bild)
  }, [los])
  return anteil
}

function Kachel({ symbol: Symbol, name, wert, nr, da }: { symbol: LucideIcon; name: string; wert: string; nr: number; da: boolean }) {
  return (
    <div
      style={{ transitionDelay: da ? `${nr * 90}ms` : '0ms' }}
      className={`flex min-w-0 flex-col gap-1 rounded-2xl bg-surface-container/80 p-5 backdrop-blur transition-[opacity,transform] duration-700 ease-out motion-reduce:transition-none ${
        da ? 'translate-y-0 opacity-100' : 'translate-y-6 opacity-0 motion-reduce:translate-y-0 motion-reduce:opacity-100'
      }`}
    >
      <dt className="flex items-center gap-2 text-label-md text-on-surface-variant">
        <Symbol className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
        <span className="truncate">{name}</span>
      </dt>
      <dd className="truncate text-headline-md tabular-nums text-on-surface">{wert}</dd>
    </div>
  )
}

export function Statistik() {
  const { t, i18n } = useTranslation()
  const flaeche = useRef<HTMLElement>(null)
  const [stand, setStand] = useState<SchildGesamt | null>(null)
  const [da, setDa] = useState(false)
  const anteil = useAnteil(da && stand !== null)
  const sprache = i18n.language

  // Frisch lesen, wann immer die Statistik ins Bild kommt.
  useEffect(() => {
    const lesen = () => void nativ.schildGesamt().then((g) => g && setStand(g)).catch(() => null)
    lesen()
    const el = flaeche.current
    if (!el || typeof IntersectionObserver === 'undefined') {
      setDa(true)
      return
    }
    const beobachter = new IntersectionObserver(
      ([eintrag]) => {
        if (!eintrag?.isIntersecting) return
        setDa(true)
        lesen()
      },
      { threshold: 0.2 },
    )
    beobachter.observe(el)
    return () => beobachter.disconnect()
  }, [])

  const g = stand ?? { werbung: 0, tracker: 0, cookies: 0, seiten: 0, seit: 0 }
  const zahl = (n: number) => Math.round(n * anteil).toLocaleString(sprache)
  const etwa = (wert: string) => t('browser.start.statistik.etwa', { wert })
  const { bytes, sekunden } = geschaetzt(g)
  const datum = g.seit > 0 ? new Intl.DateTimeFormat(sprache, { dateStyle: 'long' }).format(new Date(g.seit * 1000)) : null

  return (
    <section ref={flaeche} id="msb-statistik" aria-labelledby="msb-statistik-titel" className="flex w-full max-w-3xl scroll-mt-6 flex-col gap-5">
      <header className={`transition-opacity duration-700 motion-reduce:transition-none ${da ? 'opacity-100' : 'opacity-0 motion-reduce:opacity-100'}`}>
        <h2 id="msb-statistik-titel" className="text-headline-sm text-on-surface">
          {t('browser.start.statistik.titel')}
        </h2>
        <p className="mt-1 text-body-md text-on-surface-variant">
          {datum ? t('browser.start.statistik.seit', { datum }) : t('browser.start.statistik.nurHier')}
        </p>
      </header>
      <dl className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        <Kachel symbol={Megaphone} name={t('browser.start.statistik.werbung')} wert={zahl(g.werbung)} nr={0} da={da} />
        <Kachel symbol={Radar} name={t('browser.start.statistik.tracker')} wert={zahl(g.tracker)} nr={1} da={da} />
        <Kachel symbol={Cookie} name={t('browser.start.statistik.cookies')} wert={zahl(g.cookies)} nr={2} da={da} />
        <Kachel symbol={Globe} name={t('browser.start.statistik.seiten')} wert={zahl(g.seiten)} nr={3} da={da} />
        <Kachel symbol={Gauge} name={t('browser.start.statistik.daten')} wert={etwa(datenText(bytes * anteil, sprache))} nr={4} da={da} />
        <Kachel symbol={Clock} name={t('browser.start.statistik.zeit')} wert={etwa(zeitText(sekunden * anteil, sprache))} nr={5} da={da} />
      </dl>
      <p className="text-label-md text-on-surface-variant">
        {t('browser.start.statistik.schaetzung')}{' '}
        <button
          type="button"
          onClick={() => seiteOeffnen(QUELLE, true)}
          className="rounded text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        >
          {t('browser.start.statistik.quelle')}
        </button>
      </p>
    </section>
  )
}
