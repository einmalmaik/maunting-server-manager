import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DeviceBadge } from './DeviceBadge'

describe('DeviceBadge', () => {
  it('renders web badge by default', () => {
    render(<DeviceBadge showLabel />)
    expect(screen.getByText('Web')).toBeInTheDocument()
    expect(screen.getByTitle('Web-Panel')).toBeInTheDocument()
  })

  it('renders desktop badge when type is desktop', () => {
    render(<DeviceBadge deviceType="desktop" showLabel />)
    expect(screen.getByText('Desktop-App')).toBeInTheDocument()
    expect(screen.getByTitle('Desktop-App (MSS)')).toBeInTheDocument()
  })

  it('renders mobile badge when type is mobile', () => {
    render(<DeviceBadge deviceType="mobile" showLabel />)
    expect(screen.getByText('Mobile')).toBeInTheDocument()
    expect(screen.getByTitle('Mobile App')).toBeInTheDocument()
  })
})
