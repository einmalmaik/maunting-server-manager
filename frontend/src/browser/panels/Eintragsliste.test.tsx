import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

const { useBildStore } = await import('../services/bildStore')
const { Eintragsliste } = await import('./Eintragsliste')

const PNG = 'data:image/png;base64,iVBORw0KGgo='

describe('Eintragsliste', () => {
  beforeEach(() => {
    useBildStore.setState({ bilder: {} })
  })

  it('zeigt Ordnerpfad und Symbol eines importierten Lesezeichens', () => {
    useBildStore.setState({ bilder: { 'https://a.example': PNG } })
    render(
      <Eintragsliste
        eintraege={[
          {
            url: 'https://a.example/seite',
            titel: 'Seite A',
            zeit: 1,
            ordner: ['Lesezeichenleiste', 'Dev'],
            favicon: 'https://a.example',
          },
        ]}
        leer="Leer"
        entfernen={() => undefined}
      />,
    )
    expect(screen.getByText('Seite A')).toBeInTheDocument()
    expect(screen.getByText('Lesezeichenleiste › Dev')).toBeInTheDocument()
    expect(document.querySelector('img')).toHaveAttribute('src', PNG)
  })

  it('sucht auch in Ordnern und zeigt dieselbe Adresse in zwei Ordnern zweimal', () => {
    render(
      <Eintragsliste
        eintraege={[
          { url: 'https://a.example/', titel: 'A', zeit: 1, ordner: ['Lesezeichenleiste'] },
          { url: 'https://a.example/', titel: 'A', zeit: 2, ordner: ['Weitere Lesezeichen'] },
        ]}
        leer="Leer"
        entfernen={() => undefined}
      />,
    )
    expect(screen.getAllByText('A')).toHaveLength(2)
    fireEvent.change(screen.getByRole('textbox', { name: 'Durchsuchen' }), {
      target: { value: 'weitere' },
    })
    expect(screen.getAllByText('A')).toHaveLength(1)
    expect(screen.getByText('Weitere Lesezeichen')).toBeInTheDocument()
  })

  it('verträgt einen beschädigten Ordnerwert aus einem alten Stand', () => {
    render(
      <Eintragsliste
        eintraege={[{ url: 'https://a.example/', titel: 'A', zeit: 1, ordner: 'kaputt' as unknown as string[] }]}
        leer="Leer"
        entfernen={() => undefined}
      />,
    )
    expect(screen.getByText('A')).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
  })
})
