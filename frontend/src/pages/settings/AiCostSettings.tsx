import { useEffect, useState } from 'react'
import { Coins } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiCostPolicy } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Dropdown } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { betragFormatieren } from '@/utils/geld'

/**
 * In welcher Währung der Betreiber seine KI-Kosten liest.
 *
 * Ausdrücklich nur die **Anzeige**. Gebucht wird ausnahmslos in US-Cent, weil
 * der Anbieter in USD abrechnet — eine Umrechnung vor der Buchung wäre eine
 * zweite Fehlerquelle, und ein Kurs, der sich täglich ändert, würde rückwirkend
 * Zeilen verändern, die längst bezahlt sind.
 *
 * Der Kurs kommt vom Betreiber und nicht aus dem Netz. Ein Kursdienst wäre ein
 * weiterer Fremdzugriff, den ein selbstgehostetes Panel weder erklären noch
 * abschalten kann — für eine Zahl, die niemand auf den Cent braucht. Wer es
 * genauer will, trägt den Kurs seiner Bank ein; wer in Dollar abrechnet, lässt
 * beides, wie es ist.
 */
export function AiCostSettings({ canWrite }: { canWrite: boolean }) {
  const { t, i18n } = useTranslation()
  const [state, setState] = useState<AiCostPolicy | null>(null)
  const [entwurf, setEntwurf] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    aiApi.getCostPolicy()
      .then((policy) => {
        if (!active) return
        // Auf Form geprüft und nicht bloß auf „da": diese Karte steht mitten
        // in der KI-Seite, und eine Antwort, die nicht die erwartete ist —
        // ein älteres Backend, ein Proxy, der etwas anderes zurückgibt —
        // hätte sonst die ganze Seite mitgerissen, samt Kontingenten und
        // Anbietern. Die Währung ist eine Nebeneinstellung; sie darf keine
        // Hauptansicht kosten.
        if (!policy || !Array.isArray(policy.available_currencies)) return
        setState(policy)
        setEntwurf(policy.usd_rate)
      })
      .catch(() => { if (active) toast.error(t('ai.cost.errors.load')) })
    return () => { active = false }
  }, [t])

  const speichern = async (waehrung: string, kurs: string) => {
    if (!canWrite || busy) return
    setBusy(true)
    try {
      const frisch = await aiApi.setCostPolicy(waehrung, waehrung === 'USD' ? null : kurs)
      setState(frisch)
      // Vom Server zurück und nicht aus dem Entwurf: der Kurs wird dort auf
      // vier Stellen festgelegt, und das Feld soll zeigen, was gespeichert ist.
      setEntwurf(frisch.usd_rate)
      toast.success(t('ai.cost.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.cost.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  if (!state) return null

  // Ein Beispielbetrag mit dem **eingetippten** Kurs, nicht mit dem
  // gespeicherten: die Umrechnung soll sich ansehen lassen, bevor sie gilt.
  const beispiel = betragFormatieren(
    10_000_000, { currency: state.currency, usd_rate: entwurf }, i18n.language,
  )

  return (
    <section className="msm-card space-y-4 p-6" aria-labelledby="ai-cost-title">
      <div className="flex items-center gap-2">
        <Coins className="h-5 w-5 text-tertiary" aria-hidden="true" />
        <h3 id="ai-cost-title" className="font-headline text-lg font-semibold text-on-surface">
          {t('ai.cost.title')}
        </h3>
      </div>
      <p className="max-w-3xl text-sm text-on-surface-variant">{t('ai.cost.description')}</p>

      <div className="flex flex-wrap items-start gap-4">
        <label className="block w-full max-w-xs space-y-1.5">
          <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
            {t('ai.cost.currency')}
          </span>
          <Dropdown
            value={state.currency}
            onChange={(value) => void speichern(value, entwurf)}
            options={state.available_currencies.map((code) => ({
              value: code,
              label: t(`ai.cost.currencies.${code}`, { defaultValue: code }),
            }))}
            disabled={!canWrite || busy}
            aria-label={t('ai.cost.currency')}
          />
        </label>

        {/* Bei USD gibt es keinen Kurs — die Umrechnung von einer Währung in
            sich selbst ist keine. Das Feld verschwindet dann ganz, statt eine
            ausgegraute 1 zu zeigen, die nach einer Einstellung aussieht. */}
        {state.currency !== 'USD' && (
          <label className="block w-full max-w-xs space-y-1.5">
            <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('ai.cost.rate', { currency: state.currency })}
            </span>
            <input
              type="text"
              inputMode="decimal"
              className="msm-input w-full"
              value={entwurf}
              onChange={(event) => setEntwurf(event.target.value)}
              onBlur={() => {
                if (entwurf !== state.usd_rate) void speichern(state.currency, entwurf)
              }}
              disabled={!canWrite || busy}
              aria-label={t('ai.cost.rate', { currency: state.currency })}
            />
            <span className="block text-xs text-on-surface-variant">
              {t('ai.cost.rateRange', { min: state.min_rate, max: state.max_rate })}
            </span>
          </label>
        )}
      </div>

      <p className="max-w-3xl text-xs leading-5 text-on-surface-variant">
        {t('ai.cost.example', { amount: beispiel.primaer })}
      </p>
      <p className="max-w-3xl text-xs leading-5 text-on-surface-variant">
        {t('ai.cost.bookingHint')}
      </p>
    </section>
  )
}
