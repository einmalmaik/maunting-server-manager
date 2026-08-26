import { Activity, AlertTriangle, Blocks, Bot, CalendarClock, ChevronDown, Eye, FilePenLine, FileX, Globe, HardDriveDownload, HardDriveUpload, Mail, Network, Package, Plug, Power, ServerCog, ShieldCheck, SlidersHorizontal, Trash2, Wrench } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { aiApi, type AiActionProposal } from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import { Button } from '@/Singra/UI'
import { SecretOnce } from '@/components/ui/SecretOnce'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

function previewText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

/**
 * Felder der Vorschau, die als **Panel-Tatsache** gerendert werden.
 *
 * Ohne sie liest der Bestaetigende ausser Werkzeugname und Pfad nur `reason`
 * und `expected_effect` — und beides ist vom Modell verfasster Text. Er wuerde
 * also bestaetigen, was das Modell ueber seinen eigenen Vorschlag behauptet.
 * Die Werte hier hat dagegen das Backend aufgeloest: der Name des
 * Dienstbenutzers, die Rechte der Rolle, das Kontingent.
 *
 * Bewusst eine Auswahl und keine Ausgabe des ganzen `preview`-Objekts: was der
 * Mensch im Bestaetigungsmoment liest, soll entschieden sein, nicht das
 * Nebenprodukt eines Payload-Baus.
 */
const TATSACHEN: readonly string[] = [
  'name',
  'slug',
  'service_user',
  'webhook_url',
  'terminate_grace_days',
  'integration',
  'external_product_key',
  'game_type',
  'ram_limit_mb',
  'cpu_limit_percent',
  'disk_limit_gb',
  'node_id',
  'backup_interval_hours',
  'role',
  'role_permissions',
  'permissions',
  'ai_limits',
  'enabled',
  // Das Ziel eines Blueprint-Vorschlags. Beim Loeschen stand es bisher
  // nirgends: `path` bleibt bei diesem Werkzeug leer, die Karte zeigte also
  // „blueprint_delete“ und sonst nichts. Das Backend loest beides auf — wer
  // zustimmt, soll den Namen dessen gelesen haben, was gleich verschwindet.
  'blueprint_id',
  'blueprint_name',
  // Beim Ableiten zaehlt, was am Ende wirklich startet. `startup_after` ist das
  // gefaehrlichste Feld des ganzen Vorschlags — eine falsche Startzeile laesst
  // den Server gar nicht erst hochkommen — und steht deshalb neben der
  // bisherigen, damit der Bestaetigende den Unterschied liest und nicht nur das
  // Ergebnis. Bei Image und Umgebung genuegt der Nachher-Wert: er ist der, der
  // nach der Bestaetigung gilt, und der Quell-Blueprint bleibt unveraendert
  // daneben bestehen.
  'startup_before',
  'startup_after',
  'image_after',
  'env_after',
  'server_name',
  'zugriff',
  'pfad',
  'zeilen_angefragt',
  'titel',
  'beschreibung',
  'aktion',
  'target_id',
  'quarantine_id',
  'snapshot_id',
  'publisher_hash',
  'recipient',
  'subject',
  'start_time',
  'end_time',
  'location',
  'calendar_id',
  'event_id',
]

function tatsachenZeilen(preview: Record<string, unknown>): [string, string][] {
  const zeilen: [string, string][] = []
  for (const key of TATSACHEN) {
    const wert = preview[key]
    if (wert === undefined || wert === null || wert === '') continue
    if (Array.isArray(wert)) {
      // Eine leere Rechteliste ist die Aussage dieses Vorschlags, kein
      // fehlender Wert — sie gehoert ausdruecklich gerendert.
      zeilen.push([key, wert.length ? wert.join(', ') : '—'])
    } else if (typeof wert === 'object') {
      const paare = Object.entries(wert as Record<string, unknown>)
        .filter(([, v]) => v !== null && v !== undefined)
      if (paare.length) zeilen.push([key, paare.map(([k, v]) => `${k}: ${String(v)}`).join(', ')])
    } else {
      zeilen.push([key, String(wert)])
    }
  }
  return zeilen
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
  // `propose_file_delete` steht hier, obwohl es in `ai_tool_registry` nicht
  // `immer_bestaetigen` ist. Das ist kein Widerspruch: die Registry entscheidet,
  // ob eine Freigabe uebersprungen werden darf, dieser Farbton entscheidet, wie
  // ein Mensch die Frage gestellt bekommt, wenn er sie doch bekommt. Eine
  // geloeschte Datei ist ohne Backup weg — das gehoert rot gefragt.
  // `propose_server_repair` fehlt hier bewusst: Rechte richten und einen Port
  // neu vergeben stellt einen Zustand her, den das Panel ohnehin herstellen
  // wuerde.
  'propose_file_delete',
  // `propose_blueprint_delete` aus demselben Grund: `delete_community_blueprint`
  // entfernt die Blueprint-Datei per `unlink`, es gibt keinen Schnappschuss und
  // keinen Papierkorb. Dass das Backend Blueprints mit aktiven Servern gar nicht
  // erst zum Loeschen zulaesst, schuetzt die laufenden Anlagen — den Blueprint
  // selbst holt danach niemand zurueck.
  'propose_blueprint_delete',
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
  // Frisch erzeugte Geheimnisse aus `executeAction`. Sie liegen ausschliesslich
  // hier, in einer lokalen Variablen dieser Karte — nicht in `entries`, nicht in
  // `ai_messages`, nicht im Lauf-Snapshot. Nach einem Neuladen sind sie weg, und
  // genau das heisst „genau einmal“.
  const [geheimnisse, setGeheimnisse] = useState<{ label: string; value: string }[]>([])
  const [detailsOpen, setDetailsOpen] = useState(proposal.status === 'proposed')
  const operation = previewText(proposal.preview.operation ?? proposal.preview.aktion)
  const path = previewText(proposal.preview.path)
  const diff = previewText(proposal.preview.diff)
  const tatsachen = tatsachenZeilen(proposal.preview as Record<string, unknown>)
  const ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
    propose_config_update: FilePenLine,
    propose_config_patch: FilePenLine,
    propose_config_set: FilePenLine,
    propose_backup: HardDriveDownload,
    propose_mod_install: Package,
    propose_mod_toggle: Package,
    propose_server_create: ServerCog,
    propose_server_lifecycle: Power,
    // Fuenf Werkzeuge fielen bisher auf das Standardsymbol zurueck — ein
    // Ein-/Ausschalter fuer „Server loeschen“ ist eine falsche Auskunft.
    propose_server_delete: Trash2,
    propose_backup_restore: HardDriveUpload,
    propose_bind_ip_update: Network,
    propose_blueprint_change: Blocks,
    // Das Loeschen lag auf demselben `Blocks` wie das harmlose Ableiten — im
    // Kartenkopf war „neuen Blueprint bauen“ von „Blueprint endgueltig
    // entfernen“ nicht zu unterscheiden. `Trash2` wie bei
    // `propose_server_delete`: hier wie dort verschwindet ein ganzer Eintrag,
    // nicht eine Datei im Serververzeichnis (das traegt `FileX`).
    propose_blueprint_delete: Trash2,
    propose_server_blueprint_switch: Blocks,
    propose_hoster_integration: Plug,
    propose_hoster_product: Plug,
    propose_ai_tarif_role: ShieldCheck,
    propose_server_repair: Wrench,
    // Regler, kein Werkzeugkasten: das Tuning stellt Werte am Autopiloten ein,
    // es repariert nichts — der `Wrench` daneben wuerde genau das behaupten.
    propose_guardian_tuning: SlidersHorizontal,
    propose_file_delete: FileX,
    // Ein stehender Auftrag ist eine Uhr, kein Serververhalten — deshalb
    // dasselbe Symbol fuers Anlegen wie fuers Loeschen, aber ein anderes als
    // fuer alles, was einen Server anfasst.
    propose_task_set: CalendarClock,
    propose_task_delete: CalendarClock,
    // Auch die eingebauten Zeitplaene sind Uhren: eingestellt wird, **wann**
    // neu gestartet oder gesichert wird — nicht der Vorgang selbst.
    propose_restart_schedule_set: CalendarClock,
    propose_backup_schedule_set: CalendarClock,
    worker_start: Bot,
    worker_cancel: Bot,
    web_search: Globe,
    read_server_status: Activity,
    read_server_logs: Eye,
    read_config: FilePenLine,
    list_my_servers: ServerCog,
    desktop_artifact: Package,
    propose_email_send: Mail,
    propose_calendar_event_create: CalendarClock,
    propose_calendar_event_delete: Trash2,
  }
  const Icon = ICONS[proposal.tool_name] ?? Power
  // Eine autonom ausgefuehrte Aktion ist keine Anfrage. Sie bekommt deshalb
  // eine eigene, neutrale Farbgebung statt der warnenden — und keinen Knopf.
  const tone = proposal.autonomous
    ? 'border-outline-variant/50 bg-surface-container/60'
    : 'border-status-warning/35 bg-status-warning/5'

  const reject = async () => {
    setBusy(true)
    try {
      const rejected = await aiApi.rejectAction(proposal.id)
      onChange(rejected)
      toast.success(t('ai.actions.rejectedToast', 'Aktion abgelehnt.'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.actions.error'))
      void aiApi.getAction(proposal.id).then(onChange).catch(() => undefined)
    } finally {
      setBusy(false)
    }
  }

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
      title: t(`ai.actions.tools.${proposal.tool_name}`, proposal.tool_name),
      message,
      confirmText: t('ai.actions.execute', 'Ausführen'),
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
      // Genau-einmal-Anzeige: das Backend liefert Secrets ausschliesslich
      // in der Rueckgabe dieses einen POSTs aus. Wir heben sie in den State
      // der Karte, und nirgendwo sonst hin.
      const frisch = (executed.result as { secrets?: { label: string; value: string }[] } | null)?.secrets
      if (Array.isArray(frisch) && frisch.length) setGeheimnisse(frisch)
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

  const hasDetails = (tatsachen.length > 0) || Boolean(proposal.reason) || Boolean(proposal.expected_effect) || Boolean(diff)

  return (
    <article className={`rounded-xl border p-3 sm:p-4 ${tone}`} aria-label={t('ai.actions.title')}>
      <div className="flex flex-wrap items-start gap-2.5 sm:gap-3">
        <span className={`rounded-lg p-2 ${proposal.autonomous ? 'bg-surface-container-highest text-primary' : 'bg-status-warning/10 text-status-warning'}`}><Icon className="h-4 w-4" /></span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-on-surface">{t(`ai.actions.tools.${proposal.tool_name}`, proposal.tool_name)}</h3>
            <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-xs text-on-surface-variant">{t(`ai.actions.status.${proposal.status}`)}</span>
            {proposal.proposal_type === 'read' && (
              <span className="rounded-full bg-secondary/15 px-2 py-0.5 text-xs text-secondary">
                {t('ai.actions.type.read', 'Lese-Zugriff')}
              </span>
            )}
            {proposal.proposal_type === 'worker' && (
              <span className="rounded-full bg-primary/15 px-2 py-0.5 text-xs text-primary">
                {t('ai.actions.type.worker', 'Worker')}
              </span>
            )}
            {proposal.autonomous && (
              <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">
                <Bot className="h-3 w-3" />
                {t('ai.actions.autonomousBadge')}
              </span>
            )}
            {proposal.status !== 'proposed' && hasDetails && (
              <button
                type="button"
                onClick={() => setDetailsOpen((prev) => !prev)}
                className="ml-auto inline-flex items-center gap-1 text-xs text-on-surface-variant hover:text-on-surface py-0.5 px-1.5 rounded transition-colors"
                aria-expanded={detailsOpen}
              >
                <span>{detailsOpen ? 'Weniger Details' : 'Details'}</span>
                <ChevronDown className={`h-3 w-3 transition-transform ${detailsOpen ? 'rotate-180' : ''}`} />
              </button>
            )}
          </div>
          {operation && <p className="mt-1 text-xs sm:text-sm text-on-surface-variant">{t('ai.actions.operation', { operation })}</p>}
          {path && <p className="mt-0.5 break-all font-mono text-[11px] sm:text-xs text-on-surface-variant">{path}</p>}
          
          {detailsOpen && (
            <div className="mt-2 space-y-2">
              {tatsachen.length > 0 && (
                <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
                  {tatsachen.map(([key, wert]) => (
                    <div key={key} className="min-w-0">
                      <dt className="text-on-surface-variant">{t(`ai.actions.fields.${key}`, key)}</dt>
                      <dd className="break-words font-medium text-on-surface">{wert}</dd>
                    </div>
                  ))}
                </dl>
              )}
              {proposal.reason && (
                <p className="text-xs sm:text-sm text-on-surface-variant">
                  <span className="font-semibold text-on-surface">{t('ai.actions.reasonLabel')}</span>{' '}
                  {proposal.reason}
                </p>
              )}
              {proposal.expected_effect && (
                <p className="text-xs sm:text-sm text-on-surface-variant">
                  <span className="font-semibold text-on-surface">{t('ai.actions.effectLabel')}</span>{' '}
                  {proposal.expected_effect}
                </p>
              )}
              {diff && <pre className="max-h-64 overflow-auto rounded-lg border border-outline-variant/40 bg-surface-container-lowest p-2.5 sm:p-3 text-[11px] sm:text-xs text-on-surface-variant">{diff}</pre>}
            </div>
          )}

          {proposal.autonomous && (
            <p className="mt-1 text-[11px] text-on-surface-variant">{t('ai.actions.autonomousHint')}</p>
          )}
          {proposal.error_code && <p className="mt-1.5 flex items-center gap-1 text-xs text-status-error"><AlertTriangle className="h-3.5 w-3.5" />{t('ai.actions.failed')}</p>}
        </div>
        {proposal.status === 'proposed' && !proposal.autonomous && (
          <div className="flex w-full sm:w-auto flex-wrap items-center gap-2 justify-end mt-2 sm:mt-0">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={busy}
              onClick={() => void reject()}
            >
              {t('ai.actions.reject', 'Ablehnen')}
            </Button>
            <Button
              type="button"
              size="sm"
              variant={proposal.tool_name === 'propose_server_lifecycle' ? 'destructive' : 'primary'}
              disabled={busy}
              onClick={() => void execute()}
            >
              {busy ? t('ai.actions.executing') : t('ai.actions.review')}
            </Button>
          </div>
        )}
      </div>
      {geheimnisse.map((geheimnis) => (
        <div key={geheimnis.label} className="mt-3">
          <SecretOnce
            label={geheimnis.label}
            value={geheimnis.value}
            onDismiss={() =>
              setGeheimnisse((rest) => rest.filter((eintrag) => eintrag.label !== geheimnis.label))
            }
          />
        </div>
      ))}
    </article>
  )
}
