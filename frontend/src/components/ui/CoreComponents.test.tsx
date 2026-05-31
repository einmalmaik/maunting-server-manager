import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Button } from './Button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './Card'
import { ErrorMessage } from './ErrorMessage'
import { Input } from './Input'
import { Loader } from './Loader'
import { Switch } from './Switch'

describe('central UI components', () => {
  it('renders button, card and input with reusable MSM classes', () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>Panel</CardTitle>
          <CardDescription>Status</CardDescription>
        </CardHeader>
        <CardContent>
          <Input id="server-name" label="Server" error="Pflichtfeld" />
          <Button>Speichern</Button>
        </CardContent>
      </Card>,
    )

    expect(screen.getByText('Panel')).toBeInTheDocument()
    expect(screen.getByLabelText('Server')).toHaveClass('msm-input')
    expect(screen.getByRole('button', { name: 'Speichern' })).toHaveClass('msm-btn-primary')
    expect(screen.getByText('Pflichtfeld')).toBeInTheDocument()
  })

  it('renders accessible loader, error message and switch states', () => {
    const onChange = vi.fn()
    render(
      <>
        <Loader label="Laden" />
        <ErrorMessage message="Fehler" />
        <Switch checked={false} onCheckedChange={onChange} />
      </>,
    )

    expect(screen.getByRole('status')).toHaveTextContent('Laden')
    expect(screen.getByRole('alert')).toHaveTextContent('Fehler')

    const toggle = screen.getByRole('switch')
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(toggle)
    expect(onChange).toHaveBeenCalledWith(true)
  })
})
