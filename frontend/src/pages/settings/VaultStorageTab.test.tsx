import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as client from '@/api/client'
import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import type { MePermissions } from '@/types/permissions'
import { VaultStorageTab, type RollenSpeicher } from './VaultStorageTab'

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: vi.fn(),
}))

const api = vi.mocked(client.api)
const GIB = 1024 ** 3

function rechte(global_keys: string[]): MePermissions {
  return { is_owner: false, role_id: null, role_name: null, global_keys, server_keys: {} }
}

describe('VaultStorageTab', () => {
  let liste: RollenSpeicher[]

  beforeEach(async () => {
    await i18n.changeLanguage('de')
    api.mockReset()
    liste = [
      { role_id: 1, role_name: 'admin', quota_bytes: null },
      { role_id: 5, role_name: 'kunde', quota_bytes: 20 * GIB },
    ]
    api.mockImplementation(((pfad: string) =>
      Promise.resolve(pfad === '/settings/tresor-speicher' ? liste : { ok: true })) as unknown as typeof client.api)
  })

  it('zeigt zuerst die Rolle mit Speicher und speichert den Wert in Bytes', async () => {
    usePermissionsStore.setState({ me: rechte(['panel.settings.read', 'panel.settings.write']), isLoading: false, error: null })
    render(<VaultStorageTab />)

    const feld = await screen.findByRole('textbox', { name: /kunde/ })
    expect(feld).toHaveValue('20')
    fireEvent.change(feld, { target: { value: '50' } })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.save') }))

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith('/settings/tresor-speicher/5', {
        method: 'PUT',
        body: JSON.stringify({ quota_bytes: 50 * GIB }),
      }),
    )
  })

  it('beschriftet in GiB, wie gerechnet wird, und verfälscht keinen krummen Wert', async () => {
    liste[1].quota_bytes = 1.5 * GIB
    usePermissionsStore.setState({ me: rechte(['panel.settings.read', 'panel.settings.write']), isLoading: false, error: null })
    render(<VaultStorageTab />)

    const feld = await screen.findByLabelText(/GiB/)
    expect(feld).toHaveValue('2')
    expect(screen.getByText(i18n.t('vaultStorage.exactHint', { wert: '1,5 GiB' }))).toBeInTheDocument()

    // Unverändert gespeichert bleibt es genau 1,5 GiB, nicht die gerundeten 2.
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.save') }))
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith('/settings/tresor-speicher/5', { method: 'PUT', body: JSON.stringify({ quota_bytes: 1.5 * GIB }) }),
    )

    fireEvent.change(feld, { target: { value: '3' } })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('settings.save') }))
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith('/settings/tresor-speicher/5', { method: 'PUT', body: JSON.stringify({ quota_bytes: 3 * GIB }) }),
    )
  })

  it('erklärt, dass 0 GiB und „Wert entfernen“ zwei Wege mit gleicher Wirkung sind', async () => {
    usePermissionsStore.setState({ me: rechte(['panel.settings.read', 'panel.settings.write']), isLoading: false, error: null })
    render(<VaultStorageTab />)
    expect(await screen.findByText(i18n.t('vaultStorage.zeroOrRemove'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: i18n.t('vaultStorage.remove') })).toBeInTheDocument()
  })

  it('nimmt einer Rolle den Speicher mit null', async () => {
    usePermissionsStore.setState({ me: rechte(['panel.settings.read', 'panel.settings.write']), isLoading: false, error: null })
    render(<VaultStorageTab />)

    fireEvent.click(await screen.findByRole('button', { name: i18n.t('vaultStorage.remove') }))
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith('/settings/tresor-speicher/5', {
        method: 'PUT',
        body: JSON.stringify({ quota_bytes: null }),
      }),
    )
    expect(await screen.findByText(i18n.t('vaultStorage.noneHint'))).toBeInTheDocument()
  })

  it('zeigt ohne Schreibrecht keinen Speichern-Knopf und ohne Leserecht nichts', async () => {
    usePermissionsStore.setState({ me: rechte(['panel.settings.read']), isLoading: false, error: null })
    const { unmount } = render(<VaultStorageTab />)
    expect(await screen.findByRole('textbox', { name: /kunde/ })).toBeDisabled()
    expect(screen.queryByRole('button', { name: i18n.t('settings.save') })).toBeNull()
    unmount()

    usePermissionsStore.setState({ me: rechte([]), isLoading: false, error: null })
    render(<VaultStorageTab />)
    expect(screen.getByText(i18n.t('vaultStorage.noPermission'))).toBeInTheDocument()
    expect(api).toHaveBeenCalledTimes(1)
  })
})
