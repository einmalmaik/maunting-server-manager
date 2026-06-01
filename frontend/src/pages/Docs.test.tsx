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
    expect(link.getAttribute('href')).toBe('/api/blueprints/template?lang=en')
  })

  it('renders expected new structured sections', async () => {
    const { container } = renderDocs()
    for (const key of ['intro', 'quickstart', 'minimal', 'reference', 'howto', 'troubleshooting']) {
      expect(container.querySelector(`#docs-${key}`)).toBeInTheDocument()
    }
  })

  it('renders the TOC sidebar with anchor links', () => {
    renderDocs()
    const links = screen.getAllByRole('link')
    const introLink = links.find(l => l.getAttribute('href') === '#docs-intro')
    expect(introLink).toBeDefined()
  })

  it('renders the minimal example JSON code block', () => {
    const { container } = renderDocs()
    const minimalSection = container.querySelector('#docs-minimal')
    expect(minimalSection?.textContent).toContain('"id": "minimal_server"')
  })

  it('renders the bot example JSON code block', () => {
    const { container } = renderDocs()
    const howtoSection = container.querySelector('#docs-howto')
    expect(howtoSection?.textContent).toContain('"id": "custom_discord_bot"')
    expect(howtoSection?.textContent).toContain('"category": "bot"')
  })

  it('documents startup-created runtime directories', () => {
    renderDocs()
    expect(screen.getByText('runtime.ensureDirs')).toBeInTheDocument()
    expect(screen.getByText(/Relative directories created inside the server directory/)).toBeInTheDocument()
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

  it('documents common Docker start failures', () => {
    renderDocs()
    expect(screen.getByText('failed to extract layer ... to overlayfs')).toBeInTheDocument()
    expect(screen.getByText('Docker image unavailable / image or tag not found')).toBeInTheDocument()
    expect(screen.getByText('Rootless Docker Daemon not running for user msm')).toBeInTheDocument()
  })
})
