/**
 * Leistung: Kennzahlen der Seite jede Sekunde (`Performance.getMetrics`), mit
 * Verlauf der letzten Minute. Zeiten zeigt sie als Anteil der letzten Sekunde.
 */
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { groesse } from './netzText'
import { rufen } from './protokoll'

const VERLAUF = 60

/** Zählt als Stand (Speicher, Knoten …). */
const STAENDE = ['JSHeapUsedSize', 'JSHeapTotalSize', 'Nodes', 'JSEventListeners', 'Documents', 'Frames'] as const
/** Wächst mit der Zeit; gezeigt wird der Anteil je Sekunde. */
const ZEITEN = ['TaskDuration', 'ScriptDuration', 'LayoutDuration', 'RecalcStyleDuration'] as const

type Werte = Record<string, number>

function Linie({ werte }: { werte: number[] }) {
  if (werte.length < 2) return <svg className="h-6 w-24" aria-hidden="true" />
  const max = Math.max(...werte, 1e-9)
  const punkte = werte.map((w, i) => `${(i / (VERLAUF - 1)) * 96},${22 - (w / max) * 20}`).join(' ')
  return (
    <svg viewBox="0 0 96 24" className="h-6 w-24 shrink-0 text-primary" aria-hidden="true">
      <polyline points={punkte} fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  )
}

export function Leistung({ tab }: { tab: string }) {
  const { t } = useTranslation()
  const [verlauf, setVerlauf] = useState<Werte[]>([])
  const vorher = useRef<Werte | null>(null)

  useEffect(() => {
    let gilt = true
    vorher.current = null
    setVerlauf([])
    const holen = async () => {
      const r = await rufen<{ metrics: { name: string; value: number }[] }>(tab, 'Performance.getMetrics').catch(() => null)
      if (!gilt || !r?.metrics) return
      const jetzt: Werte = Object.fromEntries(r.metrics.map((m) => [m.name, m.value]))
      const alt = vorher.current
      vorher.current = jetzt
      const zeile: Werte = { ...jetzt }
      // Anteile der vergangenen Sekunde aus den Summen.
      if (alt) {
        const spanne = Math.max(1e-3, jetzt.Timestamp - alt.Timestamp)
        for (const z of ZEITEN) zeile[z] = Math.min(1, Math.max(0, (jetzt[z] - alt[z]) / spanne))
      } else for (const z of ZEITEN) zeile[z] = 0
      setVerlauf((v) => [...v, zeile].slice(-VERLAUF))
    }
    void rufen(tab, 'Performance.enable', { timeDomain: 'timeTicks' })
      .catch(() => null)
      .then(holen)
    const takt = setInterval(() => void holen(), 1000)
    return () => {
      gilt = false
      clearInterval(takt)
      void rufen(tab, 'Performance.disable').catch(() => null)
    }
  }, [tab])

  const letzte = verlauf[verlauf.length - 1]
  if (!letzte) return <p className="p-3 text-label-sm text-on-surface-variant">{t('browser.entwickler.laedt')}</p>
  const text = (name: string, w: number) =>
    name.startsWith('JSHeap') ? groesse(Math.round(w)) : (ZEITEN as readonly string[]).includes(name) ? `${Math.round(w * 100)} %` : String(Math.round(w))

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-2">
      <p className="px-1 pb-2 text-label-sm text-on-surface-variant">{t('browser.entwickler.leistung.hinweis')}</p>
      <dl className="grid gap-1">
        {[...STAENDE, ...ZEITEN].map((name) => (
          <div key={name} className="flex items-center gap-3 rounded-md bg-surface-container-low px-3 py-1.5 text-label-sm">
            <dt className="min-w-0 flex-1 text-on-surface">{t(`browser.entwickler.leistung.metrik.${name}`)}</dt>
            <Linie werte={verlauf.map((v) => v[name] ?? 0)} />
            <dd className="w-20 shrink-0 text-right tabular-nums text-on-surface">{text(name, letzte[name] ?? 0)}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}
