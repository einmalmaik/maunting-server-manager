import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import i18n from '@/i18n'
import { COMPONENT_MIGRATION_COMMAND, PANEL_BOOTSTRAP_COMMAND, SelfHostingDocs } from './SelfHostingDocs'

function renderPage() {
  return render(
    <MemoryRouter>
      <SelfHostingDocs />
    </MemoryRouter>,
  )
}

describe('SelfHostingDocs', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en')
  })

  it('renders the English title and exact public bootstrap command', () => {
    renderPage()

    expect(screen.getByRole('heading', { name: 'Self-hosting & deployment' })).toBeInTheDocument()
    expect(screen.getByTestId('panel-bootstrap-command').textContent).toBe(PANEL_BOOTSTRAP_COMMAND)
  })

  it('renders all release artifact names', () => {
    renderPage()

    expect(screen.getAllByText('msm-panel-<VERSION>.tar.gz')).toHaveLength(2)
    expect(screen.getAllByText('msm-frontend-<VERSION>.tar.gz')).toHaveLength(2)
    expect(screen.getAllByText('msm-agent-<VERSION>.tar.gz')).toHaveLength(2)
    expect(screen.getByText('SHA256SUMS')).toBeInTheDocument()
  })

  it('explains that no manual token copy is required', () => {
    renderPage()

    expect(screen.getByText(/no manual token or TLS fingerprint copy is required/i)).toBeInTheDocument()
  })

  it('documents the shared minimal-system installation path', () => {
    renderPage()

    expect(screen.getByText(/on minimal systems it installs every required base package/i)).toBeInTheDocument()
    expect(screen.getByText(/without replacing an existing Caddyfile/i)).toBeInTheDocument()
  })

  it('documents safe continuation of a partial PostgreSQL setup', () => {
    renderPage()

    expect(screen.getByText(/continued without deletion using --resume-partial/i)).toBeInTheDocument()
    expect(screen.getByText(/foreign PostgreSQL state remains blocked/i)).toBeInTheDocument()
  })

  it('documents the interactive component migration and its safety boundaries', () => {
    renderPage()

    expect(screen.getByTestId('component-migration-command').textContent).toBe(COMPONENT_MIGRATION_COMMAND)
    expect(screen.getByText(/keeps source data and the old control plane as a rollback basis/i)).toBeInTheDocument()
    expect(screen.getByText(/DNS A\/AAAA record.*one-time owner approval/i)).toBeInTheDocument()
    expect(screen.getByText(/saves, mods, workshop files, backups and assigned PostgreSQL databases/i)).toBeInTheDocument()
    expect(screen.getByText(/automatically uses the owner-confirmed node ID/i)).toBeInTheDocument()
  })

  it('links to node administration and the documentation index', () => {
    renderPage()

    expect(screen.getByRole('link', { name: /open node administration/i })).toHaveAttribute('href', '/admin/nodes')
    expect(screen.getByRole('link', { name: /back to documentation/i })).toHaveAttribute('href', '/docs')
  })

  it('provides mobile landmark links for long-form wayfinding', () => {
    renderPage()

    expect(document.querySelector('a[href="#deployment-units"]')).toBeInTheDocument()
    expect(document.querySelector('a[href="#component-migration"]')).toBeInTheDocument()
    expect(document.querySelector('a[href="#guardian-state"]')).toBeInTheDocument()
  })

  it('documents Guardian state backup, restore and split-brain boundaries', () => {
    renderPage()
    expect(screen.getByText('/var/lib/msm-agent/guardian', { exact: false })).toBeInTheDocument()
    expect(screen.getByText(/MSM_GUARDIAN_STATE_DIR/)).toBeInTheDocument()
    expect(screen.getByText(/must never be active on two nodes at once/i)).toBeInTheDocument()
    expect(screen.getByText(/Stop the agent, restore the path/i)).toBeInTheDocument()
  })

  it('nennt die Desktop-App samt ihrer Grenze zur Serververwaltung', () => {
    renderPage()

    // Die Grenze ist der Punkt, der hier stehen muss: wer die App
    // ausrollt, soll nicht erst im Betrieb merken, dass sie bewusst keine
    // zweite Serververwaltung ist.
    expect(document.querySelector('a[href="#smart-system"]')).toBeInTheDocument()
    expect(screen.getByText(/never reaches a server tool/i)).toBeInTheDocument()
    // Und der unsignierte Installer, weil SmartScreen sonst wie ein Fehler
    // aussieht statt wie eine bekannte Eigenschaft.
    expect(screen.getByText(/installer is not signed/i)).toBeInTheDocument()
  })
})
