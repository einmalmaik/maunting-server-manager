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

  it('keeps error toasts visible until dismissed', () => {
    act(() => {
      toast.error('Docker-Image nicht verfügbar: ghcr.io/example/demo:latest')
    })
    render(<ToastContainer />)

    expect(screen.getByRole('alert')).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(6000)
    })
    expect(screen.getByRole('alert')).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('Close'))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('auto-dismisses success toasts', () => {
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
