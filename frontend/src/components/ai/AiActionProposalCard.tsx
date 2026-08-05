import { AlertTriangle, FilePenLine, HardDriveDownload, Power } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiActionProposal } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

function previewText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

export function AiActionProposalCard({
  proposal,
  onChange,
}: {
  proposal: AiActionProposal
  onChange: (proposal: AiActionProposal) => void
}) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState(false)
  const operation = previewText(proposal.preview.operation)
  const path = previewText(proposal.preview.path)
  const diff = previewText(proposal.preview.diff)
  const Icon = proposal.tool_name === 'propose_config_update'
    ? FilePenLine
    : proposal.tool_name === 'propose_backup' ? HardDriveDownload : Power

  const execute = async () => {
    const accepted = await confirm({
      title: t('ai.actions.confirmTitle'),
      message: t(`ai.actions.confirm.${proposal.tool_name}`, { operation, path }),
      confirmText: t('ai.actions.execute'),
      danger: proposal.tool_name === 'propose_server_lifecycle',
    })
    if (!accepted) return
    setBusy(true)
    try {
      // Der kurzlebige Token bleibt nur in dieser Funktion und wird unmittelbar
      // fuer den zweiten, serverseitig erneut autorisierten Schritt verwendet.
      const confirmation = await aiApi.confirmAction(proposal.id)
      const executed = await aiApi.executeAction(proposal.id, confirmation.confirmation_token)
      onChange(executed.proposal)
      toast.success(t('ai.actions.executed'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.actions.error'))
      void aiApi.getAction(proposal.id).then(onChange).catch(() => undefined)
    } finally {
      setBusy(false)
    }
  }

  return (
    <article className="rounded-xl border border-status-warning/35 bg-status-warning/5 p-4" aria-label={t('ai.actions.title')}>
      <div className="flex flex-wrap items-start gap-3">
        <span className="rounded-lg bg-status-warning/10 p-2 text-status-warning"><Icon className="h-4 w-4" /></span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-on-surface">{t(`ai.actions.tools.${proposal.tool_name}`)}</h3>
            <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-xs text-on-surface-variant">{t(`ai.actions.status.${proposal.status}`)}</span>
          </div>
          {operation && <p className="mt-1 text-sm text-on-surface-variant">{t('ai.actions.operation', { operation })}</p>}
          {path && <p className="mt-1 break-all font-mono text-xs text-on-surface-variant">{path}</p>}
          {diff && <pre className="mt-3 max-h-64 overflow-auto rounded-lg border border-outline-variant/40 bg-surface-container-lowest p-3 text-xs text-on-surface-variant">{diff}</pre>}
          {proposal.error_code && <p className="mt-2 flex items-center gap-1 text-xs text-status-error"><AlertTriangle className="h-3.5 w-3.5" />{t('ai.actions.failed')}</p>}
        </div>
        {proposal.status === 'proposed' && <Button type="button" variant={proposal.tool_name === 'propose_server_lifecycle' ? 'destructive' : 'primary'} disabled={busy} onClick={() => void execute()}>{busy ? t('ai.actions.executing') : t('ai.actions.review')}</Button>}
      </div>
    </article>
  )
}
