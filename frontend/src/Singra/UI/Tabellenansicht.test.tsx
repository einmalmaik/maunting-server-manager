import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import i18n from '@/i18n'
import { Tabellenansicht } from './Tabellenansicht'

describe('Tabellenansicht', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
  })

  it('macht die erste Zeile zum Kopf und füllt kurze Zeilen auf', () => {
    render(<Tabellenansicht label="daten.csv" zeilen={[['Name', 'Ort'], ['Ada', 'London', 'extra'], ['Bo']]} />)
    const tabelle = screen.getByRole('table', { name: 'daten.csv' })
    expect(within(tabelle).getAllByRole('columnheader').map((k) => k.textContent)).toEqual(['#', 'Name', 'Ort', ''])
    expect(within(tabelle).getAllByRole('row')).toHaveLength(3)
  })

  it('zeichnet höchstens die gewünschte Zahl Zeilen und sagt, wie viele es sind', () => {
    const zeilen = [['n'], ...Array.from({ length: 5 }, (_, i) => [String(i)])]
    render(<Tabellenansicht label="t" zeilen={zeilen} hoechstens={2} />)
    expect(screen.getAllByRole('row')).toHaveLength(3)
    expect(screen.getByText(i18n.t('common.tabelle.gekuerzt', { gezeigt: '2', gesamt: '5' }))).toBeInTheDocument()
  })

  it('sagt, wenn nichts drinsteht', () => {
    render(<Tabellenansicht label="t" zeilen={[]} />)
    expect(screen.getByText(i18n.t('common.tabelle.leer'))).toBeInTheDocument()
  })
})
