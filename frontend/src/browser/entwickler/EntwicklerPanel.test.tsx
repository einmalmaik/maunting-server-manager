import { render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { useTabsStore } from '../services/tabsStore'

import { EntwicklerPanel, OBERFLAECHE_TAB } from './EntwicklerPanel'

describe('EntwicklerPanel ohne offenen Tab', () => {
  beforeEach(() => {
    useTabsStore.setState({ tabs: [], aktivId: '' })
  })

  it('nennt die Kennung der Oberfläche, die Rust durchlässt', () => {
    expect(OBERFLAECHE_TAB).toMatch(/^tab-[a-z0-9-]{1,40}$/)
    expect(OBERFLAECHE_TAB).not.toContain('main')
  })

  it('untersucht die Oberfläche selbst statt des Leerzustands', () => {
    render(<EntwicklerPanel />)
    expect(screen.queryByText('Keine Seite offen')).toBeNull()
    // Die Ansichten stehen bereit: Elemente, Konsole, Quellen, Netz, Anwendung, Leistung, Gerät.
    const leiste = screen.getByRole('tablist', { name: 'Entwicklerwerkzeuge' })
    expect(within(leiste).getAllByRole('tab')).toHaveLength(7)
  })
})
