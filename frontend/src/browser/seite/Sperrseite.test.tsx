import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@tauri-apps/api/core', () => ({ invoke: () => Promise.resolve(null) }))

const { Sperrseite } = await import('./Sperrseite')
const { useTabsStore } = await import('../services/tabsStore')

function gesperrt(url: string, grund: string) {
  const tab = useTabsStore.getState().tabs[0]
  return { ...tab, url, fehler: 'gesperrt', gesperrt: grund }
}

describe('Sperrseite', () => {
  it('sagt bei einer IP-Adresse, dass Seiten nur über ihren Namen aufgehen', () => {
    render(<Sperrseite tab={gesperrt('https://93.184.215.14/', 'adresse')} />)
    expect(screen.getByText(/93\.184\.215\.14 ist eine IP-Adresse/)).toBeInTheDocument()
    expect(screen.queryByText(/gehört zu/)).not.toBeInTheDocument()
  })

  it('nennt bei einer Kategorie die Kategorie', () => {
    render(<Sperrseite tab={gesperrt('https://casino.example/', 'gluecksspiel')} />)
    expect(screen.getByText(/casino\.example gehört zu/)).toBeInTheDocument()
  })
})
