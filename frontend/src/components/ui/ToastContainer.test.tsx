import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { ToastContainer } from './ToastContainer'
import { toast, useToastStore } from '@/stores/toastStore'
import i18n from '@/i18n'

describe('ToastContainer', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    i18n.changeLanguage('en')
    useToastStore.setState({ toasts: [] })
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('keeps error toasts visible for 20 seconds and auto-dismisses them', () => {
    act(() => {
      toast.error('Docker-Image nicht verfügbar: ghcr.io/example/demo:latest')
    })
    render(<ToastContainer />)

    expect(screen.getByRole('alert')).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(10000)
    })
    expect(screen.getByRole('alert')).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(10000)
    })
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('allows manual dismissal before auto-dismiss timeout', () => {
    act(() => {
      toast.error('Fehler beim Laden')
    })
    render(<ToastContainer />)

    expect(screen.getByRole('alert')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Close'))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('auto-dismisses success toasts after 5 seconds', () => {
    act(() => {
      toast.success('Saved')
    })
    render(<ToastContainer />)

    expect(screen.getByRole('status')).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('zeigt dieselbe Fehlermeldung nur einmal', () => {
    act(() => {
      toast.error('Knoten nicht erreichbar')
      toast.error('Knoten nicht erreichbar')
    })
    render(<ToastContainer />)

    expect(screen.getAllByRole('alert')).toHaveLength(1)
  })

  it('begrenzt die Anzahl gleichzeitiger Toasts auf maximal 5', () => {
    act(() => {
      toast.error('Fehler 1')
      toast.error('Fehler 2')
      toast.error('Fehler 3')
      toast.error('Fehler 4')
      toast.error('Fehler 5')
      toast.error('Fehler 6')
      toast.error('Fehler 7')
    })
    render(<ToastContainer />)

    const alerts = screen.getAllByRole('alert')
    expect(alerts).toHaveLength(5)
    expect(screen.queryByText('Fehler 1')).toBeNull()
    expect(screen.queryByText('Fehler 2')).toBeNull()
    expect(screen.getByText('Fehler 3')).toBeInTheDocument()
    expect(screen.getByText('Fehler 7')).toBeInTheDocument()
  })

  it('copies error toast text', () => {
    const message = 'failed to extract layer to overlayfs'
    act(() => {
      toast.error(message)
    })
    render(<ToastContainer />)

    fireEvent.click(screen.getByLabelText('Copy'))
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(message)
  })
})
