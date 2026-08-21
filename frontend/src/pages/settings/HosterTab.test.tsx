import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { hosterApi, type HosterIntegration, type HosterProduct } from '@/api/hoster'
import { rbacApi } from '@/api/rbac'
import type { Role } from '@/types/permissions'
import i18n from '@/i18n'
import { HosterTab } from './HosterTab'

vi.mock('@/api/hoster', () => ({
  hosterApi: {
    listIntegrations: vi.fn(),
    createIntegration: vi.fn(),
    updateIntegration: vi.fn(),
    deleteIntegration: vi.fn(),
    rotateApiKey: vi.fn(),
    rotateWebhookSecret: vi.fn(),
    listProducts: vi.fn(),
    saveProduct: vi.fn(),
    deleteProduct: vi.fn(),
    listServices: vi.fn(),
    listDeliveries: vi.fn(),
    retryDelivery: vi.fn(),
    simulate: vi.fn(),
    cleanSandboxData: vi.fn(),
  },
}))

// Die Rollenliste ist ein Nebenaufruf des Reiters: sie fuellt nur ein optionales
// Feld der Produktzuordnung. Deshalb steht sie hier als eigener Mock — ihr
// Ausfall wird weiter unten ausdruecklich geprueft.
vi.mock('@/api/rbac', () => ({
  rbacApi: {
    listRoles: vi.fn(),
  },
}))

const integration: HosterIntegration = {
  id: 3,
  name: 'Testshop',
  slug: 'testshop',
  enabled: true,
  is_sandbox: false,
  service_user_id: 9,
  webhook_url: 'https://shop.example/hooks/msm',
  terminate_grace_days: 7,
  api_key_hint: '...ab12',
  webhook_secret_configured: true,
  webhook_secret_hint: '...cd34',
  created_at: '2026-08-07T10:00:00Z',
  updated_at: '2026-08-07T10:00:00Z',
}

const product: HosterProduct = {
  id: 11,
  integration_id: 3,
  external_product_key: 'gold',
  game_type: 'minecraft',
  ram_limit_mb: 4096,
  cpu_limit_percent: 200,
  disk_limit_gb: 20,
  node_id: null,
  backup_interval_hours: null,
  role_id: 5,
  enabled: true,
}

// Bewusst unsortiert: die Ansicht sortiert selbst, und genau das soll der Test
// sehen statt der Reihenfolge des Servers zu vertrauen.
const roles: Role[] = [
  { id: 5, name: 'Premium', description: null, is_system: false, permissions: [], created_at: '2026-08-07T10:00:00Z' },
  { id: 4, name: 'Basis', description: null, is_system: false, permissions: [], created_at: '2026-08-07T10:00:00Z' },
]

/**
 * Der Reiter verlinkt die Shop-Referenz und braucht darum einen Router-Kontext;
 * ohne ihn wirft <Link> beim Rendern.
 */
function renderTab(canWrite = true) {
  return render(
    <MemoryRouter>
      <HosterTab canWrite={canWrite} />
    </MemoryRouter>,
  )
}

describe('HosterTab', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
    vi.mocked(hosterApi.listIntegrations).mockReset().mockResolvedValue([integration])
    vi.mocked(hosterApi.listProducts).mockReset().mockResolvedValue([])
    vi.mocked(hosterApi.listServices).mockReset().mockResolvedValue([])
    vi.mocked(hosterApi.listDeliveries).mockReset().mockResolvedValue([])
    vi.mocked(hosterApi.saveProduct).mockReset().mockResolvedValue(product)
    vi.mocked(hosterApi.rotateApiKey).mockReset().mockResolvedValue({
      value: 'brandneuer-api-key',
      hint: '...9999',
    })
    vi.mocked(rbacApi.listRoles).mockReset().mockResolvedValue(roles)
  })

  it('shows only the hint, never a stored secret', async () => {
    renderTab()

    expect(await screen.findByText('...ab12')).toBeInTheDocument()
    expect(screen.getByText('...cd34')).toBeInTheDocument()
    // Es gibt keinen Lesepfad fuer Klartext-Geheimnisse.
    expect(screen.queryByText(/brandneuer-api-key/)).not.toBeInTheDocument()
  })

  it('reveals a rotated key exactly once and hides it again after acknowledgement', async () => {
    renderTab()
    fireEvent.click(await screen.findByRole('button', { name: /API-Key rotieren/ }))

    await waitFor(() => expect(screen.getByText('brandneuer-api-key')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Verstanden' }))

    await waitFor(() =>
      expect(screen.queryByText('brandneuer-api-key')).not.toBeInTheDocument(),
    )
  })

  /**
   * Eine falsch getippte Webhook-Adresse war eine Sackgasse: Loeschen lehnt das
   * Backend ab, solange ein Vertrag laeuft (409), und Neuanlegen erzeugt einen
   * neuen API-Key, den der Shop nicht kennt. Ohne diesen Weg gab es keinen.
   */
  it('lets the operator correct the webhook URL of an existing integration', async () => {
    vi.mocked(hosterApi.updateIntegration).mockReset().mockResolvedValue({
      ...integration,
      webhook_url: 'https://shop.example/hooks/neu',
    })
    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: 'Bearbeiten' }))
    fireEvent.change(screen.getByDisplayValue('https://shop.example/hooks/msm'), {
      target: { value: 'https://shop.example/hooks/neu' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }))

    await waitFor(() =>
      expect(hosterApi.updateIntegration).toHaveBeenCalledWith(3, {
        webhook_url: 'https://shop.example/hooks/neu',
        terminate_grace_days: 7,
        enabled: true,
        is_sandbox: false,
      }),
    )
  })

  it('hides every write action for a read-only operator', async () => {
    renderTab(false)

    await screen.findByText('...ab12')
    expect(screen.queryByRole('button', { name: /API-Key rotieren/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Integration hinzufügen/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Löschen/ })).not.toBeInTheDocument()
  })

  it('explains the two secrets it can only hint at', async () => {
    renderTab()

    expect(await screen.findByText(/MSM speichert nur einen Hash/)).toBeInTheDocument()
    expect(screen.getByText(/HMAC-Signatur ausgehender Webhooks/)).toBeInTheDocument()
  })

  it('links to the reference for shop developers', async () => {
    renderTab()

    expect(
      await screen.findByRole('link', { name: 'Vollständige Referenz für Shop-Entwickler' }),
    ).toHaveAttribute('href', '/docs/hoster-api')
  })

  it('explains every field of the create form', async () => {
    renderTab()
    fireEvent.click(await screen.findByRole('button', { name: 'Integration hinzufügen' }))

    expect(screen.getByText(/Anzeigename für die Liste im Panel/)).toBeInTheDocument()
    expect(screen.getByText(/Panelweit eindeutige technische Kennung/)).toBeInTheDocument()
    expect(screen.getByText(/handelt im Namen dieses Panel-Benutzers/)).toBeInTheDocument()
    expect(screen.getByText(/Zustellungen sind persistent/)).toBeInTheDocument()
    expect(screen.getByText(/bevor der Aufräumlauf/)).toBeInTheDocument()
  })

  /**
   * Der Dienstbenutzer laesst sich hier nicht mehr wechseln — gerade deshalb
   * gehoert der Hinweis auch ins Bearbeiten-Formular. Genau dort fehlte er.
   */
  it('explains the edit form too, service user included', async () => {
    renderTab()
    fireEvent.click(await screen.findByRole('button', { name: 'Bearbeiten' }))

    expect(screen.getByText(/handelt im Namen dieses Panel-Benutzers/)).toBeInTheDocument()
    expect(screen.getByText(/Zustellungen sind persistent/)).toBeInTheDocument()
    expect(screen.getByText(/bevor der Aufräumlauf/)).toBeInTheDocument()
  })

  it('explains every field of the product form', async () => {
    renderTab()

    expect(await screen.findByText(/Die Produktkennung, die der Shop beim Bestellen mitschickt/)).toBeInTheDocument()
    expect(screen.getByText(/Blueprint aus der Registry/)).toBeInTheDocument()
    expect(screen.getByText(/Ressourcenpaket dieses Tarifs/)).toBeInTheDocument()
    expect(screen.getByText(/Automatisches Backup-Intervall/)).toBeInTheDocument()
    expect(screen.getByText(/über sie laufen unter anderem die KI-Kontingente/)).toBeInTheDocument()
  })

  it('offers the roles of the panel and sends the chosen one with the product', async () => {
    renderTab()

    const select = await screen.findByLabelText(/Rolle bei Buchung/)
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(3))
    // Die Eintraege stammen aus rbacApi.listRoles() und stehen alphabetisch.
    expect(within(select).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Keine Zusatzrolle',
      'Basis',
      'Premium',
    ])

    fireEvent.change(screen.getByLabelText(/Produktkennung im Shop/), { target: { value: 'gold' } })
    fireEvent.change(screen.getByLabelText(/Blueprint \/ Spieltyp/), { target: { value: 'minecraft' } })
    fireEvent.change(select, { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Produkt speichern' }))

    await waitFor(() =>
      expect(hosterApi.saveProduct).toHaveBeenCalledWith(3, {
        external_product_key: 'gold',
        game_type: 'minecraft',
        ram_limit_mb: null,
        cpu_limit_percent: null,
        disk_limit_gb: null,
        node_id: null,
        backup_interval_hours: null,
        role_id: 5,
        enabled: true,
      }),
    )
  })

  /**
   * Die Rollenliste ist Beiwerk. Faellt sie aus, bleibt die Produktsektion
   * bedienbar — ein Nebenaufruf darf nicht den ganzen Reiter lahmlegen.
   */
  it('keeps the product section usable when the role list fails to load', async () => {
    vi.mocked(rbacApi.listRoles).mockReset().mockRejectedValue(new Error('rollen weg'))
    vi.mocked(hosterApi.listProducts).mockReset().mockResolvedValue([product])
    renderTab()

    // Ohne Liste bleibt die Kennung stehen: "Keine Zusatzrolle" waere hier eine
    // Falschaussage — das Produkt hat sehr wohl eine Rolle.
    expect(await screen.findByText(/#5/)).toBeInTheDocument()
    const select = screen.getByLabelText(/Rolle bei Buchung/)
    await waitFor(() => expect(rbacApi.listRoles).toHaveBeenCalled())
    expect(within(select).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Keine Zusatzrolle',
    ])

    fireEvent.change(screen.getByLabelText(/Produktkennung im Shop/), { target: { value: 'silber' } })
    fireEvent.change(screen.getByLabelText(/Blueprint \/ Spieltyp/), { target: { value: 'minecraft' } })
    fireEvent.click(screen.getByRole('button', { name: 'Produkt speichern' }))

    await waitFor(() =>
      expect(hosterApi.saveProduct).toHaveBeenCalledWith(
        3,
        expect.objectContaining({ external_product_key: 'silber', role_id: null }),
      ),
    )
  })
})
