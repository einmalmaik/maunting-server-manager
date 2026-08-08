import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import i18n from '@/i18n'
import {
  HosterApiDocs,
  SIGNATURE_EXAMPLE_BODY,
  SIGNATURE_EXAMPLE_DIGEST,
  SIGNATURE_EXAMPLE_SECRET,
  SIGNATURE_EXAMPLE_TIMESTAMP,
} from './HosterApiDocs'

function renderPage() {
  return render(
    <MemoryRouter>
      <HosterApiDocs />
    </MemoryRouter>,
  )
}

describe('HosterApiDocs', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en')
  })

  it('renders the English title', () => {
    renderPage()

    expect(screen.getByRole('heading', { name: 'Hoster API' })).toBeInTheDocument()
  })

  it('lists every external endpoint a shop needs', () => {
    renderPage()

    for (const path of [
      '/api/hoster/v1/health',
      '/api/hoster/v1/services/{external_service_id}',
      '/api/hoster/v1/handoffs',
      '/api/hoster/handoff/{token}',
    ]) {
      expect(screen.getAllByText(path).length).toBeGreaterThan(0)
    }
  })

  it('documents every webhook event name', () => {
    renderPage()

    const events = screen.getByTestId('hoster-webhook-events').textContent ?? ''
    for (const status of [
      'pending',
      'provisioning',
      'ready',
      'suspended',
      'terminating',
      'terminated',
      'failed',
    ]) {
      expect(events).toContain(`service.${status}`)
    }
  })

  it('shows the full webhook body, not just the header block', () => {
    renderPage()

    const body = screen.getByTestId('hoster-webhook-body').textContent ?? ''
    for (const field of [
      'event',
      'external_service_id',
      'desired_state',
      'status',
      'status_code',
      'server_id',
      'correlation_id',
      'terminate_after',
      'updated_at',
    ]) {
      expect(body).toContain(`"${field}"`)
    }
  })

  it('shows a signature example that carries secret, timestamp, body and digest', () => {
    renderPage()

    const example = screen.getByTestId('hoster-signature-example').textContent ?? ''
    expect(example).toContain(SIGNATURE_EXAMPLE_SECRET)
    expect(example).toContain(SIGNATURE_EXAMPLE_TIMESTAMP)
    expect(example).toContain(SIGNATURE_EXAMPLE_BODY)
    expect(example).toContain(SIGNATURE_EXAMPLE_DIGEST)
  })

  it('warns that the signature covers the raw body', () => {
    renderPage()

    expect(screen.getByText(/Sign over the raw body/i)).toBeInTheDocument()
  })

  it('warns that oversized payloads are dropped without notice', () => {
    renderPage()

    expect(screen.getByText(/silently dropped/i)).toBeInTheDocument()
  })

  it('warns that a 4xx answer permanently drops the delivery', () => {
    renderPage()

    expect(screen.getByText(/permanently rejected/i)).toBeInTheDocument()
  })

  it('lists the admin endpoints with their required permission', () => {
    renderPage()

    // GET und POST teilen sich denselben Pfad — beide Zeilen muessen da sein.
    expect(screen.getAllByText('/api/hoster/integrations')).toHaveLength(2)
    expect(
      screen.getByText('/api/hoster/integrations/{integration_id}/deliveries/{delivery_id}/retry'),
    ).toBeInTheDocument()
    expect(screen.getAllByText('panel.hoster.write').length).toBeGreaterThan(0)
    expect(screen.getAllByText('panel.hoster.read').length).toBeGreaterThan(0)
  })

  it('renders German content after a language switch', async () => {
    await i18n.changeLanguage('de')
    renderPage()

    expect(screen.getByRole('heading', { name: 'Hoster-API' })).toBeInTheDocument()
    await i18n.changeLanguage('en')
  })
})
