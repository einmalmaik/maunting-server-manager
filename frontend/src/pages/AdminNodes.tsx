/**
 * AdminNodes — owner-only node registry UI (Phase 3 multi-node).
 * Uses existing msm-card / msm-btn / msm-input patterns + Singra ProgressBar.
 * Never displays agent tokens (API does not return them).
 */
import { useEffect, useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Activity,
  HardDrive,
  Network,
  Plus,
  RefreshCw,
  Server,
  Trash2,
  Pencil,
} from 'lucide-react'
import { useNodeStore } from '@/stores/nodeStore'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { Badge } from '@/components/ui/Badge'
import { PasswordInput } from '@/components/ui/PasswordInput'
import { ProgressBar } from '@/Singra/UI/ProgressBar'
import { NodeEnrollmentDialog } from '@/components/nodes/NodeEnrollmentDialog'
import type { Node } from '@/types'
import { api } from '@/api/client'
import { useHasPermission } from '@/hooks/useHasPermission'

function statusVariant(status: string): 'success' | 'destructive' | 'default' | 'warning' {
  switch (status) {
    case 'online':
      return 'success'
    case 'offline':
      return 'destructive'
    case 'unknown':
      return 'default'
    default:
      return 'warning'
  }
}

function formatRamMb(mb: number | null | undefined): string {
  if (mb == null) return '—'
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`
  return `${Math.round(mb)} MB`
}

function cpuPercent(node: Node): number | null {
  const m = node.metrics
  if (m?.cpu_percent != null) return m.cpu_percent
  return null
}

function ramPercent(node: Node): number | null {
  const m = node.metrics
  if (m?.ram_percent != null) return m.ram_percent
  if (m?.ram_used_bytes != null && m?.ram_total_bytes) {
    return (m.ram_used_bytes / m.ram_total_bytes) * 100
  }
  return null
}

function freeRamMb(node: Node): number | null {
  const m = node.metrics
  if (m?.ram_total_bytes != null && m?.ram_used_bytes != null) {
    return Math.max(0, Math.round((m.ram_total_bytes - m.ram_used_bytes) / (1024 * 1024)))
  }
  // Capacity alone is not free RAM — do not mislabel totals as free.
  return null
}

export function AdminNodes() {
  const { t } = useTranslation()
  const { nodes, total, loading, fetchNodes, createNode, updateNode, deleteNode, healthCheck } =
    useNodeStore()
  const canManageNodes = useHasPermission('nodes.manage')
  const [showForm, setShowForm] = useState(false)
  const [showEnrollment, setShowEnrollment] = useState(false)
  const [editing, setEditing] = useState<Node | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [updateAvailable, setUpdateAvailable] = useState(false)
  const [updatingNodes, setUpdatingNodes] = useState(false)
  const [form, setForm] = useState({
    name: '',
    host: '',
    auth_token: '',
    tls_fingerprint: '',
  })

  const [searchVal, setSearchVal] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [currentPage, setCurrentPage] = useState(1)
  const [pageSize] = useState(25)

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(searchVal)
      setCurrentPage(1)
    }, 400)
    return () => clearTimeout(timer)
  }, [searchVal])

  const load = useCallback(() => {
    void fetchNodes(currentPage, pageSize, debouncedSearch).catch((err: unknown) => {
      toast.error(err instanceof Error ? err.message : t('nodes.loadFailed'))
    })
  }, [fetchNodes, currentPage, pageSize, debouncedSearch, t])

  useEffect(() => {
    api<{ update_available: boolean }>('/system/update/status')
      .then((data) => {
        setUpdateAvailable(data.update_available)
      })
      .catch(() => {})
  }, [])

  const handleUpdateNodes = async () => {
    setUpdatingNodes(true)
    try {
      const res = await api<{ message: string }>('/system/update/nodes', { method: 'POST' })
      toast.success(res.message || 'Node-Updates gestartet')
      await fetchNodes(currentPage, pageSize, debouncedSearch)
    } catch (err: any) {
      toast.error(err.message || 'Node-Update fehlgeschlagen')
    } finally {
      setUpdatingNodes(false)
    }
  }

  useEffect(() => {
    load()
    const id = window.setInterval(load, 20_000)
    return () => window.clearInterval(id)
  }, [load])

  const openEnrollment = () => {
    setShowForm(false)
    setEditing(null)
    setShowEnrollment(true)
  }

  const openManualCreate = () => {
    setEditing(null)
    setForm({ name: '', host: 'https://', auth_token: '', tls_fingerprint: '' })
    setShowEnrollment(false)
    setShowForm(true)
  }

  const openEdit = (node: Node) => {
    setEditing(node)
    setForm({
      name: node.name,
      host: node.host,
      auth_token: '',
      tls_fingerprint: node.tls_fingerprint || '',
    })
    setShowForm(true)
  }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      if (editing) {
        const payload: {
          name?: string
          host?: string
          auth_token?: string
          tls_fingerprint?: string
        } = {
          name: form.name.trim(),
          host: form.host.trim(),
        }
        if (form.auth_token.trim()) {
          payload.auth_token = form.auth_token.trim()
        }
        if (form.tls_fingerprint.trim()) {
          payload.tls_fingerprint = form.tls_fingerprint.trim()
        }
        await updateNode(editing.id, payload)
        toast.success(t('nodes.updated'))
      } else {
        if (form.auth_token.trim().length < 16) {
          toast.error(t('nodes.tokenTooShort'))
          return
        }
        if (!form.tls_fingerprint.trim()) {
          toast.error(t('nodes.fingerprintRequired'))
          return
        }
        await createNode({
          name: form.name.trim(),
          host: form.host.trim(),
          auth_token: form.auth_token.trim(),
          tls_fingerprint: form.tls_fingerprint.trim(),
        })
        toast.success(t('nodes.created'))
      }
      setShowForm(false)
      setForm({ name: '', host: '', auth_token: '', tls_fingerprint: '' })
      setEditing(null)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('common.error'))
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (node: Node) => {
    if (node.is_local) {
      toast.error(t('nodes.cannotDeleteLocal'))
      return
    }
    if (node.server_count > 0) {
      toast.error(t('nodes.cannotDeleteWithServers', { count: node.server_count }))
      return
    }
    if (
      !(await confirm({
        message: t('nodes.confirmDelete', { name: node.name }),
        danger: true,
        confirmText: t('common.delete'),
      }))
    ) {
      return
    }
    try {
      await deleteNode(node.id)
      toast.success(t('nodes.deleted'))
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('common.error'))
    }
  }

  const handleHealth = async (node: Node) => {
    setBusyId(node.id)
    try {
      const fresh = await healthCheck(node.id)
      toast.success(
        fresh.status === 'online' ? t('nodes.healthOnline') : t('nodes.healthOffline'),
      )
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('nodes.healthFailed'))
    } finally {
      setBusyId(null)
    }
  }

  if (loading && nodes.length === 0) {
    return (
      <div className="flex h-64 items-center justify-center">
        <span className="h-6 w-6 animate-spin rounded-full border-2 border-secondary border-t-transparent" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-headline text-headline-sm text-primary">{t('nav.nodes')}</h1>
          <p className="mt-1 font-body-md text-body-md text-on-surface-variant">
            {t('nodes.subtitle')}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {canManageNodes && updateAvailable && (
            <button
              type="button"
              disabled={updatingNodes}
              onClick={handleUpdateNodes}
              className="msm-btn-secondary inline-flex items-center gap-2 px-4 py-2 disabled:opacity-60"
            >
              <RefreshCw className={`h-4 w-4 ${updatingNodes ? 'animate-spin' : ''}`} />
              {updatingNodes ? t('nodes.updating', 'Updating Nodes...') : t('nodes.updateAll', 'Nodes updaten')}
            </button>
          )}
          {canManageNodes && (
            <button
              type="button"
              onClick={openEnrollment}
              className="msm-btn-primary inline-flex items-center gap-2 px-4 py-2"
            >
              <Plus className="h-4 w-4" />
              {t('nodes.add')}
            </button>
          )}
        </div>
      </div>
      <div className="flex items-center gap-4 bg-surface-variant/20 p-4 rounded-xl">
        <div className="relative flex-1 max-w-sm">
          <input
            type="text"
            value={searchVal}
            onChange={(e) => setSearchVal(e.target.value)}
            placeholder={t('nodes.searchPlaceholder', { defaultValue: 'Nodes filtern...' })}
            className="w-full bg-surface border border-surface-variant-outline rounded-lg py-2 px-3 pl-9 text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all"
          />
          <svg
            className="absolute left-3 top-2.5 h-4 w-4 text-on-surface-variant"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="2"
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
        </div>
      </div>

      {showEnrollment && (
        <NodeEnrollmentDialog
          onClose={() => setShowEnrollment(false)}
          onManualSetup={openManualCreate}
          onApproved={() => fetchNodes(currentPage, pageSize, debouncedSearch)}
        />
      )}

      {showForm && (
        <div className="msm-card p-6">
          <h3 className="mb-4 font-headline text-body-lg text-primary">
            {editing ? t('nodes.edit') : t('nodes.add')}
          </h3>
          <form onSubmit={handleSave} className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div>
              <label className="mb-1.5 block font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
                {t('nodes.name')}
              </label>
              <input
                className="msm-input"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                required
                maxLength={100}
                autoComplete="off"
              />
            </div>
            <div>
              <label className="mb-1.5 block font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
                {t('nodes.host')}
              </label>
              <input
                className="msm-input"
                value={form.host}
                onChange={(e) => setForm({ ...form, host: e.target.value })}
                required
                placeholder="https://10.0.0.5:9000"
                maxLength={255}
                autoComplete="off"
              />
            </div>
            <div className="md:col-span-2">
              <label className="mb-1.5 block font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
                {t('nodes.tlsFingerprint')}
              </label>
              <input
                className="msm-input font-mono text-sm"
                value={form.tls_fingerprint}
                onChange={(e) => setForm({ ...form, tls_fingerprint: e.target.value })}
                required={!editing}
                placeholder={t('nodes.fingerprintPlaceholder')}
                maxLength={128}
                autoComplete="off"
                spellCheck={false}
              />
              <p className="mt-1 text-xs text-on-surface-variant">{t('nodes.fingerprintHint')}</p>
            </div>
            <div className="md:col-span-2">
              <label className="mb-1.5 block font-label-md text-label-md uppercase tracking-wider text-on-surface-variant">
                {t('nodes.agentToken')}
              </label>
              <PasswordInput
                value={form.auth_token}
                onChange={(e) => setForm({ ...form, auth_token: e.target.value })}
                required={!editing}
                minLength={editing ? undefined : 16}
                placeholder={editing ? t('nodes.tokenLeaveBlank') : undefined}
                autoComplete="new-password"
              />
              <p className="mt-1 font-body-md text-xs text-on-surface-variant">
                {t('nodes.tokenHint')}
              </p>
            </div>
            <div className="flex gap-2 md:col-span-2">
              <button type="submit" className="msm-btn-primary px-4 py-2" disabled={saving}>
                {saving ? t('common.loading') : t('common.save')}
              </button>
              <button
                type="button"
                className="msm-btn-secondary px-4 py-2"
                onClick={() => {
                  setShowForm(false)
                  setEditing(null)
                }}
              >
                {t('common.cancel')}
              </button>
            </div>
          </form>
        </div>
      )}

      {nodes.length === 0 ? (
        <div className="msm-card border-2 border-dashed border-outline-variant p-12 text-center">
          <Network className="mx-auto mb-4 h-10 w-10 text-on-surface-variant" />
          <h3 className="mb-1 font-headline text-body-lg text-on-surface">{t('nodes.empty')}</h3>
          <p className="font-body-md text-sm text-on-surface-variant">{t('nodes.emptyHint')}</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {nodes.map((node) => {
            const cpu = cpuPercent(node)
            const ram = ramPercent(node)
            return (
              <div key={node.id} className="msm-card p-5" data-testid={`node-card-${node.id}`}>
                <div className="mb-3 flex items-start justify-between gap-2">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="font-headline text-body-md text-on-surface">{node.name}</h3>
                      <Badge variant={statusVariant(node.status)}>
                        {t(`nodes.status.${node.status}`, { defaultValue: node.status })}
                      </Badge>
                      {node.is_local && (
                        <Badge variant="info">{t('nodes.local')}</Badge>
                      )}
                    </div>
                    <p className="mt-1 break-all font-mono-sm text-mono-sm text-on-surface-variant">
                      {node.host}
                    </p>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <button
                      type="button"
                      className="msm-btn-secondary p-2"
                      title={t('nodes.healthCheck')}
                      onClick={() => void handleHealth(node)}
                      disabled={busyId === node.id}
                    >
                      <RefreshCw
                        className={`h-4 w-4 ${busyId === node.id ? 'animate-spin' : ''}`}
                      />
                    </button>
                    {canManageNodes && (
                      <>
                        <button
                          type="button"
                          className="msm-btn-secondary p-2"
                          title={t('nodes.edit')}
                          onClick={() => openEdit(node)}
                        >
                          <Pencil className="h-4 w-4" />
                        </button>
                        <button
                          type="button"
                          className="msm-btn-secondary p-2 text-status-error disabled:opacity-40"
                          title={t('common.delete')}
                          disabled={node.is_local || node.server_count > 0}
                          onClick={() => void handleDelete(node)}
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </>
                    )}
                  </div>
                </div>

                <div className="mb-4 space-y-3">
                  <ProgressBar
                    value={cpu}
                    label="CPU"
                    hint={cpu != null ? `${cpu.toFixed(0)}%` : '—'}
                    heat
                    data-testid={`node-cpu-${node.id}`}
                  />
                  <ProgressBar
                    value={ram}
                    label="RAM"
                    hint={
                      ram != null
                        ? `${ram.toFixed(0)}% · ${t('nodes.freeRam', { value: formatRamMb(freeRamMb(node)) })}`
                        : node.ram_total != null
                          ? t('nodes.ramCapacity', {
                              value: formatRamMb(node.ram_total),
                              defaultValue: `Kapazität ${formatRamMb(node.ram_total)}`,
                            })
                          : '—'
                    }
                    heat
                    data-testid={`node-ram-${node.id}`}
                  />
                </div>

                <div className="flex flex-wrap gap-4 text-xs text-on-surface-variant">
                  <span className="inline-flex items-center gap-1.5">
                    <Server className="h-3.5 w-3.5" />
                    {t('nodes.serverCount', { count: node.server_count })}
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <Activity className="h-3.5 w-3.5" />
                    {node.cpu_total != null
                      ? t('nodes.cpuCores', { count: node.cpu_total })
                      : 'CPU —'}
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <HardDrive className="h-3.5 w-3.5" />
                    {node.disk_total != null
                      ? t('nodes.diskTotal', { value: formatRamMb(node.disk_total) })
                      : 'Disk —'}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {Math.ceil(total / pageSize) > 1 && (
        <div className="mt-8 flex items-center justify-between border-t border-surface-variant pt-4 text-sm text-on-surface-variant">
          <div>
            Zeige {(currentPage - 1) * pageSize + 1} bis {Math.min(currentPage * pageSize, total)} von {total} Nodes
          </div>
          <div className="flex items-center gap-2">
            <button
              disabled={currentPage <= 1}
              onClick={() => setCurrentPage((p) => p - 1)}
              className="msm-btn-secondary px-3 py-1.5 text-xs disabled:opacity-50"
            >
              Zurück
            </button>
            <span className="font-medium px-2">
              Seite {currentPage} von {Math.ceil(total / pageSize)}
            </span>
            <button
              disabled={currentPage >= Math.ceil(total / pageSize)}
              onClick={() => setCurrentPage((p) => p + 1)}
              className="msm-btn-secondary px-3 py-1.5 text-xs disabled:opacity-50"
            >
              Weiter
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
