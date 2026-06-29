import { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Activity,
  CheckCircle2,
  Copy,
  Loader2,
  Plus,
  Power,
  RotateCw,
  Send,
  Trash2,
  Webhook,
  XCircle,
} from 'lucide-react';
import { api } from '@/api/client';
import { toast } from '@/stores/toastStore';
import { confirm } from '@/stores/confirmStore';

interface WebhookSub {
  id: number;
  label: string | null;
  target_url: string;
  secret_hint: string | null;
  enabled: boolean;
  event_filter: string | null;
  last_delivery_status: string | null;
  last_delivery_at: string | null;
  last_response_code: number | null;
}

interface WebhookSubWithSecret extends WebhookSub {
  secret: string;
}

interface WebhookDelivery {
  id: number;
  subscription_id: number;
  event_type: string;
  payload: Record<string, unknown>;
  payload_hash: string;
  status: string;
  response_code: number | null;
  error: string | null;
  attempt: number;
  sent_at: string;
}

const KNOWN_EVENT_TYPES = [
  { value: 'status_change', label: 'Status-Änderung (start/stop)' },
  { value: 'player_update', label: 'Spielerzahl-Update' },
  { value: 'error', label: 'Server-Fehler' },
];

export function OutgoingWebhooksPanel({ serverId }: { serverId: number }) {
  const { t } = useTranslation();
  const [subs, setSubs] = useState<WebhookSub[]>([]);
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [revealedSecrets, setRevealedSecrets] = useState<Record<number, string>>({});
  const [testPending, setTestPending] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, d] = await Promise.all([
        api<{ items: WebhookSub[] }>(`/servers/${serverId}/webhooks`),
        api<{ items: WebhookDelivery[] }>(`/servers/${serverId}/webhooks/deliveries?limit=20`),
      ]);
      setSubs(s.items);
      setDeliveries(d.items);
    } catch {
      /* tab empty */
    } finally {
      setLoading(false);
    }
  }, [serverId]);

  useEffect(() => {
    void refresh();
    const h = setInterval(refresh, 5000);
    return () => clearInterval(h);
  }, [refresh]);

  const copyToClipboard = (text: string, label: string) => {
    void navigator.clipboard.writeText(text);
    toast.success(t('webhook.copied', { defaultValue: `${label} kopiert` }));
  };

  const handleCreate = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const targetUrl = String(fd.get('target_url') || '');
    const label = String(fd.get('label') || '') || null;
    const eventFilter = String(fd.get('event_filter') || '') || null;
    setBusy('create');
    try {
      const r = await api<WebhookSubWithSecret>(`/servers/${serverId}/webhooks`, {
        method: 'POST',
        body: JSON.stringify({ target_url: targetUrl, label, event_filter: eventFilter }),
      });
      setRevealedSecrets((p) => ({ ...p, [r.id]: r.secret }));
      setShowCreate(false);
      toast.success(t('webhook.created', { defaultValue: 'Webhook angelegt' }));
      await refresh();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const handleRotate = async (subId: number) => {
    const ok = await confirm({
      title: t('webhook.rotateTitle', { defaultValue: 'Secret rotieren?' }),
      message: t('webhook.rotateMsg', {
        defaultValue:
          'Das neue Secret wird einmalig angezeigt. Du musst es im Empfaenger (Bot, Monitor) aktualisieren — sonst empfängt der nichts mehr.',
      }),
      confirmText: t('webhook.rotate', { defaultValue: 'Rotieren' }),
      danger: true,
    });
    if (!ok) return;
    setBusy(`rotate-${subId}`);
    try {
      const r = await api<WebhookSubWithSecret>(
        `/servers/${serverId}/webhooks/${subId}/rotate`,
        { method: 'POST' },
      );
      setRevealedSecrets((p) => ({ ...p, [subId]: r.secret }));
      toast.success(t('webhook.rotated', { defaultValue: 'Secret rotiert' }));
      await refresh();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const handleToggleEnabled = async (sub: WebhookSub) => {
    setBusy(`toggle-${sub.id}`);
    try {
      await api(`/servers/${serverId}/webhooks/${sub.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ enabled: !sub.enabled }),
      });
      await refresh();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const handleDelete = async (sub: WebhookSub) => {
    const ok = await confirm({
      title: t('webhook.deleteTitle', { defaultValue: 'Webhook löschen?' }),
      message: t('webhook.deleteMsg', {
        defaultValue:
          'Die Subscription wird entfernt. Eingehende Events werden danach nicht mehr an diese URL gesendet.',
      }),
      confirmText: t('common.delete', { defaultValue: 'Löschen' }),
      danger: true,
    });
    if (!ok) return;
    setBusy(`delete-${sub.id}`);
    try {
      await api(`/servers/${serverId}/webhooks/${sub.id}`, { method: 'DELETE' });
      toast.success(t('webhook.deleted', { defaultValue: 'Webhook gelöscht' }));
      await refresh();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  const handleTest = async (subId: number) => {
    setTestPending(subId);
    try {
      const r = await api<{ delivery_id: number; queued: boolean }>(
        `/servers/${serverId}/webhooks/${subId}/test`,
        { method: 'POST', body: JSON.stringify({ event_type: 'status_change' }) },
      );
      if (r.queued) {
        toast.success(
          t('webhook.testQueued', {
            defaultValue: 'Test-Event versendet — Antwort erscheint im Live-Feed',
          }),
        );
      } else {
        toast.error(t('webhook.testFailed', { defaultValue: 'Test konnte nicht versendet werden' }));
      }
      setTimeout(refresh, 1500);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : String(err));
    } finally {
      setTestPending(null);
    }
  };

  if (loading) {
    return (
      <div className="msm-card p-6 text-on-surface-variant">
        {t('common.loading', { defaultValue: 'Lädt …' })}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="msm-card p-5">
        <div className="flex items-start gap-3 mb-4">
          <Webhook className="w-6 h-6 text-primary mt-0.5" />
          <div className="flex-1">
            <h3 className="font-headline text-headline-sm text-primary">
              {t('webhook.outTitle', { defaultValue: 'Webhooks an Drittsysteme' })}
            </h3>
            <p className="text-body-sm text-on-surface-variant mt-1">
              {t('webhook.outSubtitle', {
                defaultValue:
                  'MSM schickt Status- und Spielerzahl-Updates aktiv an eine URL, die du hier hinterlegst — z. B. ein Discord-Bot, der den Server-Status anzeigt.',
              })}
            </p>
          </div>
          <button
            type="button"
            className="msm-btn-primary flex items-center gap-2"
            onClick={() => setShowCreate((p) => !p)}
          >
            <Plus className="w-4 h-4" />
            {t('webhook.add', { defaultValue: 'Webhook hinzufügen' })}
          </button>
        </div>

        {showCreate && (
          <form
            onSubmit={handleCreate}
            className="msm-card border border-outline/30 p-4 mt-3 space-y-3"
          >
            <div>
              <label className="block text-label-md text-on-surface-variant mb-1">
                {t('webhook.label', { defaultValue: 'Bezeichnung (optional)' })}
              </label>
              <input
                type="text"
                name="label"
                className="msm-input w-full"
                placeholder="Discord-Bot #1"
              />
            </div>
            <div>
              <label className="block text-label-md text-on-surface-variant mb-1">
                {t('webhook.targetUrl', { defaultValue: 'Ziel-URL' })}
              </label>
              <input
                type="url"
                name="target_url"
                className="msm-input w-full font-mono text-body-sm"
                placeholder="http://localhost:5173/api/webhooks/server-panel/…?secret=…"
                required
              />
              <p className="text-body-xs text-on-surface-variant mt-1">
                {t('webhook.targetUrlHelp', {
                  defaultValue:
                    'Die URL bekommst du vom Empfaengersystem (Bot-Anbieter, Monitor-Setup usw.).',
                })}
              </p>
            </div>
            <div>
              <label className="block text-label-md text-on-surface-variant mb-1">
                {t('webhook.filter', {
                  defaultValue: 'Event-Filter (leer = alle)',
                })}
              </label>
              <select name="event_filter" className="msm-input w-full">
                <option value="">
                  {t('webhook.filterAll', { defaultValue: 'Alle Events' })}
                </option>
                {KNOWN_EVENT_TYPES.map((ev) => (
                  <option key={ev.value} value={ev.value}>
                    {ev.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex gap-2 justify-end pt-2">
              <button
                type="button"
                className="msm-btn-secondary"
                onClick={() => setShowCreate(false)}
              >
                {t('common.cancel', { defaultValue: 'Abbrechen' })}
              </button>
              <button
                type="submit"
                className="msm-btn-primary"
                disabled={busy === 'create'}
              >
                {busy === 'create'
                  ? t('common.working', { defaultValue: 'Bitte warten …' })
                  : t('common.save', { defaultValue: 'Anlegen' })}
              </button>
            </div>
          </form>
        )}
      </div>

      {/* Bestehende Subscriptions */}
      {subs.length === 0 && !showCreate && (
        <div className="msm-card p-6 text-center text-on-surface-variant">
          {t('webhook.empty', {
            defaultValue: 'Noch keine Webhooks konfiguriert. Klicke oben auf "Webhook hinzufügen", um einen anzulegen.',
          })}
        </div>
      )}

      {subs.map((sub) => (
        <div key={sub.id} className="msm-card p-5 space-y-3">
          <div className="flex items-center gap-3">
            <span
              className={`msm-badge ${
                sub.enabled
                  ? 'bg-status-success/15 text-status-success'
                  : 'bg-surface-container-highest text-on-surface-variant'
              }`}
            >
              {sub.enabled
                ? t('webhook.active', { defaultValue: 'Aktiv' })
                : t('webhook.inactive', { defaultValue: 'Pausiert' })}
            </span>
            <span className="font-medium text-title-sm text-on-surface">
              {sub.label || `Webhook #${sub.id}`}
            </span>
            <DeliveryBadge status={sub.last_delivery_status} code={sub.last_response_code} />
          </div>

          <div>
            <label className="block text-label-md text-on-surface-variant mb-1">
              {t('webhook.targetUrl', { defaultValue: 'Ziel-URL' })}
            </label>
            <div className="flex items-stretch gap-2">
              <code className="msm-input flex-1 font-mono text-body-sm overflow-x-auto whitespace-nowrap">
                {sub.target_url}
              </code>
              <button
                type="button"
                className="msm-btn-secondary flex items-center gap-1"
                onClick={() => copyToClipboard(sub.target_url, 'URL')}
              >
                <Copy className="w-4 h-4" />
                {t('common.copy', { defaultValue: 'Kopieren' })}
              </button>
            </div>
          </div>

          <div className="flex items-center gap-4 text-body-sm text-on-surface-variant">
            <span>
              {t('webhook.currentSecretHint', { defaultValue: 'Aktives Secret endet auf' })}:{' '}
              <code className="px-1.5 py-0.5 rounded bg-surface-container-highest font-mono">
                {sub.secret_hint || '****'}
              </code>
            </span>
            {sub.event_filter && (
              <span>
                {t('webhook.filterOnly', { defaultValue: 'Filter' })}:{' '}
                <code className="px-1.5 py-0.5 rounded bg-surface-container-highest font-mono">
                  {sub.event_filter}
                </code>
              </span>
            )}
            {sub.last_delivery_at && (
              <span className="ml-auto">
                {t('webhook.lastDelivery', { defaultValue: 'Letzte Zustellung' })}:{' '}
                {new Date(sub.last_delivery_at).toLocaleString()}
              </span>
            )}
          </div>

          {revealedSecrets[sub.id] && (
            <div className="msm-card border border-status-warning/40 p-3 bg-status-warning/5">
              <div className="flex items-center justify-between mb-2">
                <span className="text-label-md text-status-warning font-medium">
                  {t('webhook.secretNew', { defaultValue: 'Neues Webhook-Secret' })}
                </span>
                <button
                  type="button"
                  className="msm-btn-secondary flex items-center gap-1"
                  onClick={() =>
                    copyToClipboard(revealedSecrets[sub.id], 'Secret')
                  }
                >
                  <Copy className="w-4 h-4" />
                  {t('common.copy', { defaultValue: 'Kopieren' })}
                </button>
              </div>
              <code className="msm-input block font-mono text-body-sm break-all">
                {revealedSecrets[sub.id]}
              </code>
              <p className="text-body-xs text-on-surface-variant mt-2">
                {t('webhook.secretSetup', {
                  defaultValue:
                    'Trage dieses Secret im Empfaengersystem ein. Es wird als X-Webhook-Secret-Header mitgesendet und dort verifiziert.',
                })}
              </p>
            </div>
          )}

          <div className="flex flex-wrap gap-2 pt-2 border-t border-outline/30">
            <button
              type="button"
              className="msm-btn-secondary flex items-center gap-2"
              onClick={() => void handleTest(sub.id)}
              disabled={testPending === sub.id || !sub.enabled}
              title={
                sub.enabled
                  ? undefined
                  : t('webhook.testHintDisabled', {
                      defaultValue: 'Webhook muss aktiv sein für Test-Send',
                    })
              }
            >
              {testPending === sub.id ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
              {t('webhook.test', { defaultValue: 'Test senden' })}
            </button>
            <button
              type="button"
              className="msm-btn-secondary flex items-center gap-2"
              onClick={() => void handleRotate(sub.id)}
              disabled={busy === `rotate-${sub.id}`}
            >
              <RotateCw className="w-4 h-4" />
              {t('webhook.rotate', { defaultValue: 'Secret rotieren' })}
            </button>
            <button
              type="button"
              className="msm-btn-secondary flex items-center gap-2"
              onClick={() => void handleToggleEnabled(sub)}
              disabled={busy === `toggle-${sub.id}`}
            >
              <Power className="w-4 h-4" />
              {sub.enabled
                ? t('webhook.pause', { defaultValue: 'Pausieren' })
                : t('webhook.resume', { defaultValue: 'Fortsetzen' })}
            </button>
            <button
              type="button"
              className="msm-btn-danger flex items-center gap-2 ml-auto"
              onClick={() => void handleDelete(sub)}
              disabled={busy === `delete-${sub.id}`}
            >
              <Trash2 className="w-4 h-4" />
              {t('common.delete', { defaultValue: 'Löschen' })}
            </button>
          </div>
        </div>
      ))}

      {/* Live-Feed */}
      <div className="msm-card p-5">
        <div className="flex items-center gap-2 mb-3">
          <Activity className="w-5 h-5 text-primary" />
          <h4 className="font-headline text-title-sm text-primary">
            {t('webhook.feedTitle', { defaultValue: 'Zustell-Feed' })}
          </h4>
          <span className="text-body-xs text-on-surface-variant ml-auto">
            {t('webhook.feedAuto', { defaultValue: 'Auto-Refresh alle 5s' })}
          </span>
        </div>
        {deliveries.length === 0 ? (
          <p className="text-body-sm text-on-surface-variant">
            {t('webhook.feedEmpty', {
              defaultValue:
                'Noch keine Zustellungen. Wenn der Server startet/stoppt oder ein Spielerzahl-Update eintrifft, erscheinen sie hier.',
            })}
          </p>
        ) : (
          <ul className="space-y-2">
            {deliveries.map((d) => (
              <li
                key={d.id}
                className="border border-outline/30 rounded-lg p-3 bg-surface-container-lowest"
              >
                <div className="flex items-center gap-2 mb-1">
                  <DeliveryBadge status={d.status} code={d.response_code} />
                  <span className="msm-badge bg-primary/10 text-primary font-mono">
                    {d.event_type}
                  </span>
                  <span className="text-body-xs text-on-surface-variant">
                    #{d.id}
                  </span>
                  <span className="text-body-xs text-on-surface-variant ml-auto">
                    {new Date(d.sent_at).toLocaleTimeString()}
                    {d.attempt > 1 && (
                      <span className="ml-2">
                        ({t('webhook.attempt', { defaultValue: 'Versuch' })} {d.attempt})
                      </span>
                    )}
                  </span>
                </div>
                {d.error && (
                  <p className="text-body-xs text-status-error mb-1">{d.error}</p>
                )}
                <details className="text-body-xs">
                  <summary className="cursor-pointer text-on-surface-variant hover:text-primary">
                    {t('webhook.feedPayload', { defaultValue: 'Payload anzeigen' })}
                  </summary>
                  <pre className="mt-2 p-2 bg-surface-container-highest rounded font-mono overflow-x-auto">
                    {JSON.stringify(d.payload, null, 2)}
                  </pre>
                </details>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function DeliveryBadge({
  status,
  code,
}: {
  status: string | null;
  code: number | null;
}) {
  if (status === 'ok') {
    return (
      <span className="msm-badge bg-status-success/15 text-status-success flex items-center gap-1">
        <CheckCircle2 className="w-3 h-3" />
        {code ?? ''}
      </span>
    );
  }
  if (status === 'failed') {
    return (
      <span className="msm-badge bg-status-error/15 text-status-error flex items-center gap-1">
        <XCircle className="w-3 h-3" />
        {code ?? 'fail'}
      </span>
    );
  }
  if (status === 'pending') {
    return (
      <span className="msm-badge bg-surface-container-highest text-on-surface-variant flex items-center gap-1">
        <Loader2 className="w-3 h-3 animate-spin" />
        …
      </span>
    );
  }
  return <span className="msm-badge bg-surface-container-highest text-on-surface-variant">—</span>;
}
