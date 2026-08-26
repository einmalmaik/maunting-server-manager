import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Sparkles, Trash2, Zap } from 'lucide-react'
import { Button, Dropdown } from '@/Singra/UI'
import { aiApi, type AiProviderAvailable } from '@/api/ai'
import { api } from '@/api/client'
import { useHasPermission } from '@/hooks/useHasPermission'
import {
  aiChatPreferenceKeys,
  readAiProviderChoice,
  readAiReasoningChoice,
  writeAiProviderChoice,
  writeAiReasoningChoice,
  type Denkwahl,
} from '@/lib/aiChatPreferences'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'
import { AiAutonomyButton, type ServerOption } from './AiAutonomyButton'
import { denkwahlFuer, ReasoningPicker } from './ReasoningPicker'

export function MobileAiControls({ onActionDone }: { onActionDone?: () => void }) {
  const { t } = useTranslation()
  const user = useAuthStore((s) => s.user)
  const canUseAutonomy = useHasPermission('ai.autonomous.use')
  const userId = user?.id ?? 'anonym'
  const keys = useMemo(() => aiChatPreferenceKeys(userId), [userId])

  const [providers, setProviders] = useState<AiProviderAvailable[]>([])
  const [providerId, setProviderId] = useState<number | null>(() => readAiProviderChoice(keys.provider))
  const [denken, setDenken] = useState<Denkwahl>(
    () => readAiReasoningChoice(keys.reasoning) ?? { an: false, stufe: null }
  )
  const [servers, setServers] = useState<ServerOption[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    aiApi.listProviders()
      .then((liste) => {
        if (!active) return
        setProviders(liste)
        const available = liste.filter((p) => p.available)
        const currentSaved = readAiProviderChoice(keys.provider)
        const chosen = available.some((p) => p.id === currentSaved)
          ? currentSaved
          : (user?.ai_provider_id && available.some((p) => p.id === user.ai_provider_id))
            ? user.ai_provider_id
            : available[0]?.id ?? null
        setProviderId(chosen)
      })
      .catch(() => {})
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => { active = false }
  }, [keys.provider, user?.ai_provider_id])

  useEffect(() => {
    if (!canUseAutonomy) return
    let active = true
    api<ServerOption[]>('/servers')
      .then((liste) => {
        if (active) setServers(liste)
      })
      .catch(() => {})
    return () => { active = false }
  }, [canUseAutonomy])

  const availableProviders = useMemo(() => providers.filter((p) => p.available), [providers])
  const activeProvider = useMemo(
    () => availableProviders.find((p) => p.id === providerId) ?? availableProviders[0] ?? null,
    [availableProviders, providerId]
  )

  const handleProviderChange = (rawId: string) => {
    const nextId = Number(rawId)
    setProviderId(nextId)
    writeAiProviderChoice(keys.provider, nextId)
    const target = availableProviders.find((p) => p.id === nextId)
    if (target) {
      const nextDenken = denkwahlFuer(denken, target)
      setDenken(nextDenken)
      writeAiReasoningChoice(keys.reasoning, nextDenken)
    }
    window.dispatchEvent(new Event('msm:ai-preference-changed'))
  }

  const handleReasoningChange = (nextDenken: Denkwahl) => {
    setDenken(nextDenken)
    writeAiReasoningChoice(keys.reasoning, nextDenken)
    window.dispatchEvent(new Event('msm:ai-preference-changed'))
  }

  const handleClearHistory = async () => {
    try {
      await aiApi.clearHistory()
      toast.success(t('ai.chat.cleared', 'Verlauf gelöscht'))
      window.dispatchEvent(new Event('msm:ai-chat-cleared'))
      onActionDone?.()
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  if (loading && availableProviders.length === 0) return null

  return (
    <div className="space-y-3 rounded-xl border border-outline-variant/40 bg-surface-container/60 p-3">
      <div className="space-y-1.5">
        <label className="flex items-center gap-1.5 text-xs font-medium text-on-surface-variant">
          <Sparkles className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
          <span>{t('ai.chat.provider', 'KI-Anbieter & Modell')}</span>
        </label>
        <Dropdown
          value={providerId ? String(providerId) : null}
          onChange={handleProviderChange}
          options={availableProviders.map((provider) => ({
            value: String(provider.id),
            label: provider.name,
            hint: provider.default_model,
          }))}
          placeholder={t('ai.chat.selectProvider')}
        />
      </div>

      {activeProvider?.reasoning && (
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-on-surface-variant">
            {t('ai.chat.reasoningLevel', 'Nachdenken / Denkschritte')}
          </label>
          <ReasoningPicker
            provider={activeProvider}
            wahl={denken}
            onChange={handleReasoningChange}
            compact={true}
          />
        </div>
      )}

      {canUseAutonomy && (
        <div className="flex items-center justify-between pt-1 border-t border-outline-variant/30">
          <div className="flex items-center gap-1.5">
            <Zap className="h-3.5 w-3.5 text-status-warning" aria-hidden="true" />
            <span className="text-xs font-medium text-on-surface">{t('ai.autonomy.title', 'Autonomer Modus')}</span>
          </div>
          <AiAutonomyButton servers={servers} />
        </div>
      )}

      <div className="pt-2 border-t border-outline-variant/30 flex justify-end">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={handleClearHistory}
          className="w-full justify-center text-xs text-status-error hover:bg-status-error/10 hover:text-status-error"
        >
          <Trash2 className="h-3.5 w-3.5 mr-1.5" aria-hidden="true" />
          {t('ai.chat.clear', 'Verlauf leeren')}
        </Button>
      </div>
    </div>
  )
}
