import { describe, it, expect, beforeEach } from 'vitest'
import { useConfirmStore, confirm } from './confirmStore'

beforeEach(() => {
  // Auf Initialzustand setzen, damit Tests sich nicht gegenseitig beeinflussen.
  useConfirmStore.setState({ pending: null })
})

describe('confirmStore', () => {
  it('resolves with true when accepted', async () => {
    const p = confirm({ message: 'OK?' })
    useConfirmStore.getState().resolve(true)
    await expect(p).resolves.toBe(true)
  })

  it('resolves with false when cancelled', async () => {
    const p = confirm({ message: 'OK?' })
    useConfirmStore.getState().resolve(false)
    await expect(p).resolves.toBe(false)
  })

  it('clears pending after resolving', async () => {
    const p = confirm({ message: 'OK?' })
    expect(useConfirmStore.getState().pending).not.toBeNull()
    useConfirmStore.getState().resolve(true)
    await p
    expect(useConfirmStore.getState().pending).toBeNull()
  })

  it('auto-cancels a previous pending confirm when a new one is opened', async () => {
    const first = confirm({ message: 'first' })
    const second = confirm({ message: 'second' })
    // Second resolves "yes", first must already be resolved as "no".
    useConfirmStore.getState().resolve(true)
    await expect(first).resolves.toBe(false)
    await expect(second).resolves.toBe(true)
  })

  it('passes through options (title, danger, custom texts)', () => {
    void confirm({
      title: 'Achtung',
      message: 'Wirklich?',
      confirmText: 'Ja, weg damit',
      cancelText: 'Doch nicht',
      danger: true,
    })
    const p = useConfirmStore.getState().pending
    expect(p).not.toBeNull()
    expect(p!.title).toBe('Achtung')
    expect(p!.message).toBe('Wirklich?')
    expect(p!.confirmText).toBe('Ja, weg damit')
    expect(p!.cancelText).toBe('Doch nicht')
    expect(p!.danger).toBe(true)
  })
})
