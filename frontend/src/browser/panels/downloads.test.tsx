import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { gerufen } = vi.hoisted(() => ({ gerufen: [] as { befehl: string; args: Record<string, unknown> }[] }))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: (befehl: string, args: Record<string, unknown>) => {
    gerufen.push({ befehl, args })
    if (befehl === 'konfig_aendern') return Promise.resolve({ ...(args.felder as object) })
    return Promise.resolve(null)
  },
}))

const { useDownloadsStore } = await import('../services/downloadsStore')
const { DownloadsPanel } = await import('./Downloads')
const { Downloads } = await import('../einstellungen/Downloads')

const melde = (nr: number, stand: 'start' | 'pruefung' | 'fertig' | 'blockiert' | 'fehler' | 'speicher', datei: string) =>
  useDownloadsStore.getState().ereignis({ art: 'download', id: 'tab-a', nr, stand, url: 'https://example.com/datei.zip', datei })

beforeEach(() => {
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}
  useDownloadsStore.setState({ downloads: [], neu: 0 })
  gerufen.length = 0
})

describe('Downloads mit Virenschutz', () => {
  it('führt zwei Downloads derselben Adresse getrennt bis zum Ergebnis', () => {
    melde(1, 'start', 'datei.zip')
    melde(2, 'start', 'datei.zip')
    melde(2, 'pruefung', 'datei.zip')
    melde(1, 'pruefung', 'datei.zip')
    melde(1, 'blockiert', 'datei.zip')
    melde(2, 'fertig', 'C:\\Downloads\\datei (1).zip')
    const { downloads } = useDownloadsStore.getState()
    expect(downloads.map((d) => [d.nr, d.stand, d.datei])).toEqual([
      [2, 'fertig', 'C:\\Downloads\\datei (1).zip'],
      [1, 'blockiert', 'datei.zip'],
    ])
  })

  it('sagt, was der Virenschutz tut, und zeigt nur fertige Dateien im Ordner', () => {
    melde(1, 'start', 'gut.pdf')
    melde(1, 'pruefung', 'gut.pdf')
    melde(2, 'start', 'eicar.com')
    melde(2, 'blockiert', 'eicar.com')
    render(<DownloadsPanel />)
    const gut = screen.getByText('gut.pdf').closest('li')!
    expect(within(gut).getByText('Wird vom Virenschutz geprüft')).toBeInTheDocument()
    const boese = screen.getByText('eicar.com').closest('li')!
    expect(within(boese).getByText('Vom Virenschutz blockiert und gelöscht')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /im Ordner zeigen/ })).toBeNull()
    // „Liste leeren“ lässt laufende Prüfungen stehen.
    fireEvent.click(screen.getByRole('button', { name: 'Liste leeren' }))
    expect(useDownloadsStore.getState().downloads.map((d) => d.nr)).toEqual([1])
  })

  it('sagt, wenn ein Download am vollen Gerät abbricht', () => {
    melde(1, 'start', 'gross.iso')
    melde(1, 'speicher', 'gross.iso')
    render(<DownloadsPanel />)
    const zeile = screen.getByText('gross.iso').closest('li')!
    expect(within(zeile).getByText('Abgebrochen: zu wenig Speicher auf dem Gerät')).toBeInTheDocument()
  })

  it('öffnet eine fertige Datei, zeigt sie im Ordner und öffnet den Download-Ordner', async () => {
    melde(1, 'fertig', 'C:\\Downloads\\test.pdf')
    render(<DownloadsPanel />)
    const zeile = screen.getByText('test.pdf').closest('li')!
    // Klick auf Dateinamen öffnet die Datei
    fireEvent.click(within(zeile).getByRole('button', { name: 'test.pdf' }))
    expect(gerufen).toContainEqual({ befehl: 'download_oeffnen', args: { pfad: 'C:\\Downloads\\test.pdf' } })

    // Klick auf Öffnen-Icon-Button öffnet ebenfalls
    fireEvent.click(within(zeile).getByRole('button', { name: 'test.pdf öffnen' }))
    expect(gerufen.filter((g) => g.befehl === 'download_oeffnen')).toHaveLength(2)

    // Klick auf Ordner-Button zeigt im Explorer
    fireEvent.click(within(zeile).getByRole('button', { name: 'test.pdf im Ordner zeigen' }))
    expect(gerufen).toContainEqual({ befehl: 'download_zeigen', args: { pfad: 'C:\\Downloads\\test.pdf' } })

    // Kopfzeilen-Button öffnet Download-Ordner
    fireEvent.click(screen.getByRole('button', { name: 'Ordner öffnen' }))
    expect(gerufen).toContainEqual({ befehl: 'download_ordner_oeffnen', args: undefined })
  })

  it('löscht eine Datei und entfernt sie aus der Liste', async () => {
    melde(1, 'fertig', 'C:\\Downloads\\test.pdf')
    render(<DownloadsPanel />)
    expect(screen.getByText('test.pdf')).toBeInTheDocument()

    // Klick auf Lösch-Button
    fireEvent.click(screen.getByRole('button', { name: 'test.pdf von der Festplatte löschen' }))
    await waitFor(() => {
      expect(gerufen).toContainEqual({ befehl: 'download_loeschen', args: { pfad: 'C:\\Downloads\\test.pdf' } })
      expect(screen.queryByText('test.pdf')).toBeNull()
    })
  })

  it('schaltet „Bei jedem Download fragen“ in der Gerätekonfiguration', async () => {
    render(<Downloads />)
    fireEvent.click(screen.getByRole('switch', { name: 'Bei jedem Download fragen' }))
    await waitFor(() => expect(gerufen.find((g) => g.befehl === 'konfig_aendern')?.args).toEqual({ felder: { download_fragen: true } }))
  })
})
