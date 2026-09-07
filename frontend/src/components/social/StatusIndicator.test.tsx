import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StatusDot } from './StatusIndicator'

describe('StatusIndicator', () => {
  it('renders online dot with proper title', () => {
    render(<StatusDot status="online" />)
    expect(screen.getByTitle('Online')).toBeInTheDocument()
  })

  it('renders away dot with proper title', () => {
    render(<StatusDot status="away" />)
    expect(screen.getByTitle('Abwesend')).toBeInTheDocument()
  })

  it('renders invisible dot by default', () => {
    render(<StatusDot status="invisible" />)
    expect(screen.getByTitle('Unsichtbar / Offline')).toBeInTheDocument()
  })
})
