import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Docs } from './Docs'
import i18n from '@/i18n'
import { usePermissionsStore } from '@/stores/permissionsStore'
import { useToastStore } from '@/stores/toastStore'
import type { MePermissions } from '@/types/permissions'

function renderDocs() {
  return render(
    <MemoryRouter>
      <Docs />
    </MemoryRouter>,
  )
}

function setMe(me: MePermissions | null) {
  usePermissionsStore.setState({ me, isLoading: false })
}

const ownerMe: MePermissions = {
  is_owner: true,
  role_id: null,
  role_name: null,
  global_keys: [],
  server_keys: {},
}

describe('Docs page', () => {
  beforeEach(() => {
    i18n.changeLanguage('en')
    setMe(null)
    useToastStore.setState({ toasts: [] })
  })

  it('renders English headline and template link with correct href', async () => {
    renderDocs()
    expect(await screen.findByText('Blueprint Documentation')).toBeInTheDocument()
    const link = screen.getByTestId('docs-template-download') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/api/blueprints/template')
  })

  it('renders all expected sections (including the two examples)', async () => {
    renderDocs()
    for (const key of [
      'intro',
      'workflow',
      'addServer',
      'addBlueprintSteam',
      'addBlueprintCustom',
      'location',
      'schema',
      'runtime',
      'ports',
      'source',
      'httpSecurity',
      'mods',
      'import',
      'exampleMinecraftVersion',
      'exampleWine',
    ]) {
      expect(screen.getByTestId(`docs-section-${key}`)).toBeInTheDocument()
    }
  })

  it('renders the TOC sidebar with anchor links per section', () => {
    renderDocs()
    expect(screen.getByTestId('docs-toc')).toBeInTheDocument()
    const intro = screen.getByTestId('docs-toc-intro') as HTMLAnchorElement
    expect(intro.getAttribute('href')).toBe('#docs-intro')
    const mcEx = screen.getByTestId('docs-toc-exampleMinecraftVersion') as HTMLAnchorElement
    expect(mcEx.getAttribute('href')).toBe('#docs-exampleMinecraftVersion')
  })

  it('renders the Minecraft and Wine example JSON code blocks', () => {
    renderDocs()
    const mc = screen.getByTestId('docs-section-exampleMinecraftVersion')
    expect(mc.textContent).toContain('"id": "minecraft_paper_1_20_4"')
    expect(mc.textContent).toContain('"VERSION": "1.20.4"')
    const wine = screen.getByTestId('docs-section-exampleWine')
    expect(wine.textContent).toContain('"id": "my_windows_server"')
    expect(wine.textContent).toContain('WINEPREFIX')
  })

  it('links to the Blueprints page for upload / replace / delete', () => {
    renderDocs()
    const link = screen.getByTestId('docs-link-blueprints') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('/blueprints')
  })

  it('no longer renders the upload button on the Docs page', () => {
    setMe(ownerMe)
    renderDocs()
    expect(screen.queryByTestId('docs-blueprint-upload')).toBeNull()
    expect(screen.queryByTestId('docs-blueprint-upload-input')).toBeNull()
  })

  it('renders German headline after language switch', async () => {
    await i18n.changeLanguage('de')
    renderDocs()
    expect(await screen.findByText('Blueprint-Dokumentation')).toBeInTheDocument()
    await i18n.changeLanguage('en')
  })
})
