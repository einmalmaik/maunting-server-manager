import { useId } from 'react'
import { useTranslation } from 'react-i18next'

import type { AiContextStatus } from '@/api/ai'

/** Umfang des Rings. Muss zum `r` im SVG passen: 2 · π · 7 ≈ 43,98. */
const UMFANG = 43.98
/**
 * Ab wie vielen Prozentpunkten vor der Faltmarke der Ring warnt.
 *
 * Ohne Vorwarnung springt die Farbe genau in dem Moment um, in dem ohnehin
 * gefaltet wird — dann ist die Anzeige eine Meldung über Vergangenes statt ein
 * Hinweis. Zehn Punkte sind bei jedem Fenster etwa eine Handvoll Nachrichten.
 */
const WARNUNG_VOR = 10

/**
 * Wie voll der Kontext ist, als Ring neben dem Absende-Knopf.
 *
 * Er beantwortet zwei Fragen, die vorher nirgends beantwortet waren: *wie viel
 * Gespräch hat die KI gerade vor sich* und *wann wird zusammengefasst*. Bis
 * hierher war beides unsichtbar — der Chat faltete, zeigte eine Zeile darüber,
 * und niemand konnte sehen, warum jetzt und nicht später.
 *
 * Rein darstellend: geladen wird in `AiChat`, damit es eine Stelle gibt, die
 * weiß, wann die Zahlen veraltet sind (nach jeder Antwort und nach jedem
 * Falten).
 *
 * Bei `known: false` — der Katalog kennt das Modell nicht — zeigt der Ring
 * **keinen** Prozentwert. Ein geschätzter sähe genauso aus wie ein gemessener,
 * und man würde ihm glauben.
 */
export function AiContextMeter({ status }: { status: AiContextStatus | null }) {
  const { t } = useTranslation()
  const tooltipId = useId()
  if (!status) return null

  const anteil = status.usable_tokens > 0
    ? Math.min(status.used_tokens / status.usable_tokens, 1)
    : 0
  const prozent = Math.round(anteil * 100)
  const faltmarke = status.usable_tokens > 0
    ? Math.round((status.compaction_at_tokens / status.usable_tokens) * 100)
    : 100
  const farbe = !status.known
    ? 'text-on-surface-variant/60'
    : prozent >= faltmarke
      ? 'text-status-warning'
      : prozent >= faltmarke - WARNUNG_VOR
        ? 'text-status-warning/70'
        : 'text-on-surface-variant'

  const zeilen = status.known
    ? [
        t('ai.context.tooltip.window', { tokens: format(status.window_tokens ?? 0) }),
        t('ai.context.tooltip.used', { tokens: format(status.used_tokens), percent: prozent }),
        t('ai.context.tooltip.free', {
          tokens: format(Math.max(status.usable_tokens - status.used_tokens, 0)),
        }),
        t('ai.context.tooltip.compaction', { percent: status.compaction_percent }),
        ...(status.summarized ? [t('ai.context.tooltip.summarized')] : []),
      ]
    : [t('ai.context.tooltip.unknown')]

  return (
    <div className="group relative flex h-9 shrink-0 items-center">
      <span
        tabIndex={0}
        role="img"
        aria-label={zeilen.join(' ')}
        aria-describedby={tooltipId}
        className={`grid h-7 w-7 place-items-center rounded-full outline-none focus-visible:ring-2 focus-visible:ring-ring ${farbe}`}
      >
        <svg viewBox="0 0 18 18" className="h-4 w-4 -rotate-90" aria-hidden="true">
          {/* Die Spur bewusst aus `currentColor` mit Deckkraft statt aus einem
              eigenen Farbtoken: so folgt sie jedem Farbwechsel des Rings, ohne
              dass es zwei Stellen gäbe, an denen die Farbe steht. */}
          <circle cx="9" cy="9" r="7" fill="none" strokeWidth="2.5" stroke="currentColor" opacity="0.2" />
          {status.known && (
            <circle
              cx="9" cy="9" r="7" fill="none" strokeWidth="2.5" strokeLinecap="round"
              stroke="currentColor"
              strokeDasharray={`${(anteil * UMFANG).toFixed(2)} ${UMFANG}`}
            />
          )}
        </svg>
      </span>
      {/* Nur CSS, kein Zustand: der Ring wird nach jeder Antwort neu gezeichnet,
          und ein offener Tooltip mit eigenem State überlebte das nicht. */}
      <div
        id={tooltipId}
        role="tooltip"
        className="pointer-events-none absolute bottom-full right-0 z-50 mb-2 hidden w-56 rounded-lg border border-outline-variant bg-surface-container-high p-2.5 text-xs leading-5 text-on-surface-variant shadow-panel group-hover:block group-focus-within:block"
      >
        {zeilen.map((zeile) => <p key={zeile}>{zeile}</p>)}
      </div>
    </div>
  )
}

/**
 * Tausender als „k". Die genaue Tokenzahl ist ohnehin geschätzt — sie auf die
 * Einerstelle auszuschreiben verspräche eine Genauigkeit, die es nicht gibt.
 */
function format(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}k`
  return String(tokens)
}
