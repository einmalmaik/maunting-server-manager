import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import i18n from '@/i18n'
import { useAuthStore } from '@/stores/authStore'
import { BenutzernameInline } from './BenutzernameInline'

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, api: vi.fn() }
})

const t = (key: string) => i18n.t(key)

/**
 * Die eine Stelle zum Ändern: der Name neben dem Profilbild, im Panel wie in
 * der App. Ein Klick auf den Stift macht ihn zum Feld, Speichern schließt es.
 */
describe('BenutzernameInline', () => {
  beforeEach(() => {
    vi.mocked(api).mockReset()
    useAuthStore.setState({ user: { id: 1, username: 'alt_name', username_gewaehlt: true } as any })
  })

  it('zeigt den Namen mit Stift und speichert an Ort und Stelle', async () => {
    vi.mocked(api).mockResolvedValue({ id: 1, username: 'neu_name', username_gewaehlt: true } as any)
    render(<BenutzernameInline />)

    expect(screen.getByText('alt_name')).toBeInTheDocument()
    expect(screen.queryByLabelText(t('benutzername.label'))).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: t('benutzername.aendern') }))
    const feld = screen.getByLabelText(t('benutzername.label')) as HTMLInputElement
    expect(feld.value).toBe('alt_name')
    // Unverändert gibt es nichts zu speichern.
    expect(screen.getByRole('button', { name: t('common.save') })).toBeDisabled()

    fireEvent.change(feld, { target: { value: 'neu_name' } })
    fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(screen.getByText('neu_name')).toBeInTheDocument())
    expect(screen.queryByLabelText(t('benutzername.label'))).not.toBeInTheDocument()
    expect(api).toHaveBeenCalledWith('/auth/me/username', {
      method: 'PATCH',
      body: JSON.stringify({ username: 'neu_name' }),
    })
    expect(useAuthStore.getState().user?.username).toBe('neu_name')
  })

  it('zeigt den Fehler unter der Zeile und schickt nichts ab', () => {
    render(<BenutzernameInline />)
    fireEvent.click(screen.getByRole('button', { name: t('benutzername.aendern') }))
    fireEvent.change(screen.getByLabelText(t('benutzername.label')), { target: { value: 'max@web.de' } })

    expect(screen.getByText(t('benutzername.fehlerForm'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('common.save') })).toBeDisabled()
    expect(api).not.toHaveBeenCalled()
  })

  it('Abbrechen lässt den Namen stehen', () => {
    render(<BenutzernameInline />)
    fireEvent.click(screen.getByRole('button', { name: t('benutzername.aendern') }))
    fireEvent.change(screen.getByLabelText(t('benutzername.label')), { target: { value: 'anders' } })
    fireEvent.click(screen.getByRole('button', { name: t('common.cancel') }))

    expect(screen.getByText('alt_name')).toBeInTheDocument()
    expect(api).not.toHaveBeenCalled()
  })
})
