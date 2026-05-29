import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Boxes, Download, RefreshCw, Trash2, Upload } from 'lucide-react'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import type { BlueprintListEntry } from '@/types'

/** Panel-Verwaltung fuer Blueprints: Liste + Aktionen (Download / Ersetzen /
 *  Loeschen). Backend-Endpunkte sind bereits vorhanden — diese Seite ist die
 *  fehlende UI-Fassade. RBAC:
 *
 *  - Liste: ``panel.settings.read`` (Route-Gate in App.tsx).
 *  - Ersetzen / Loeschen / Neu hochladen: ``panel.settings.write`` (UI-Gate
 *    hier + Backend prueft erneut). Native Blueprints sind hart geschuetzt
 *    (Backend antwortet mit 400/409); die UI versteckt die Buttons zusaetzlich.
 */
export function Blueprints() {
  const { t } = useTranslation()
  const canWrite = useHasPermission('panel.settings.write')
  const [entries, setEntries] = useState<BlueprintListEntry[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const newFileRef = useRef<HTMLInputElement | null>(null)
  const replaceFileRef = useRef<HTMLInputElement | null>(null)
  const replaceTargetIdRef = useRef<string | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await api<{ blueprints: BlueprintListEntry[] }>('/blueprints')
      setEntries(res.blueprints)
    } catch (err) {
      const message = err instanceof Error ? err.message : t('blueprints.loadFailed')
      toast.error(message)
      setEntries([])
    }
  }, [t])

  useEffect(() => {
    void load()
  }, [load])

  const uploadFile = async (file: File, expectedId: string | null) => {
    const text = await file.text()
    // Strip comments (// and /* */) safely without affecting strings (e.g. URLs)
    const strippedText = text.replace(/\\"|"(?:\\"|[^"])*"|(\/\/.*|\/\*[\s\S]*?\*\/)/g, (m, g) => g ? "" : m)
    let body: unknown
    try {
      body = JSON.parse(strippedText)
    } catch {
      toast.error(t('blueprints.uploadInvalidJson'))
      return
    }
    if (expectedId) {
      // Beim "Ersetzen" pruefen wir clientseitig, dass die hochgeladene
      // Datei tatsaechlich denselben meta.id-Eintrag traegt. Sonst wird
      // versehentlich eine neue Blueprint angelegt statt zu ueberschreiben.
      const incomingId =
        body && typeof body === 'object' && 'meta' in body
          ? ((body as { meta?: { id?: unknown } }).meta?.id ?? null)
          : null
      if (incomingId !== expectedId) {
        toast.error(t('blueprints.replaceIdMismatch', { expected: expectedId }))
        return
      }
    }
    try {
      const res = await api<{ id: string }>('/blueprints/import', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      toast.success(t('blueprints.uploadSuccess', { id: res.id }))
      await load()
    } catch (err) {
      const message = err instanceof Error ? err.message : t('blueprints.uploadFailed')
      toast.error(message)
    }
  }

  const onNewFileSelected = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setBusy('new')
    try {
      await uploadFile(file, null)
    } finally {
      setBusy(null)
    }
  }

  const onReplaceFileSelected = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    const targetId = replaceTargetIdRef.current
    replaceTargetIdRef.current = null
    if (!file || !targetId) return
    setBusy(`replace:${targetId}`)
    try {
      await uploadFile(file, targetId)
    } finally {
      setBusy(null)
    }
  }

  const handleReplace = (id: string) => {
    replaceTargetIdRef.current = id
    replaceFileRef.current?.click()
  }

  const handleDelete = async (entry: BlueprintListEntry) => {
    const ok = await confirm({
      title: t('blueprints.deleteConfirmTitle'),
      message: t('blueprints.deleteConfirmBody', { name: entry.name, id: entry.id }),
      confirmText: t('blueprints.deleteConfirm'),
      danger: true,
    })
    if (!ok) return
    setBusy(`delete:${entry.id}`)
    try {
      await api<void>(`/blueprints/${encodeURIComponent(entry.id)}`, { method: 'DELETE' })
      toast.success(t('blueprints.deleteSuccess', { id: entry.id }))
      await load()
    } catch (err) {
      const message = err instanceof Error ? err.message : t('blueprints.deleteFailed')
      toast.error(message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="container mx-auto px-4 py-8 max-w-6xl">
      <div className="flex items-center gap-3 mb-2">
        <Boxes className="w-8 h-8 text-primary" />
        <h1 className="font-headline text-display-sm font-extrabold text-on-surface">
          {t('blueprints.pageTitle')}
        </h1>
      </div>
      <p className="font-body-md text-body-md text-on-surface-variant mb-6">
        {t('blueprints.pageSubtitle')}
      </p>

      {canWrite && (
        <div className="mb-6 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => newFileRef.current?.click()}
            disabled={busy === 'new'}
            className="msm-btn-primary inline-flex items-center gap-2 px-4 py-2 disabled:opacity-50"
            data-testid="blueprints-upload-new"
          >
            {busy === 'new' ? (
              <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin" />
            ) : (
              <Upload className="w-4 h-4" />
            )}
            {t('blueprints.uploadNew')}
          </button>
          <input
            ref={newFileRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={onNewFileSelected}
            data-testid="blueprints-upload-new-input"
          />
          <input
            ref={replaceFileRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={onReplaceFileSelected}
            data-testid="blueprints-replace-input"
          />
          <span className="font-body-md text-xs text-on-surface-variant">
            {t('blueprints.uploadHint')}
          </span>
        </div>
      )}

      <div className="msm-card overflow-hidden" data-testid="blueprints-list">
        {entries === null ? (
          <div className="p-6 text-on-surface-variant">{t('blueprints.loading')}</div>
        ) : entries.length === 0 ? (
          <div className="p-6 text-on-surface-variant">{t('blueprints.empty')}</div>
        ) : (
          <table className="w-full text-left">
            <thead className="border-b border-outline-variant/30">
              <tr className="text-xs uppercase text-on-surface-variant">
                <th className="px-4 py-3">{t('blueprints.columns.name')}</th>
                <th className="px-4 py-3">{t('blueprints.columns.id')}</th>
                <th className="px-4 py-3">{t('blueprints.columns.origin')}</th>
                <th className="px-4 py-3">{t('blueprints.columns.category')}</th>
                <th className="px-4 py-3">{t('blueprints.columns.source')}</th>
                <th className="px-4 py-3 text-right">{t('blueprints.columns.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => {
                const isNative = entry.origin === 'native'
                return (
                  <tr
                    key={entry.id}
                    className="border-b border-outline-variant/10 last:border-b-0"
                    data-testid={`blueprint-row-${entry.id}`}
                  >
                    <td className="px-4 py-3 text-on-surface">{entry.name}</td>
                    <td className="px-4 py-3 font-mono text-xs text-on-surface-variant">
                      {entry.id}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={
                          isNative
                            ? 'inline-block rounded-full px-2 py-0.5 text-xs bg-primary/10 text-primary'
                            : 'inline-block rounded-full px-2 py-0.5 text-xs bg-surface-container-highest text-on-surface-variant'
                        }
                      >
                        {isNative ? t('blueprints.originNative') : t('blueprints.originCommunity')}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-on-surface-variant">{entry.category}</td>
                    <td className="px-4 py-3 text-on-surface-variant">{entry.source_type}</td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-2">
                        <a
                          href={`/api/blueprints/${encodeURIComponent(entry.id)}`}
                          download
                          className="msm-btn-secondary inline-flex items-center gap-1 px-3 py-1.5 text-xs"
                          data-testid={`blueprint-download-${entry.id}`}
                          title={t('blueprints.download')}
                        >
                          <Download className="w-3.5 h-3.5" />
                          {t('blueprints.download')}
                        </a>
                        {canWrite && !isNative && (
                          <>
                            <button
                              type="button"
                              onClick={() => handleReplace(entry.id)}
                              disabled={busy === `replace:${entry.id}`}
                              className="msm-btn-secondary inline-flex items-center gap-1 px-3 py-1.5 text-xs disabled:opacity-50"
                              data-testid={`blueprint-replace-${entry.id}`}
                              title={t('blueprints.replace')}
                            >
                              <RefreshCw className="w-3.5 h-3.5" />
                              {t('blueprints.replace')}
                            </button>
                            <button
                              type="button"
                              onClick={() => handleDelete(entry)}
                              disabled={busy === `delete:${entry.id}`}
                              className="inline-flex items-center gap-1 px-3 py-1.5 text-xs rounded-md text-status-destructive hover:bg-status-destructive/10 transition-colors disabled:opacity-50"
                              data-testid={`blueprint-delete-${entry.id}`}
                              title={t('blueprints.delete')}
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                              {t('blueprints.delete')}
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
