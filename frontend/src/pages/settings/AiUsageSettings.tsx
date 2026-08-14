import { useEffect, useState } from 'react'
import { BarChart3 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiUsageEvents, type AiUsageOverview } from '@/api/ai'
import { toast } from '@/stores/toastStore'
import { betragFormatieren } from '@/utils/geld'

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
  const [events, setEvents] = useState<AiUsageEvents | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    aiApi.getUsageOverview()
      .then((result) => { if (active) setData(result) })
      .catch(() => { if (active) toast.error(t('ai.usage.errors.load')) })
      .finally(() => { if (active) setLoading(false) })
    // Die Einzelaufstellung wird getrennt geholt und **still**: sie ist der
    // Nachweis neben den Summen, nicht deren Voraussetzung. Fällt sie aus,
    // fehlt sie — die Übersicht darüber bleibt bedienbar.
    aiApi.getUsageEvents(50)
      .then((result) => { if (active) setEvents(result) })
      .catch(() => { /* still */ })
    return () => { active = false }
  }, [t])

  if (loading || !data) return null

  const numbers = new Intl.NumberFormat(i18n.language)
  const geld = (micro: number) => betragFormatieren(micro, data.cost_policy, i18n.language)
  const zeitpunkt = new Intl.DateTimeFormat(i18n.language, {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  })

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
                    <td className="py-2 text-right tabular-nums text-on-surface">{geld(entry.cost_month_micro_usd).primaer}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-outline-variant/40 font-semibold">
                  <td className="py-2 pr-4 text-on-surface">{t('ai.usage.total')}</td>
                  <td colSpan={2} />
                  <td className="py-2 pr-4 text-right tabular-nums text-on-surface">{numbers.format(data.total_tokens_month)}</td>
                  <td />
                  <td className="py-2 text-right tabular-nums text-on-surface">
                    {geld(data.total_cost_month_micro_usd).primaer}
                    {geld(data.total_cost_month_micro_usd).sekundaer && (
                      <span className="block text-xs font-normal text-on-surface-variant">
                        {geld(data.total_cost_month_micro_usd).sekundaer}
                      </span>
                    )}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
          <p className="text-xs text-on-surface-variant">{t('ai.usage.hint')}</p>
        </>
      )}

      {events && events.entries.length > 0 && (
        <div className="space-y-2 border-t border-outline-variant/40 pt-4">
          <h4 className="font-headline text-sm font-semibold text-on-surface">
            {t('ai.usage.events.title')}
          </h4>
          {/* Der Satz, der die eigentliche Frage beantwortet: warum eine
              einzelne Chatnachricht mit sechsstelligen Tokenzahlen dasteht. */}
          <p className="max-w-3xl text-xs text-on-surface-variant">
            {t('ai.usage.events.description')}
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-outline-variant/40 text-left text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                  <th scope="col" className="py-2 pr-4">{t('ai.usage.events.when')}</th>
                  <th scope="col" className="py-2 pr-4">{t('ai.usage.user')}</th>
                  <th scope="col" className="py-2 pr-4">{t('ai.usage.events.model')}</th>
                  <th scope="col" className="py-2 pr-4 text-right">{t('ai.usage.events.input')}</th>
                  <th scope="col" className="py-2 pr-4 text-right">{t('ai.usage.events.output')}</th>
                  <th scope="col" className="py-2 pr-4 text-right">{t('ai.usage.events.cached')}</th>
                  <th scope="col" className="py-2 pr-4 text-right">{t('ai.usage.events.cacheWritten')}</th>
                  <th scope="col" className="py-2 pr-4 text-right">{t('ai.usage.events.calls')}</th>
                  <th scope="col" className="py-2 pr-4 text-right">{t('ai.usage.cost')}</th>
                  <th scope="col" className="py-2">{t('ai.usage.events.source')}</th>
                </tr>
              </thead>
              <tbody>
                {events.entries.map((event) => {
                  const betrag = betragFormatieren(
                    event.cost_micro_usd, events.cost_policy, i18n.language,
                  )
                  // Drei Zustände, nicht zwei. Eine Bestandszeile ohne
                  // Herkunft ist etwas anderes als eine geschätzte: bei ihr
                  // wurde nicht geraten, es ist nur nicht mehr feststellbar.
                  const herkunft = event.cost_source ?? 'unknown'
                  return (
                    <tr key={event.id} className="border-b border-outline-variant/20 last:border-0">
                      <td className="py-2 pr-4 whitespace-nowrap text-on-surface-variant">
                        {zeitpunkt.format(new Date(event.created_at))}
                      </td>
                      <td className="py-2 pr-4 text-on-surface-variant">{event.username}</td>
                      <td className="py-2 pr-4 text-on-surface-variant">{event.model ?? '—'}</td>
                      <td className="py-2 pr-4 text-right tabular-nums text-on-surface-variant">
                        {event.prompt_tokens === null ? '—' : numbers.format(event.prompt_tokens)}
                      </td>
                      <td className="py-2 pr-4 text-right tabular-nums text-on-surface-variant">
                        {event.completion_tokens === null ? '—' : numbers.format(event.completion_tokens)}
                      </td>
                      <td className="py-2 pr-4 text-right tabular-nums text-on-surface-variant">
                        {event.cached_tokens === null ? '—' : numbers.format(event.cached_tokens)}
                      </td>
                      <td className="py-2 pr-4 text-right tabular-nums text-on-surface-variant">
                        {event.cache_write_tokens === null ? '—' : numbers.format(event.cache_write_tokens)}
                      </td>
                      <td className="py-2 pr-4 text-right tabular-nums text-on-surface-variant">
                        {event.provider_requests === null ? '—' : numbers.format(event.provider_requests)}
                      </td>
                      <td className="py-2 pr-4 text-right tabular-nums text-on-surface" title={betrag.sekundaer ?? undefined}>
                        {betrag.primaer}
                      </td>
                      <td className="py-2">
                        <span
                          className={
                            herkunft === 'provider'
                              ? 'text-xs text-on-surface-variant'
                              : 'text-xs text-status-warning'
                          }
                        >
                          {t(`ai.usage.events.sources.${herkunft}`)}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          {events.has_more && (
            <p className="text-xs text-on-surface-variant">{t('ai.usage.events.more')}</p>
          )}
        </div>
      )}
    </section>
  )
}
