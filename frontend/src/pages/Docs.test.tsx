import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Docs } from './Docs'
import i18n from '@/i18n'

function renderDocs() {
  return render(
    <MemoryRouter>
      <Docs />
    </MemoryRouter>,
  )
}

describe('Docs page', () => {
  beforeEach(() => {
    i18n.changeLanguage('en')
  })

  it('renders English headline and template link with correct href', async () => {
    renderDocs()
    expect(await screen.findByText('Blueprint Documentation')).toBeInTheDocument()
    const link = screen.getByTestId('docs-template-download') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/api/blueprints/template')
  })

  it('renders all expected sections', async () => {
    renderDocs()
    for (const key of [
      'intro',
      'location',
      'schema',
      'runtime',
      'ports',
      'source',
      'httpSecurity',
      'mods',
      'import',
    ]) {
      expect(screen.getByTestId(`docs-section-${key}`)).toBeInTheDocument()
    }
  })

  it('renders German headline after language switch', async () => {
    await i18n.changeLanguage('de')
    renderDocs()
    expect(await screen.findByText('Blueprint-Dokumentation')).toBeInTheDocument()
    await i18n.changeLanguage('en')
  })
})
