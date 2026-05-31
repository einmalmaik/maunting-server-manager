import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Logo } from './Logo'

describe('Logo', () => {
  it('uses the central MSM logo asset', () => {
    render(<Logo />)

    const logo = screen.getByAltText('MauntingStudios')
    expect(logo).toHaveAttribute('src', '/logo.png')
  })
})
