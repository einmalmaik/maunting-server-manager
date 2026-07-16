import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import i18n from '@/i18n'
import { PANEL_BOOTSTRAP_COMMAND, SelfHostingDocs } from './SelfHostingDocs'

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

  it('links to node administration and the documentation index', () => {
    renderPage()

    expect(screen.getByRole('link', { name: /open node administration/i })).toHaveAttribute('href', '/admin/nodes')
    expect(screen.getByRole('link', { name: /back to documentation/i })).toHaveAttribute('href', '/docs')
  })
})
