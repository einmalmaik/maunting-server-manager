import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Profile } from './Profile'
import i18n from '@/i18n'

vi.mock('@/api/client', () => ({ api: vi.fn() }))
vi.mock('@/hooks/useHasPermission', () => ({ useHasPermission: () => true }))

function renderProfile() {
  return render(<MemoryRouter><Profile /></MemoryRouter>)
}

describe('Profile', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en')
  })

  it('trägt dieselbe Seitenhülle wie /settings: msm-page plus PageHeader', () => {
    const { container } = renderProfile()

    // Ohne msm-page verliert die Seite die Breitenbegrenzung aller anderen Panelseiten.
    expect(container.querySelector('.msm-page')).not.toBeNull()

    const header = screen.getByRole('banner')
    expect(within(header).getByRole('heading', { level: 1 })).toHaveTextContent('Profile')
    expect(within(header).getByText('Panel')).toBeInTheDocument()
    expect(within(header).getByText('Your Account')).toBeInTheDocument()
  })

  it('zeigt den aktiven Tab als Status-Badge im Kopf', () => {
    renderProfile()

    const badge = within(screen.getByRole('banner')).getByText('Account')
    expect(badge).toHaveClass('msm-badge-info')
  })
})
