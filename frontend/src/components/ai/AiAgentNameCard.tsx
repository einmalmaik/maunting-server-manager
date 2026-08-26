import { useState } from 'react'
import { Signature } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { api } from '@/api/client'
import { Button } from '@/Singra/UI'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'

/**
 * Der Rufname des Assistenten — die eine Personalisierung, die dem Benutzer
 * gehört und nicht dem Betreiber.
 *
 * Leer heißt Standardname „Singra“; was als Name erlaubt ist, entscheidet
 * allein das Backend (schemas/user.py), das Frontend rät nicht mit. Der Name
 * landet dort im Lageblock des Assistenten und gilt damit überall zugleich:
 * im Panel-Chat, im Sprachmodus und in der Desktop-App.
 */
export function AiAgentNameCard() {
  const { t } = useTranslation()
  const { user, updateUser } = useAuthStore()
  const [name, setName] = useState(user?.agent_name ?? '')
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    setSaving(true)
    try {
      const antwort = await api<{ agent_name: string | null }>('/auth/me/agent-name', {
        method: 'PATCH',
        body: JSON.stringify({ agent_name: name.trim() || null }),
      })
      updateUser({ agent_name: antwort.agent_name })
      setName(antwort.agent_name ?? '')
      toast.success(t('ai.profile.agentNameSaved', 'Name gespeichert.'))
    } catch (err: any) {
      toast.error(err.message || t('common.error'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="msm-card space-y-4 p-6" aria-labelledby="ai-agent-name-title">
      <div className="flex items-center gap-2">
        <Signature className="h-5 w-5 text-secondary" aria-hidden="true" />
        <h2 id="ai-agent-name-title" className="font-headline text-lg font-semibold text-on-surface">
          {t('ai.profile.agentNameTitle', 'Name des Assistenten')}
        </h2>
      </div>
      <p className="max-w-3xl text-sm text-on-surface-variant">
        {t('ai.profile.agentNameDescription')}
      </p>
      <div className="flex max-w-md items-end gap-3">
        <label className="flex-1">
          <span className="mb-1 block text-xs font-medium text-on-surface-variant">
            {t('ai.profile.agentNameLabel', 'Rufname')}
          </span>
          <input
            className="msm-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Assistent"
            maxLength={32}
          />
        </label>
        <Button onClick={handleSave} disabled={saving || (name.trim() || '') === (user?.agent_name ?? '')}>
          {t('common.save', 'Speichern')}
        </Button>
      </div>
    </section>
  )
}
