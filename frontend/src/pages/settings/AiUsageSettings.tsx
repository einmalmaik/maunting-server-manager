import { useEffect, useState } from 'react'
import { BarChart3 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiUsageOverview } from '@/api/ai'
import { toast } from '@/stores/toastStore'

/**
 * Wer wieviel KI verbraucht — die Wirkung von `ai.usage.read.all`.
 *
 * Das Recht stand seit dem ersten Entwurf im Katalog und wurde nirgends
 * geprüft; der Rollen-Editor musste es als „noch ohne Funktion" beschriften.
 * Diese Ansicht ist die Funktion dazu.
 *
 * Sie hängt bewusst **nicht** an `panel.settings.read`, obwohl sie hier steht:
 * wer Verbräuche sieht, sieht das Nutzungsverhalten fremder Kunden. Das ist
 * eine eigene Entscheidung des Betreibers, kein Nebeneffekt davon, dass jemand
 * Kontingente einstellen darf.
 *
 * Die Zahlen stammen aus derselben Quelle, die auch die Sperre durchsetzt —
 * eine Ansicht, die andere Werte zeigt als die Grenze prüft, wäre schlimmer als
 * gar keine: die Frage „warum wurde ich abgewiesen?" bliebe dann unbeantwortbar.
 */
export function AiUsageSettings() {
  const { t, i18n } = useTranslation()
  const [data, setData] = useState<AiUsageOverview | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    aiApi.getUsageOverview()
      .then((result) => { if (active) setData(result) })
      .catch(() => { if (active) toast.error(t('ai.usage.errors.load')) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [t])

  if (loading || !data) return null

  const numbers = new Intl.NumberFormat(i18n.language)
  const money = new Intl.NumberFormat(i18n.language, { minimumFractionDigits: 2 })
  const cents = (value: number) => money.format(value / 100)

  return (
    <section className="msm-card space-y-4 p-6" aria-labelledby="ai-usage-title">
      <div className="flex items-center gap-2">
        <BarChart3 className="h-5 w-5 text-secondary" aria-hidden="true" />
        <h3 id="ai-usage-title" className="font-headline text-lg font-semibold text-on-surface">
          {t('ai.usage.title')}
        </h3>
      </div>
      <p className="max-w-3xl text-sm text-on-surface-variant">{t('ai.usage.description')}</p>

      {data.entries.length === 0 ? (
        <p className="rounded-lg border border-outline-variant/40 bg-surface-container-low/45 p-3 text-sm text-on-surface-variant">
          {t('ai.usage.empty')}
        </p>
      ) : (
        <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-outline-variant/40 text-left text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                  <th scope="col" className="py-2 pr-4">{t('ai.usage.user')}</th>
                  <th scope="col" className="py-2 pr-4 text-right">{t('ai.usage.today')}</th>
                  <th scope="col" className="py-2 pr-4 text-right">{t('ai.usage.week')}</th>
                  <th scope="col" className="py-2 pr-4 text-right">{t('ai.usage.month')}</th>
                  <th scope="col" className="py-2 pr-4 text-right">{t('ai.usage.requests')}</th>
                  <th scope="col" className="py-2 text-right">{t('ai.usage.cost')}</th>
                </tr>
              </thead>
              <tbody>
                {data.entries.map((entry) => (
                  <tr key={entry.user_id} className="border-b border-outline-variant/20 last:border-0">
                    <td className="py-2 pr-4 text-on-surface">{entry.username}</td>
                    <td className="py-2 pr-4 text-right tabular-nums text-on-surface-variant">{numbers.format(entry.tokens_today)}</td>
                    <td className="py-2 pr-4 text-right tabular-nums text-on-surface-variant">{numbers.format(entry.tokens_week)}</td>
                    <td className="py-2 pr-4 text-right tabular-nums text-on-surface">{numbers.format(entry.tokens_month)}</td>
                    <td className="py-2 pr-4 text-right tabular-nums text-on-surface-variant">{numbers.format(entry.requests_month)}</td>
                    <td className="py-2 text-right tabular-nums text-on-surface">{cents(entry.cost_month_cents)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-outline-variant/40 font-semibold">
                  <td className="py-2 pr-4 text-on-surface">{t('ai.usage.total')}</td>
                  <td colSpan={2} />
                  <td className="py-2 pr-4 text-right tabular-nums text-on-surface">{numbers.format(data.total_tokens_month)}</td>
                  <td />
                  <td className="py-2 text-right tabular-nums text-on-surface">{cents(data.total_cost_month_cents)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
          <p className="text-xs text-on-surface-variant">{t('ai.usage.hint')}</p>
        </>
      )}
    </section>
  )
}
