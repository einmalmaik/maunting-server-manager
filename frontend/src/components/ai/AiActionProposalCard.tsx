import { AlertTriangle, Blocks, Bot, FilePenLine, HardDriveDownload, HardDriveUpload, Network, Package, Power, ServerCog, Trash2 } from 'lucide-react'
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

/**
 * Werkzeuge, deren Wirkung sich nicht zurueckholen laesst.
 *
 * Der Bestaetigungsdialog faerbte nur `propose_server_lifecycle` als
 * gefaehrlich — also ausgerechnet das Werkzeug, dessen Wirkung ein Neustart
 * ist. Loeschen, ein Backup ueberspielen und der Blueprintwechsel standen in
 * derselben ruhigen Farbe wie „Backup erstellen“.
 *
 * `propose_server_lifecycle` bleibt drin: „stop“ auf einem laufenden Server
 * wirft Spieler heraus, und das ist im Moment des Klicks das Gleiche wert wie
 * eine Warnung.
 */
const UNUMKEHRBAR: readonly string[] = [
  'propose_server_delete',
  'propose_backup_restore',
  'propose_server_blueprint_switch',
  'propose_server_lifecycle',
]

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
  const Icon = {
    propose_config_update: FilePenLine,
    propose_config_patch: FilePenLine,
    propose_backup: HardDriveDownload,
    propose_mod_install: Package,
    propose_server_create: ServerCog,
    propose_server_lifecycle: Power,
    // Fuenf Werkzeuge fielen bisher auf das Standardsymbol zurueck — ein
    // Ein-/Ausschalter fuer „Server loeschen“ ist eine falsche Auskunft.
    propose_server_delete: Trash2,
    propose_backup_restore: HardDriveUpload,
    propose_bind_ip_update: Network,
    propose_blueprint_change: Blocks,
    propose_server_blueprint_switch: Blocks,
  }[proposal.tool_name] ?? Power
  // Eine autonom ausgefuehrte Aktion ist keine Anfrage. Sie bekommt deshalb
  // eine eigene, neutrale Farbgebung statt der warnenden — und keinen Knopf.
  const tone = proposal.autonomous
    ? 'border-outline-variant bg-surface-container'
    : 'border-status-warning/35 bg-status-warning/5'

  const execute = async () => {
    // Der Dialog zeigte bisher nur Operation und Pfad — Tool-Name und Diff
    // standen ausschliesslich in der Karte dahinter. Im Bestaetigungsmoment sah
    // der Benutzer damit weniger als vorher. Jetzt steht alles Wesentliche im
    // Dialog, inklusive des Hinweises, woher ein Vorschlag stammen kann:
    // Logs, Configs und Anhaenge sind Daten aus dem Server, nicht aus dem Panel.
    const message = [
      t(`ai.actions.tools.${proposal.tool_name}`),
      t(`ai.actions.confirm.${proposal.tool_name}`, { operation, path }),
      diff ? t('ai.actions.confirmDiffLines', { count: diff.split('\n').length }) : '',
      t('ai.actions.confirmProvenance'),
    ].filter(Boolean).join('\n\n')

    const accepted = await confirm({
      title: t('ai.actions.confirmTitle'),
      message,
      confirmText: t('ai.actions.execute'),
      danger: UNUMKEHRBAR.includes(proposal.tool_name),
    })
    if (!accepted) return
    setBusy(true)
    try {
      // Der kurzlebige Token bleibt nur in dieser Funktion und wird unmittelbar
      // fuer den zweiten, serverseitig erneut autorisierten Schritt verwendet.
      const confirmation = await aiApi.confirmAction(proposal.id)
      const executed = await aiApi.executeAction(proposal.id, confirmation.confirmation_token)
      onChange(executed.proposal)
      // Lifecycle-Aktionen laufen im Hintergrund weiter. Eine Erfolgsmeldung
      // waere hier eine Aussage ueber einen noch offenen Ausgang.
      toast.success(
        executed.proposal.status === 'executing'
          ? t('ai.actions.queued')
          : t('ai.actions.executed'),
      )
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.actions.error'))
      void aiApi.getAction(proposal.id).then(onChange).catch(() => undefined)
    } finally {
      setBusy(false)
    }
  }

  return (
    <article className={`rounded-xl border p-4 ${tone}`} aria-label={t('ai.actions.title')}>
      <div className="flex flex-wrap items-start gap-3">
        <span className={`rounded-lg p-2 ${proposal.autonomous ? 'bg-surface-container-highest text-primary' : 'bg-status-warning/10 text-status-warning'}`}><Icon className="h-4 w-4" /></span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-on-surface">{t(`ai.actions.tools.${proposal.tool_name}`)}</h3>
            <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-xs text-on-surface-variant">{t(`ai.actions.status.${proposal.status}`)}</span>
            {proposal.autonomous && (
              <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">
                <Bot className="h-3 w-3" />
                {t('ai.actions.autonomousBadge')}
              </span>
            )}
          </div>
          {operation && <p className="mt-1 text-sm text-on-surface-variant">{t('ai.actions.operation', { operation })}</p>}
          {path && <p className="mt-1 break-all font-mono text-xs text-on-surface-variant">{path}</p>}
          {/* Zielpunkt 3.6: warum geaendert wird und welche Folgen erwartet
              werden. Beides stammt vom Modell und wird als dessen Begruendung
              gekennzeichnet, nicht als Zusage des Panels. */}
          {proposal.reason && (
            <p className="mt-2 text-sm text-on-surface-variant">
              <span className="font-semibold text-on-surface">{t('ai.actions.reasonLabel')}</span>{' '}
              {proposal.reason}
            </p>
          )}
          {proposal.expected_effect && (
            <p className="mt-1 text-sm text-on-surface-variant">
              <span className="font-semibold text-on-surface">{t('ai.actions.effectLabel')}</span>{' '}
              {proposal.expected_effect}
            </p>
          )}
          {diff && <pre className="mt-3 max-h-64 overflow-auto rounded-lg border border-outline-variant/40 bg-surface-container-lowest p-3 text-xs text-on-surface-variant">{diff}</pre>}
          {proposal.autonomous && (
            <p className="mt-2 text-xs text-on-surface-variant">{t('ai.actions.autonomousHint')}</p>
          )}
          {proposal.error_code && <p className="mt-2 flex items-center gap-1 text-xs text-status-error"><AlertTriangle className="h-3.5 w-3.5" />{t('ai.actions.failed')}</p>}
        </div>
        {proposal.status === 'proposed' && !proposal.autonomous && <Button type="button" variant={proposal.tool_name === 'propose_server_lifecycle' ? 'destructive' : 'primary'} disabled={busy} onClick={() => void execute()}>{busy ? t('ai.actions.executing') : t('ai.actions.review')}</Button>}
      </div>
    </article>
  )
}
