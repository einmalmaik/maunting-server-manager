import { useEffect, useState } from 'react'
import { Check, GraduationCap, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiLearningPolicy, type AiSkillManaged } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button, Dropdown } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

/**
 * Ob und wie die KI panelweit gültige Skills anlegen darf.
 *
 * Ein globaler Skill wirkt für **jeden** Benutzer des Panels, bei einem Hoster
 * also für alle Kunden. Das ist die einzige Stelle, an der ein Gespräch Text in
 * den Kontext fremder Gespräche bringen kann — deshalb eine eigene Entscheidung
 * des Betreibers und keine Voreinstellung im Code.
 *
 * Die Freigabeliste steht direkt darunter, weil die Stufe „Freigabe nötig" ohne
 * sichtbaren Rückstau eine Sackgasse wäre: gelernte Skills lägen dort und
 * niemand wüsste davon.
 */
export function AiLearningSettings({ canWrite }: { canWrite: boolean }) {
  const { t } = useTranslation()
  const [state, setState] = useState<AiLearningPolicy | null>(null)
  const [pending, setPending] = useState<AiSkillManaged[]>([])
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)

  const reloadPending = async () => {
    setPending(await aiApi.listPendingSkills().catch(() => [] as AiSkillManaged[]))
  }

  useEffect(() => {
    let active = true
    Promise.all([
      aiApi.getLearningPolicy(),
      aiApi.listPendingSkills().catch(() => [] as AiSkillManaged[]),
    ])
      .then(([policy, rows]) => {
        if (!active) return
        setState(policy)
        setPending(rows)
      })
      .catch(() => { if (active) toast.error(t('ai.learning.errors.load')) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [t])

  const changePolicy = async (value: string) => {
    if (!canWrite || busy) return
    setBusy(true)
    try {
      setState(await aiApi.setLearningPolicy(value as AiLearningPolicy['policy']))
      toast.success(t('ai.learning.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.learning.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  const approve = async (row: AiSkillManaged) => {
    setBusy(true)
    try {
      await aiApi.approveSkill(row.id)
      await reloadPending()
      toast.success(t('ai.skills.approved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.skills.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  const reject = async (row: AiSkillManaged) => {
    if (!await confirm({ message: t('ai.skills.remove'), confirmText: t('common.delete'), danger: true })) return
    setBusy(true)
    try {
      await aiApi.deleteSkill(row.id)
      await reloadPending()
      toast.success(t('ai.skills.deleted'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.skills.errors.save'))
    } finally {
      setBusy(false)
    }
  }

  if (loading || !state) return null

  return (
    <section className="msm-card space-y-4 p-6" aria-labelledby="ai-learning-title">
      <div className="flex items-center gap-2">
        <GraduationCap className="h-5 w-5 text-tertiary" aria-hidden="true" />
        <h3 id="ai-learning-title" className="font-headline text-lg font-semibold text-on-surface">
          {t('ai.learning.title')}
        </h3>
      </div>
      <p className="max-w-3xl text-sm text-on-surface-variant">{t('ai.learning.description')}</p>

      <label className="block w-full max-w-md space-y-1.5">
        <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
          {t('ai.learning.policy')}
        </span>
        <Dropdown
          value={state.policy}
          onChange={(value) => void changePolicy(value)}
          options={[
            { value: 'off', label: t('ai.learning.options.off'), hint: t('ai.learning.hints.off') },
            { value: 'review', label: t('ai.learning.options.review'), hint: t('ai.learning.hints.review') },
            { value: 'instant', label: t('ai.learning.options.instant'), hint: t('ai.learning.hints.instant') },
          ]}
          disabled={!canWrite || busy}
          aria-label={t('ai.learning.policy')}
        />
      </label>

      <div className="space-y-2 border-t border-outline-variant/40 pt-4">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
          {t('ai.skills.pendingTitle')}
        </h4>
        <p className="max-w-3xl text-xs leading-5 text-on-surface-variant">{t('ai.skills.pendingHint')}</p>

        {/* Die Liste kommt von /ai/skills/pending und verlangt ai.skills.manage —
            die Zahl daneben nur panel.settings.read. Wer die Stufe einstellen darf,
            aber die Warteschlange nicht sehen, bekäme sonst „Nichts zu prüfen"
            zu lesen, während sich der Rückstau aufbaut. */}
        {pending.length === 0 && (
          <p className="text-sm text-on-surface-variant">
            {state.pending_count > 0
              ? t('ai.skills.pendingHidden', { count: state.pending_count })
              : t('ai.skills.pendingEmpty')}
          </p>
        )}

        <ul className="space-y-2">
          {pending.map((row) => (
            <li
              key={row.id}
              className="space-y-2 rounded-xl border border-outline-variant/40 bg-surface-container-low/35 p-4"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-on-surface">{row.name}</span>
                <span className="text-xs text-on-surface-variant">{row.skill_key}</span>
              </div>
              <p className="text-xs leading-5 text-on-surface-variant">{row.description}</p>
              {/* Der vollständige Text steht hier bewusst mit — freigeben, ohne
                  gelesen zu haben, wäre keine Prüfung. */}
              <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded-lg bg-surface-container-high/60 p-3 font-mono text-[11px] leading-5 text-on-surface-variant">
                {row.body}
              </pre>
              {canWrite && (
                <div className="flex flex-wrap gap-2">
                  <Button type="button" size="sm" disabled={busy} onClick={() => void approve(row)}>
                    <Check className="h-4 w-4" aria-hidden="true" />
                    {t('ai.skills.approve')}
                  </Button>
                  <Button type="button" size="sm" variant="destructive" disabled={busy} onClick={() => void reject(row)}>
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                    {t('common.delete')}
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
