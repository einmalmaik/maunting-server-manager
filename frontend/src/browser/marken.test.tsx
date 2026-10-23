import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { MarkenSymbol } from './marken'

const farben = (svg: Element) => [...svg.querySelectorAll('path')].map((p) => p.getAttribute('fill'))

describe('Marken-Symbole', () => {
  it('zeigt Google in seinen vier Farben, nicht als blaues G', () => {
    const { container } = render(<MarkenSymbol marke="google" />)
    expect(farben(container.querySelector('svg')!)).toEqual(['#4285F4', '#34A853', '#FBBC05', '#EA4335'])
  })

  it.each([
    ['whatsapp', '#25D366'],
    ['discord', '#5865F2'],
    ['telegram', '#26A5E4'],
    ['instagram', '#E4405F'],
  ] as const)('zeichnet %s in seiner Markenfarbe', (marke, farbe) => {
    const { container } = render(<MarkenSymbol marke={marke} />)
    expect(farben(container.querySelector('svg')!)).toEqual([farbe])
  })
})
