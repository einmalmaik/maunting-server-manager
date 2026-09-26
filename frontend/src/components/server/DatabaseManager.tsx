import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Database, Plus, Shield } from 'lucide-react'
import { api } from '@/api/client'
import { PostgresStudio } from '@/components/postgres/PostgresStudio'
import { PostgresCredentialsDialog } from '@/components/server/PostgresCredentialsDialog'
import { useHasPermission } from '@/hooks/useHasPermission'
import { prompt } from '@/stores/promptStore'
import { toast } from '@/stores/toastStore'
import type { PostgresCredential, PostgresDatabase, PostgresPowerUserCredential, PostgresResources } from '@/types'
import { ActionMenu, Badge, Button, Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle, Dropdown } from '@/Singra/UI'

interface Props {
  serverId: number
  /** Datenbankserver (eigene Instanz): Datenbanknamen frei wählbar, kein Power-User. */
  dedicated?: boolean
}

/**
 * Datenbanken eines Servers: Auswahl, Anlegen, Löschen — und darunter das
 * PostgreSQL-Studio der gewählten Datenbank.
 */
export function DatabaseManager({ serverId, dedicated = false }: Props) {
  const { t } = useTranslation()
  const canAdmin = useHasPermission('server.databases.admin', serverId)
  const [resources, setResources] = useState<PostgresResources>({ databases: [], users: [] })
  const [loaded, setLoaded] = useState(false)
  const [selectedDbId, setSelectedDbId] = useState<number | null>(null)
  const [credentials, setCredentials] = useState<PostgresCredential[]>([])
  const [powerDialog, setPowerDialog] = useState<{ db: PostgresDatabase; password: string } | null>(null)
  const [busy, setBusy] = useState(false)
  // Neu einhängen erzwingt, dass das Studio nach der Einrichtung frisch lädt.
  const [studioKey, setStudioKey] = useState(0)

  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    try {
      await action()
    } catch (err) {
      toast.error((err as Error).message || t('common.error'))
    } finally {
      setBusy(false)
    }
  }

  const fetchResources = async () => {
    const data = await api<PostgresResources>(`/servers/${serverId}/databases`)
    setResources(data)
    setLoaded(true)
    // Die gemerkte Kennung wird nicht geglaubt, sondern gegen die frische Liste
    // geprüft. Sonst bleibt nach dem Löschen die verschwundene Datenbank
    // ausgewählt und das Studio fragt weiter nach ihr.
    setSelectedDbId((current) => (data.databases.some((db) => db.id === current) ? current : data.databases[0]?.id ?? null))
  }

  useEffect(() => {
    void run(fetchResources)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverId])

  const databases = Array.isArray(resources?.databases) ? resources.databases : []
  const selected = databases.find((database) => database.id === selectedDbId) || null

  const bootstrap = () =>
    run(async () => {
      const result = await api<{ credentials: PostgresCredential[] }>(`/servers/${serverId}/databases/bootstrap`, {
        method: 'POST',
        body: JSON.stringify({ database_count: 1 }),
      })
      setCredentials(result.credentials)
      await fetchResources()
    })

  const createDatabase = () =>
    run(async () => {
      let name: string | null = null
      if (dedicated) {
        name = await prompt({ title: t('databaseManager.newDatabase'), message: t('databaseManager.newDatabaseMessage'), placeholder: 'shop' })
        if (!name) return
      }
      const result = await api<{ credential: PostgresCredential }>(`/servers/${serverId}/databases`, {
        method: 'POST',
        body: JSON.stringify({ name }),
      })
      setCredentials([result.credential])
      await fetchResources()
    })

  const deleteDatabase = () =>
    run(async () => {
      if (!selected) return
      const typed = await prompt({
        title: t('databaseManager.deleteDatabase'),
        message: t('databaseManager.deleteDatabaseMessage', { name: selected.name }),
        expectedValue: selected.name,
        confirmText: t('common.delete'),
        danger: true,
      })
      if (!typed) return
      await api(`/servers/${serverId}/databases/${selected.id}`, {
        method: 'DELETE',
        body: JSON.stringify({ confirm_name: typed }),
      })
      await fetchResources()
      toast.success(t('databaseManager.databaseDeleted'))
    })

  const bootstrapInstance = async () => {
    try {
      const result = await api<{ created: string[] }>(`/servers/${serverId}/databases/instance/bootstrap`, { method: 'POST' })
      toast.success(t('postgresStudio.connection.bootstrapDone', { count: result.created.length }))
      await fetchResources()
      setStudioKey((key) => key + 1)
    } catch (err) {
      toast.error((err as Error).message || t('common.error'))
    }
  }

  const powerAction = (kind: 'enable' | 'rotate' | 'demote') =>
    run(async () => {
      if (!selected) return
      if (kind === 'demote') {
        const typed = await prompt({
          title: t('databaseManager.powerUserDemote'),
          message: t('databaseManager.powerUserDemoteMessage', { role: selected.owner_role }),
          expectedValue: selected.owner_role,
          confirmText: t('databaseManager.powerUserDemote'),
          danger: true,
        })
        if (!typed) return
        await api(`/servers/${serverId}/databases/power-user/demote`, {
          method: 'DELETE',
          body: JSON.stringify({ database_id: selected.id, username: selected.owner_role, confirm_name: typed }),
        })
        await fetchResources()
        return
      }
      const endpoint = kind === 'enable' ? 'power-user' : 'power-user/rotate'
      const credential = await api<PostgresPowerUserCredential>(`/servers/${serverId}/databases/${endpoint}`, {
        method: 'POST',
        body: JSON.stringify({ database_id: selected.id }),
      })
      setPowerDialog({ db: selected, password: credential.password })
      await fetchResources()
    })

  if (loaded && databases.length === 0) {
    return (
      <div className="msm-card p-8 text-center">
        <Database className="mx-auto h-8 w-8 text-on-surface-variant" />
        <h3 className="mt-3 font-headline text-xl text-on-surface">{t('databaseManager.emptyTitle')}</h3>
        <p className="mt-2 text-sm text-on-surface-variant">{t('databaseManager.emptyHint')}</p>
        {canAdmin && (
          <Button className="mt-5" onClick={() => void bootstrap()} disabled={busy}>
            <Plus className="h-4 w-4" />
            {t('databaseManager.createDatabase')}
          </Button>
        )}
        <PostgresCredentialsDialog credentials={credentials} onClose={() => setCredentials([])} />
      </div>
    )
  }

  const adminItems = canAdmin
    ? [
        { key: 'create', label: t('databaseManager.newDatabase'), onSelect: () => void createDatabase() },
        ...(!dedicated && selected && !selected.is_power_user
          ? [{ key: 'power', label: t('databaseManager.powerUserEnable'), onSelect: () => void powerAction('enable') }]
          : []),
        ...(!dedicated && selected?.is_power_user
          ? [
              { key: 'power-rotate', label: t('databaseManager.powerUserRotate'), onSelect: () => void powerAction('rotate') },
              { key: 'power-demote', label: t('databaseManager.powerUserDemote'), onSelect: () => void powerAction('demote') },
            ]
          : []),
        { key: 'delete', label: t('databaseManager.deleteDatabase'), destructive: true, separatorBefore: true, disabled: !selected, onSelect: () => void deleteDatabase() },
      ]
    : []

  return (
    <div className="space-y-4">
      <div className="msm-card flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="flex min-w-0 items-center gap-3">
          <Database className="h-5 w-5 text-secondary" />
          <div className="min-w-0">
            <h2 className="font-headline text-lg font-semibold text-on-surface">{t('databaseManager.title')}</h2>
            <p className="text-xs text-on-surface-variant">{dedicated ? t('databaseManager.subtitleDedicated') : t('databaseManager.subtitleShared')}</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Dropdown
            value={selectedDbId != null ? String(selectedDbId) : null}
            onChange={(value) => setSelectedDbId(Number(value))}
            options={databases.map((database) => ({ value: String(database.id), label: database.name, hint: database.owner_role }))}
            className="w-64"
            aria-label={t('databaseManager.pickDatabase')}
            data-testid="database-switcher"
          />
          {selected?.is_power_user && !dedicated && (
            <Badge variant="warning">
              <Shield className="mr-1 h-3 w-3" />
              {t('databaseManager.powerUserActive')}
            </Badge>
          )}
          {adminItems.length > 0 && <ActionMenu label={t('databaseManager.actions')} items={adminItems} disabled={busy} />}
        </div>
      </div>

      {selected && (
        <PostgresStudio
          key={`${selected.id}:${studioKey}`}
          serverId={serverId}
          databaseId={selected.id}
          databaseName={selected.name}
          onResourcesChanged={() => void run(fetchResources)}
          onBootstrap={dedicated && canAdmin ? bootstrapInstance : undefined}
        />
      )}
      <PostgresCredentialsDialog credentials={credentials} onClose={() => setCredentials([])} />
      <PowerUserDialog state={powerDialog} onClose={() => setPowerDialog(null)} />
    </div>
  )
}

function PowerUserDialog({ state, onClose }: { state: { db: PostgresDatabase; password: string } | null; onClose: () => void }) {
  const { t } = useTranslation()
  return (
    <Dialog open={Boolean(state)} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('databaseManager.powerUserTitle')}</DialogTitle>
        </DialogHeader>
        {state && (
          <div className="space-y-3 p-6">
            <p className="rounded-lg border border-status-warning/40 bg-status-warning/10 p-3 text-sm text-status-warning">{t('databaseManager.powerUserWarning')}</p>
            <div className="space-y-2 font-mono text-sm text-on-surface">
              <div>database: {state.db.name}</div>
              <div>username: {state.db.owner_role}</div>
              <div className="break-all rounded bg-status-destructive/10 p-2 text-status-destructive">password: {state.password}</div>
            </div>
            <p className="text-xs text-on-surface-variant">{t('databaseManager.powerUserHub')}</p>
          </div>
        )}
        <DialogFooter>
          <Button onClick={onClose}>{t('common.close')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
