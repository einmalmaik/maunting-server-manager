/**
 * Tresorspeicher je Rolle, aufgebaut wie das KI-Kontingent: eine Rolle wählen,
 * Wert eintragen, speichern. Ein Konto bekommt den höchsten Wert seiner Rollen;
 * ohne Rolle mit Speicher lädt es nichts hoch. Das setzt das Backend durch
 * (`vault_blob_service.quote_fuer`), diese Ansicht stellt nur ein.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { HardDrive, Save } from 'lucide-react'

import { api, SanitizedApiError } from '@/api/client'
import { useHasPermission } from '@/hooks/useHasPermission'
import { Button, Dropdown, NumberStepper } from '@/Singra/UI'
import { Spinner } from '@/components/ui/Spinner'
import { toast } from '@/stores/toastStore'

const GIB = 1024 ** 3
/** Wie `vault_blob_service.MAX_QUOTE` im Backend (1 PiB). */
const MAX_GB = 1024 ** 2

export interface RollenSpeicher {
  role_id: number
  role_name: string
  quota_bytes: number | null
}

export function VaultStorageTab() {
  const { t } = useTranslation()
  const canRead = useHasPermission('panel.settings.read')
  const canWrite = useHasPermission('panel.settings.write')
  const [rollen, setRollen] = useState<RollenSpeicher[]>([])
  const [rolleId, setRolleId] = useState<number | null>(null)
  const [gb, setGb] = useState(0)
  const [laden, setLaden] = useState(canRead)
  const [speichern, setSpeichern] = useState(false)

  useEffect(() => {
    if (!canRead) {
      setLaden(false)
      return
    }
    let aktiv = true
    api<RollenSpeicher[]>('/settings/tresor-speicher')
      .then((liste) => {
        if (!aktiv) return
        setRollen(liste)
        const erste = liste.find((r) => r.quota_bytes !== null) ?? liste[0]
        setRolleId(erste?.role_id ?? null)
        setGb(erste?.quota_bytes ? Math.round(erste.quota_bytes / GIB) : 0)
      })
      .catch((error: unknown) => {
        if (aktiv) toast.error(error instanceof SanitizedApiError ? error.message : t('vaultStorage.loadFailed'))
      })
      .finally(() => {
        if (aktiv) setLaden(false)
      })
    return () => {
      aktiv = false
    }
  }, [canRead, t])

  const rolle = rollen.find((r) => r.role_id === rolleId) ?? null

  const waehlen = (id: number) => {
    setRolleId(id)
    const neu = rollen.find((r) => r.role_id === id)
    setGb(neu?.quota_bytes ? Math.round(neu.quota_bytes / GIB) : 0)
  }

  const setzen = async (quotaBytes: number | null) => {
    if (!rolle || !canWrite || speichern) return
    setSpeichern(true)
    try {
      await api(`/settings/tresor-speicher/${rolle.role_id}`, {
        method: 'PUT',
        body: JSON.stringify({ quota_bytes: quotaBytes }),
      })
      setRollen((alt) => alt.map((r) => (r.role_id === rolle.role_id ? { ...r, quota_bytes: quotaBytes } : r)))
      if (quotaBytes === null) setGb(0)
      toast.success(t('vaultStorage.saved'))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('vaultStorage.saveFailed'))
    } finally {
      setSpeichern(false)
    }
  }

  if (!canRead) {
    return <div className="msm-card p-6 text-sm text-on-surface-variant">{t('vaultStorage.noPermission')}</div>
  }
  if (laden) {
    return <div className="flex h-64 items-center justify-center"><Spinner size="lg" className="text-primary" /></div>
  }

  return (
    <div className="space-y-6">
      <div className="msm-card p-6">
        <div className="mb-3 flex items-center gap-2">
          <HardDrive className="h-5 w-5 text-primary" aria-hidden="true" />
          <h3 className="font-headline text-title-lg font-semibold text-on-surface">{t('vaultStorage.title')}</h3>
        </div>
        <p className="max-w-3xl text-sm text-on-surface-variant">{t('vaultStorage.description')}</p>
      </div>

      {rollen.length === 0 && <div className="msm-card p-6 text-sm text-on-surface-variant">{t('vaultStorage.noRoles')}</div>}

      {rolle && (
        <section className="msm-card space-y-5 p-6" aria-labelledby="vault-storage-role">
          <label className="block w-full max-w-sm space-y-1.5">
            <span id="vault-storage-role" className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('vaultStorage.selectRole')}
            </span>
            <Dropdown
              value={String(rolle.role_id)}
              onChange={(wert) => waehlen(Number(wert))}
              options={rollen.map((r) => ({
                value: String(r.role_id),
                label: r.role_name,
                hint: r.quota_bytes === null ? t('vaultStorage.none') : `${Math.round(r.quota_bytes / GIB)} GB`,
              }))}
              disabled={speichern}
              aria-label={t('vaultStorage.selectRole')}
            />
          </label>

          <div className="max-w-sm space-y-2">
            <label htmlFor="vault-storage-gb" className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              {t('vaultStorage.gb')}
            </label>
            <NumberStepper
              id="vault-storage-gb"
              min={0}
              max={MAX_GB}
              step={1}
              value={gb}
              disabled={!canWrite || speichern}
              onValueChange={(roh) => {
                const zahl = Number(roh)
                if (Number.isInteger(zahl) && zahl >= 0 && zahl <= MAX_GB) setGb(zahl)
              }}
              aria-label={`${t('vaultStorage.gb')}: ${rolle.role_name}`}
            />
            {rolle.quota_bytes === null && <p className="text-xs text-on-surface-variant">{t('vaultStorage.noneHint')}</p>}
          </div>

          {canWrite && (
            <div className="flex flex-wrap gap-2">
              <Button type="button" disabled={speichern} onClick={() => void setzen(gb * GIB)}>
                <Save className="h-4 w-4" aria-hidden="true" />
                {t('settings.save')}
              </Button>
              {rolle.quota_bytes !== null && (
                <Button type="button" variant="ghost" disabled={speichern} onClick={() => void setzen(null)}>
                  {t('vaultStorage.remove')}
                </Button>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  )
}
