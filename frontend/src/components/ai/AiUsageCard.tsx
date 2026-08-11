import { useEffect, useState } from 'react'
import { Gauge } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiUsageMine } from '@/api/ai'

/**
 * Der eigene KI-Verbrauch, mit der eigenen Grenze daneben.
 *
 * Bewusst ohne Sonderrecht erreichbar: Wer von der KI ein „Kontingent
 * ausgeschöpft" bekommt, muss nachsehen können, woran es lag. Eine Grenze, die
 * ihre eigene Begründung verbirgt, ist für den Betroffenen nicht von einem
 * Fehler zu unterscheiden.
 *
 * Ohne hinterlegte Grenze steht hier nur der Verbrauch. Das ist der Normalfall
 * auf einer frischen Installation — „unbegrenzt" ist dort keine Nachlässigkeit,
 * sondern die ausdrückliche Voreinstellung (siehe `ai_limit_service`).
 */
export function AiUsageCard() {
  const { t, i18n } = useTranslation()
  const [data, setData] = useState<AiUsageMine | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let active = true
    aiApi.getMyUsage()
      .then((result) => { if (active) setData(result) })
      // Still: das Profil soll nicht wegen einer Nebenansicht eine Fehlermeldung
      // werfen. Ohne Zahlen fehlt die Karte, alles andere bleibt bedienbar.
      .catch(() => { if (active) setFailed(true) })
    return () => { active = false }
  }, [])

  if (failed || !data) return null

  const numbers = new Intl.NumberFormat(i18n.language)
  const money = new Intl.NumberFormat(i18n.language, { minimumFractionDigits: 2 })

  const periods: Array<{ key: string; used: number; limit: number | null }> = [
    { key: 'today', used: data.tokens_today, limit: data.limits.daily_token_limit },
    { key: 'week', used: data.tokens_week, limit: data.limits.weekly_token_limit },
    { key: 'month', used: data.tokens_month, limit: data.limits.monthly_token_limit },
  ]

  return (
    <section className="msm-card space-y-4 p-6" aria-labelledby="ai-usage-mine-title">
      <div className="flex items-center gap-2">
        <Gauge className="h-5 w-5 text-secondary" aria-hidden="true" />
        <h2 id="ai-usage-mine-title" className="font-headline text-lg font-semibold text-on-surface">
          {t('ai.usage.mineTitle')}
        </h2>
      </div>
      <p className="max-w-3xl text-sm text-on-surface-variant">{t('ai.usage.mineDescription')}</p>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {periods.map(({ key, used, limit }) => {
          // Drei Zustände, nicht zwei. `null` heißt „keine Grenze hinterlegt“,
          // 0 heißt „gesperrt“ — und das ist das Gegenteil davon. Vorher fielen
          // beide in denselben Zweig, sodass ausgerechnet der Gesperrte unter
          // seinem „0 / 0“ las, es sei gar keine Grenze gesetzt. Die Sperre ist
          // echt: `_ensure_within` im Backend weist mit 0 jede Anfrage ab.
          // Die 0 bleibt dabei weiterhin aus der Division heraus.
          let share: number | null = null
          if (limit === 0) share = 100
          else if (limit !== null) share = Math.min(100, (used / limit) * 100)

          // Dieselben Schwellen wie bei den CPU-/RAM-Balken (`Singra/UI/ProgressBar`
          // mit `heat`): Wer 90 % seines Kontingents verbraucht hat, soll das sehen,
          // ohne die Zahl darüber selbst ins Verhältnis setzen zu müssen. Eine
          // Grenze von 0 landet über die 100 von oben automatisch im roten Bereich.
          let barColor = 'bg-primary'
          if (share !== null && share >= 90) barColor = 'bg-status-error'
          else if (share !== null && share >= 70) barColor = 'bg-status-warning'
          return (
            <div key={key} className="space-y-2 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                {t(`ai.usage.${key}`)}
              </p>
              <p className="text-lg font-semibold tabular-nums text-on-surface">
                {numbers.format(used)}
                {limit !== null && (
                  <span className="text-sm font-normal text-on-surface-variant">
                    {' / '}{numbers.format(limit)}
                  </span>
                )}
              </p>
              {share === null ? (
                <p className="text-xs text-on-surface-variant">{t('ai.usage.noLimit')}</p>
              ) : (
                <>
                  <div
                    className="h-1.5 overflow-hidden rounded-full bg-surface-container-high"
                    role="progressbar"
                    aria-valuenow={Math.round(share)}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={t(`ai.usage.${key}`)}
                  >
                    <div className={`h-full rounded-full ${barColor}`} style={{ width: `${share}%` }} />
                  </div>
                  {limit === 0 && (
                    // Der volle rote Balken allein bliebe zweideutig — er sieht aus
                    // wie „heute aufgebraucht, morgen wieder da“. Der Satz nennt den
                    // Unterschied: hier war nie etwas freigegeben.
                    <p className="text-xs text-status-error">{t('ai.usage.blocked')}</p>
                  )}
                </>
              )}
            </div>
          )
        })}
      </div>

      <p className="text-xs text-on-surface-variant">
        {t('ai.usage.mineCost', {
          cost: money.format(data.cost_month_cents / 100),
          requests: numbers.format(data.requests_month),
        })}
      </p>
    </section>
  )
}
