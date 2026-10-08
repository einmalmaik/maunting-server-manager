import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { MarkenSymbol } from './marken'

const farben = (svg: Element) => [...svg.querySelectorAll('path')].map((p) => p.getAttribute('fill'))

describe('Marken-Symbole', () => {
  it('zeigt Google in seinen vier Farben, nicht als blaues G', () => {
    const { container } = render(<MarkenSymbol marke="google" />)
    expect(farben(container.querySelector('svg')!)).toEqual(['#4285F4', '#34A853', '#FBBC05', '#EA4335'])
  })

  it('ein Symbol ohne Markenfarbe nimmt die Textfarbe', () => {
    const { container } = render(<MarkenSymbol marke="github" />)
    expect(farben(container.querySelector('svg')!)).toEqual(['currentColor'])
  })
})
