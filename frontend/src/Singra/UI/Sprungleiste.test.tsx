import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Topbar } from '@/components/layout/Topbar'
import { Sprungleiste } from './Sprungleiste'

describe('Sprungleiste', () => {
  it('klebt direkt unter der Topbar und ohne Topbar ganz oben', () => {
    // Bis 27.09.2026 klebte die Leiste bei top-16. Die Topbar ist aber nur
    // 48 px hoch (h-12) und ab lg ausgeblendet: in der Lücke darüber lief der
    // Inhalt durch. Ändert sich die Topbar, muss dieser Versatz mitgehen.
    render(
      <MemoryRouter>
        <Topbar />
        <Sprungleiste label="Abschnitte" ziele={[{ id: 'a', label: 'A' }]} />
      </MemoryRouter>,
    )

    const topbar = screen.getByRole('banner')
    expect(topbar.className).toMatch(/(^|\s)h-12(\s|$)/)
    expect(topbar.className).toMatch(/(^|\s)lg:hidden(\s|$)/)

    const leiste = screen.getByRole('navigation', { name: 'Abschnitte' })
    expect(leiste.className).toMatch(/(^|\s)sticky(\s|$)/)
    expect(leiste.className).toMatch(/(^|\s)top-12(\s|$)/)
    expect(leiste.className).toMatch(/(^|\s)lg:top-0(\s|$)/)
    expect(screen.getByRole('link', { name: 'A' })).toHaveAttribute('href', '#a')
  })
})
