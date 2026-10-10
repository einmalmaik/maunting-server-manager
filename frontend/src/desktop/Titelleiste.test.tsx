import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const fenster = vi.hoisted(() => ({
  minimize: vi.fn(async () => {}),
  toggleMaximize: vi.fn(async () => {}),
  close: vi.fn(async () => {}),
}))
vi.mock('@tauri-apps/api/window', () => ({ getCurrentWindow: () => fenster }))

import { Titelleiste } from './Titelleiste'

afterEach(() => vi.restoreAllMocks())

describe('Titelleiste', () => {
  it('minimiert, maximiert und schließt über das Fenster, gezogen wird an der Leiste', () => {
    const { container } = render(<Titelleiste />)
    expect(container.firstElementChild?.hasAttribute('data-tauri-drag-region')).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Minimieren' }))
    fireEvent.click(screen.getByRole('button', { name: 'Maximieren' }))
    fireEvent.click(screen.getByRole('button', { name: 'Schließen' }))
    expect(fenster.minimize).toHaveBeenCalledOnce()
    expect(fenster.toggleMaximize).toHaveBeenCalledOnce()
    // close() und nicht destroy(): Rust hält das Fenster an, und die App fragt.
    expect(fenster.close).toHaveBeenCalledOnce()
  })

  it('gibt es unter Android nicht', () => {
    vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Linux; Android 14) AppleWebKit')
    const { container } = render(<Titelleiste />)
    expect(container.firstChild).toBeNull()
  })
})
