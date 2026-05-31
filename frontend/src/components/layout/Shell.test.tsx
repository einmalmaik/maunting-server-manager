import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { Shell } from './Shell'

describe('Shell', () => {
  it('renders the privacy footer link without an impressum link', () => {
    render(
      <MemoryRouter>
        <Routes>
          <Route path="/" element={<Shell />}>
            <Route index element={<div>Dashboard</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: 'Datenschutz' })).toHaveAttribute('href', '/privacy')
    expect(screen.queryByText(/Impressum/i)).toBeNull()
  })
})
