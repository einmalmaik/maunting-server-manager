import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Profile } from './Profile'
import i18n from '@/i18n'
import { usePublicSettingsStore } from '@/stores/publicSettingsStore'

vi.mock('@/api/client', () => ({ api: vi.fn() }))
vi.mock('@/hooks/useHasPermission', async () => {
  const { useState } = await import('react')
  // Ein Hook-Ersatz muss selbst ein Hook sein (AGENTS.md, Punkt 26).
  return { useHasPermission: () => useState(true)[0] }
})
vi.mock('./profile/AccountTab', () => ({ AccountTab: () => <div>inhalt:konto</div> }))
vi.mock('./profile/DatenexportKarte', () => ({ DatenexportKarte: () => <div>inhalt:export</div> }))
vi.mock('./profile/PasswordTab', () => ({ PasswordTab: () => <div>inhalt:passwort</div> }))
vi.mock('./profile/TwoFactorTab', () => ({ TwoFactorTab: () => <div>inhalt:2fa</div> }))
vi.mock('./profile/MessengerSicherheitTab', () => ({ MessengerSicherheitTab: () => <div>inhalt:messenger</div> }))
vi.mock('./profile/LinkedAccountsTab', () => ({ LinkedAccountsTab: () => <div>inhalt:verknuepft</div> }))
vi.mock('./profile/CredentialsTab', () => ({ CredentialsTab: () => <div>inhalt:zugangsdaten</div> }))
vi.mock('./profile/DevicesTab', () => ({ DevicesTab: () => <div>inhalt:geraete</div> }))
vi.mock('./profile/SocialTab', () => ({ SocialTab: () => <div>inhalt:freunde</div> }))
vi.mock('./profile/AudioTab', () => ({ AudioTab: () => <div>inhalt:audio</div> }))
vi.mock('./profile/AiTab', () => ({ AiTab: () => <div>inhalt:ki</div> }))
vi.mock('./profile/DangerZoneTab', () => ({ DangerZoneTab: () => <div>inhalt:gefahr</div> }))

let suche = ''
function Adresse() {
  suche = useLocation().search
  return null
}

function renderProfile(pfad = '/profile') {
  return render(
    <MemoryRouter initialEntries={[pfad]}>
      <Profile />
      <Adresse />
    </MemoryRouter>,
  )
}

describe('Profile', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en')
    usePublicSettingsStore.setState({ social_enabled: true })
  })

  it('trägt dieselbe Seitenhülle wie /settings: msm-page plus PageHeader', () => {
    const { container } = renderProfile()

    // Ohne msm-page verliert die Seite die Breitenbegrenzung aller anderen Panelseiten.
    expect(container.querySelector('.msm-page')).not.toBeNull()

    const header = screen.getByRole('banner')
    expect(within(header).getByRole('heading', { level: 1 })).toHaveTextContent('Profile')
    expect(within(header).getByText('Panel')).toBeInTheDocument()
    expect(within(header).getByText('Your account')).toBeInTheDocument()
  })

  it('zeigt den aktiven Tab als Status-Badge im Kopf', () => {
    renderProfile()

    const badge = within(screen.getByRole('banner')).getByText('Account')
    expect(badge).toHaveClass('msm-badge-info')
  })

  it('hat acht Reiter und den Export unter Konto', () => {
    renderProfile()
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Account', 'Security', 'Devices', 'Friends & milestones', 'Connections', 'Audio & video', 'AI', 'Danger zone',
    ])
    expect(screen.getByText('inhalt:konto')).toBeInTheDocument()
    expect(screen.getByText('inhalt:export')).toBeInTheDocument()
  })

  it.each([
    ['2fa', 'Security', ['inhalt:passwort', 'inhalt:2fa', 'inhalt:messenger']],
    ['password', 'Security', ['inhalt:passwort']],
    ['messenger', 'Security', ['inhalt:messenger']],
    ['linked', 'Connections', ['inhalt:verknuepft', 'inhalt:zugangsdaten']],
    ['credentials', 'Connections', ['inhalt:zugangsdaten']],
  ])('alter Link ?tab=%s landet unter %s', (alt, reiter, inhalte) => {
    renderProfile(`/profile?tab=${alt}`)
    expect(screen.getByRole('tab', { name: reiter })).toHaveAttribute('aria-selected', 'true')
    for (const inhalt of inhalte) expect(screen.getByText(inhalt)).toBeInTheDocument()
  })

  it('ohne Social kein Freunde-Reiter und kein Messenger-PIN; der Link fällt auf Konto', () => {
    usePublicSettingsStore.setState({ social_enabled: false })
    renderProfile('/profile?tab=social')
    expect(screen.queryByRole('tab', { name: 'Friends & milestones' })).toBeNull()
    expect(screen.getByRole('tab', { name: 'Account' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(screen.getByRole('tab', { name: 'Security' }))
    expect(screen.queryByText('inhalt:messenger')).toBeNull()
  })

  it('ein Klick auf einen Reiter schreibt ihn in die Adresse', () => {
    renderProfile()
    fireEvent.click(screen.getByRole('tab', { name: 'Connections' }))
    expect(suche).toBe('?tab=connections')
    expect(screen.getByText('inhalt:verknuepft')).toBeInTheDocument()
  })
})
