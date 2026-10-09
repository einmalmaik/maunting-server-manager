import { renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tauri-apps/api/core', () => ({ invoke: () => Promise.resolve(null) }))
// Der Ersatz ruft selbst einen Hook (AGENTS.md Punkt 26).
vi.mock('@/hooks/useHasPermission', async () => {
  const { useState } = await import('react')
  return { useHasPermission: () => useState(true)[0] }
})

const { useAnSingra, singraEntwurf } = await import('./anSingra')
const { entwurfNehmen } = await import('@/lib/aiEntwurf')
const { useSitzung } = await import('./sitzung')
const { useEinstellungenStore } = await import('./einstellungenStore')
const { leererTab } = await import('./tab')

const tab = (teil: Partial<ReturnType<typeof leererTab>> = {}) => ({ ...leererTab('tab-a'), url: 'https://seite.example/artikel', titel: 'Ein Artikel', ...teil })

function benutzen(t: ReturnType<typeof tab>) {
  const wrapper = ({ children }: { children: ReactNode }) => <MemoryRouter>{children}</MemoryRouter>
  return renderHook(() => ({ aktion: useAnSingra(t), ort: useLocation().pathname }), { wrapper })
}

beforeEach(() => {
  entwurfNehmen()
  useSitzung.setState({ stand: 'an' })
  useEinstellungenStore.setState({ ausgeblendet: [] })
})

describe('An Singra übergeben', () => {
  it('gibt nur Titel und Adresse als Entwurf und öffnet Singra', () => {
    const { result, rerender } = benutzen(tab())
    result.current.aktion!()
    rerender()
    expect(entwurfNehmen()).toBe('Ein Artikel\nhttps://seite.example/artikel')
    expect(result.current.ort).toBe('/ai')
  })

  it('nimmt ohne Titel nur die Adresse', () => {
    expect(singraEntwurf({ url: 'https://a.example/', titel: '' })).toBe('https://a.example/')
    expect(singraEntwurf({ url: 'https://a.example/', titel: 'https://a.example/' })).toBe('https://a.example/')
  })

  it.each([
    ['privater Tab', () => tab({ privat: true })],
    ['eigene Seite', () => tab({ url: 'msb://einstellungen' })],
    ['leerer Tab', () => tab({ url: '' })],
  ])('gibt es nicht für: %s', (_name, bauen) => {
    expect(benutzen(bauen()).result.current.aktion).toBeNull()
  })

  it('gibt es ungekoppelt und bei ausgeblendetem Singra nicht', () => {
    useSitzung.setState({ stand: 'aus' })
    expect(benutzen(tab()).result.current.aktion).toBeNull()
    useSitzung.setState({ stand: 'an' })
    useEinstellungenStore.setState({ ausgeblendet: ['singra'] })
    expect(benutzen(tab()).result.current.aktion).toBeNull()
  })
})
