/**
 * Zugangsdaten-Bereich im Server-Detail (Zielpunkt 17.4).
 *
 * Zeigt verstaendlich, welche Zugangsdaten dieser Server braucht, woher sie
 * gerade kommen und wie der Benutzer eine eigene Zuordnung setzt. Geheimnisse
 * werden nie angezeigt — nur Bezeichnung, Benutzername und ein Hinweis auf die
 * letzten Zeichen.
 */
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { KeyRound } from 'lucide-react'

import {
  credentialsApi,
  type CredentialKind,
  type ServerCredentialStatus,
  type UserCredential,
} from '@/api/credentials'
import { SanitizedApiError } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { Dropdown } from '@/components/ui/Dropdown'

export function ServerCredentialsPanel({
  serverId,
  canManage,
}: {
  serverId: number
  canManage: boolean
}) {
  const { t } = useTranslation()
  const [rows, setRows] = useState<ServerCredentialStatus[]>([])
  const [mine, setMine] = useState<UserCredential[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const [statuses, own] = await Promise.all([
        credentialsApi.listForServer(serverId),
        // Ohne Verwaltungsrecht braucht die Auswahlliste gar nicht geladen zu
        // werden — angezeigt wird dann nur der aktuelle Zustand.
        canManage ? credentialsApi.listMine() : Promise.resolve([] as UserCredential[]),
      ])
      // Defensiv: dieser Bereich haengt im Server-Detail. Eine unerwartete
      // Antwort darf die gesamte Serverseite nicht mitreissen.
      setRows(Array.isArray(statuses) ? statuses : [])
      setMine(Array.isArray(own) ? own : [])
    } catch (error: unknown) {
      toast.error(
        error instanceof SanitizedApiError ? error.message : t('credentials.errors.load'),
      )
    } finally {
      setLoading(false)
    }
  }, [serverId, canManage, t])

  useEffect(() => {
    void load()
  }, [load])

  const bind = async (kind: CredentialKind, credentialId: number | null) => {
    if (!canManage || busy) return
    setBusy(true)
    try {
      setRows(await credentialsApi.bind(serverId, kind, credentialId))
      toast.success(t('credentials.bound'))
    } catch (error: unknown) {
      toast.error(
        error instanceof SanitizedApiError ? error.message : t('credentials.errors.bind'),
      )
    } finally {
      setBusy(false)
    }
  }

  // Nur anzeigen, was dieser Server laut Blueprint braucht oder bereits nutzt.
  const relevant = rows.filter((row) => row.required || row.source === 'server')
  if (loading || relevant.length === 0) return null

  return (
    <section className="msm-card space-y-4 p-6" aria-labelledby="server-credentials-title">
      <div className="flex items-center gap-2">
        <KeyRound className="h-5 w-5 text-secondary" aria-hidden="true" />
        <h3
          id="server-credentials-title"
          className="font-headline text-base font-semibold text-on-surface"
        >
          {t('credentials.serverTitle')}
        </h3>
      </div>
      <p className="text-sm text-on-surface-variant">{t('credentials.serverDescription')}</p>

      <ul className="space-y-3">
        {relevant.map((row) => {
          const options = mine.filter((item) => item.kind === row.kind)
          return (
            <li
              key={row.kind}
              className="rounded-xl border border-outline-variant/40 p-4"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium text-on-surface">
                  {t(`credentials.kinds.${row.kind}`)}
                </span>
                <span
                  className={
                    row.configured
                      ? 'msm-badge-info text-xs'
                      : 'rounded-full bg-status-warning/10 px-2 py-0.5 text-xs text-status-warning'
                  }
                >
                  {t(`credentials.sources.${row.source}`)}
                </span>
              </div>
              {!row.configured && (
                <p className="mt-2 text-xs text-status-warning">
                  {t('credentials.missing')}
                </p>
              )}
              {/*
                Bezeichnung, Benutzername und Hinweis gehoeren dem Benutzer,
                dessen Zugangsdaten gebunden sind. Wer nur zuschauen darf,
                bekommt sie vom Backend gar nicht mehr — ohne die zusaetzliche
                Bedingung stuende hier ein leerer Absatz. Die Herkunft oben
                (das Badge) bleibt sichtbar, sie ist die eigentliche Auskunft.
              */}
              {row.configured && row.source === 'server' && row.label !== null && (
                <p className="mt-2 text-xs text-on-surface-variant">
                  {row.label}
                  {row.username ? ` · ${row.username}` : ''}
                  {row.hint ? ` · ${row.hint}` : ''}
                </p>
              )}

              {canManage && (
                <div className="mt-3 block space-y-1.5">
                  <span className="block text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
                    {t('credentials.assign')}
                  </span>
                  <Dropdown
                    disabled={busy}
                    value={row.credential_id ? String(row.credential_id) : ''}
                    onChange={(val) => void bind(row.kind, val === '' ? null : Number(val))}
                    options={[
                      { value: '', label: t('credentials.usePanelDefault') },
                      ...options.map((item) => ({
                        value: String(item.id),
                        label: item.label + (item.username ? ` (${item.username})` : ''),
                      })),
                    ]}
                    aria-label={t('credentials.assign')}
                  />
                  {options.length === 0 && (
                    <span className="block text-xs text-on-surface-variant">
                      {t('credentials.noneStored')}
                    </span>
                  )}
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
