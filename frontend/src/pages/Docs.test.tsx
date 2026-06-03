import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Docs } from './Docs'
import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { useToastStore } from '@/stores/toastStore'

function renderIndex() {
  return render(
    <MemoryRouter>
      <Docs />
    </MemoryRouter>,
  )
}

describe('Docs index page', () => {
  beforeEach(() => {
    i18n.changeLanguage('en')
    usePermissionsStore.setState({ me: null, isLoading: false })
    useToastStore.setState({ toasts: [] })
  })

  it('renders the English index headline', async () => {
    renderIndex()
    expect(await screen.findByText('Help & Documentation')).toBeInTheDocument()
  })

  it('links to the Blueprints sub-docs', () => {
    renderIndex()
    const link = screen.getByRole('link', { name: /open blueprint docs/i })
    expect(link.getAttribute('href')).toBe('/docs/blueprints')
  })

  it('links to the OAuth sub-docs', () => {
    renderIndex()
    const link = screen.getByRole('link', { name: /open oauth docs/i })
    expect(link.getAttribute('href')).toBe('/docs/oauth')
  })

  it('renders German index headline after language switch', async () => {
    await i18n.changeLanguage('de')
    renderIndex()
    expect(await screen.findByText('Hilfe & Dokumentation')).toBeInTheDocument()
    await i18n.changeLanguage('en')
  })
})
