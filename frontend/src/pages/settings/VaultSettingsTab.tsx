import { useState, useEffect } from 'react'
import { Check, KeyRound, RefreshCw, Server, ShieldCheck } from 'lucide-react'
import { Button, Dropdown, type DropdownOption } from '@/Singra/UI'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { useNodeStore } from '@/stores/nodeStore'
import { useHasPermission } from '@/hooks/useHasPermission'

interface VaultNodeAssignmentResponse {
  node_id: string | null
  assigned_node_name: string | null
  is_multi_node_active: boolean
  migrated_entries?: number
}

export function VaultSettingsTab() {
  const canWrite = useHasPermission('panel.settings.write')
  const { nodes, fetchNodes } = useNodeStore()

  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [selectedNodeId, setSelectedNodeId] = useState<string>('')
  const [currentAssignment, setCurrentAssignment] = useState<VaultNodeAssignmentResponse | null>(null)

  useEffect(() => {
    void fetchNodes()
    void loadAssignment()
  }, [])

  const loadAssignment = async () => {
    setLoading(true)
    try {
      const res = await api<VaultNodeAssignmentResponse>('/api/vault/node-assignment')
      setCurrentAssignment(res)
      setSelectedNodeId(res.node_id || '')
    } catch {
      toast.error('Konnte Node-Zuweisung nicht laden')
    } finally {
      setLoading(false)
    }
  }

  const handleSave = async () => {
    const isChangingNode = (selectedNodeId || null) !== (currentAssignment?.node_id || null)
    if (isChangingNode) {
      const targetName = selectedNodeId
        ? (nodes.find((n) => String(n.id) === selectedNodeId)?.name || `Node #${selectedNodeId}`)
        : 'Zentraler Panel-Node'
      const confirmed = window.confirm(
        `Möchtest du den Passwort-Manager wirklich auf "${targetName}" umziehen? Sämtliche verschlüsselten Tresor-Datensätze werden dabei automatisch und unterbrechungsfrei migriert.`
      )
      if (!confirmed) return
    }

    setSaving(true)
    try {
      const nodeIdToSend = selectedNodeId ? selectedNodeId : null
      const res = await api<VaultNodeAssignmentResponse>('/api/vault/node-assignment', {
        method: 'PUT',
        body: JSON.stringify({ node_id: nodeIdToSend }),
      })
      setCurrentAssignment(res)
      setSelectedNodeId(res.node_id || '')
      if (res.migrated_entries && res.migrated_entries > 0) {
        toast.success(`Node-Zuweisung aktualisiert. ${res.migrated_entries} Tresor-Datensätze nahtlos migriert.`)
      } else {
        toast.success('Node-Zuweisung für Passwort-Manager gespeichert')
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Speichern fehlgeschlagen')
    } finally {
      setSaving(false)
    }
  }

  // Dropdown Optionen vorbereiten (keine nativen <select>!)
  const nodeOptions: DropdownOption[] = [
    { value: '', label: 'Zentraler Panel-Node (Standard)' },
    ...nodes.map((n) => ({
      value: String(n.id),
      label: `${n.name} (${n.host}) ${n.status === 'online' ? '• Online' : '• Offline'}`,
    })),
  ]

  const assignedNode = nodes.find((n) => String(n.id) === selectedNodeId)

  return (
    <div className="space-y-6 max-w-4xl">
      {/* HEADER KARTE */}
      <div className="msm-card p-6 space-y-4">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary border border-primary/20">
              <KeyRound className="h-5 w-5" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-on-surface">
                Passwort-Manager & Authenticator — Multi-Node
              </h3>
              <p className="text-xs text-on-surface-variant">
                Weise den Zero-Knowledge Speicher und Synchronisationsdienst einem dedizierten Node zu.
              </p>
            </div>
          </div>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void loadAssignment()}
            className="flex items-center gap-1.5"
            disabled={loading}
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            <span>Aktualisieren</span>
          </Button>
        </div>

        <div className="rounded-xl border border-outline-variant/30 bg-surface-container-low p-3.5 text-xs text-on-surface-variant flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-primary shrink-0" />
            <span className="text-on-surface font-medium">Zero-Knowledge Verschlüsselung via DIS</span>
            <span className="hidden sm:inline text-on-surface-variant">• Native Desktop- & Mobile-App</span>
          </div>
          {currentAssignment && (
            <span className="msm-badge-info text-[11px] shrink-0">
              {currentAssignment.assigned_node_name || 'Zentraler Panel-Node'}
            </span>
          )}
        </div>
      </div>

      {/* NODE ZUWEISUNG */}
      <div className="msm-card p-6 space-y-6">
        <div className="space-y-1">
          <h4 className="text-sm font-semibold text-on-surface flex items-center gap-2">
            <Server className="h-4 w-4 text-primary" />
            <span>Dedizierte Node-Zuweisung</span>
          </h4>
          <p className="text-xs text-on-surface-variant">
            Wähle den Node aus deinem Multi-Node Verbund, der die verschlüsselten Tresor-Blobs und Synchronisations-Anfragen ausführen soll.
          </p>
        </div>

        <div className="space-y-4 max-w-lg">
          <div>
            <label className="block text-xs font-medium text-on-surface-variant mb-1.5">
              Zuständiger Node
            </label>
            <Dropdown
              options={nodeOptions}
              value={selectedNodeId}
              onChange={(val) => setSelectedNodeId(val)}
              placeholder="Node auswählen..."
              disabled={!canWrite || loading}
            />
          </div>

          {assignedNode && (
            <div className="rounded-xl border border-outline-variant/30 bg-surface-container p-3 text-xs space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="font-medium text-on-surface">{assignedNode.name}</span>
                <span
                  className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${
                    assignedNode.status === 'online'
                      ? 'bg-status-success/10 text-status-success'
                      : 'bg-status-error/10 text-status-error'
                  }`}
                >
                  {assignedNode.status}
                </span>
              </div>
              <div className="text-[11px] text-on-surface-variant font-mono">
                Host: {assignedNode.host}
              </div>
            </div>
          )}

          {canWrite && (
            <div className="pt-2">
              <Button
                size="sm"
                onClick={() => void handleSave()}
                disabled={saving || loading}
                className="flex items-center gap-1.5"
              >
                {saving ? (
                  <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Check className="h-3.5 w-3.5" />
                )}
                <span>Zuweisung speichern</span>
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
