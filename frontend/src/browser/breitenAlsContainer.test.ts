import postcss from 'postcss'
import { describe, expect, it } from 'vitest'

import { alsContainerAbfrage, breitenAlsContainer, viewportAlsContainer } from '../../vite.breitenAlsContainer'

describe('Breakpoints je Bereich im Browser-Bau', () => {
  it('macht reine Breitenabfragen zu Containerabfragen', () => {
    expect(alsContainerAbfrage('(min-width: 640px)')).toBe('msb (min-width: 640px)')
    expect(alsContainerAbfrage('not all and (min-width: 768px)')).toBe('msb not (min-width: 768px)')
    expect(alsContainerAbfrage('(min-width: 640px) and (max-width: 1023.98px)')).toBe(
      'msb (min-width: 640px) and (max-width: 1023.98px)',
    )
  })

  it('lässt alles andere als Medienabfrage stehen', () => {
    expect(alsContainerAbfrage('(prefers-reduced-motion: reduce)')).toBeNull()
    expect(alsContainerAbfrage('print')).toBeNull()
    expect(alsContainerAbfrage('(min-width: 640px) and (hover: hover)')).toBeNull()
  })

  it('rechnet Viewport-Einheiten am Bereich', () => {
    expect(viewportAlsContainer('calc(100dvh - 5.5rem)')).toBe('calc(100cqh - 5.5rem)')
    expect(viewportAlsContainer('90vw')).toBe('90cqw')
    expect(viewportAlsContainer('1px solid red')).toBe('1px solid red')
  })

  it('schreibt das Stylesheet um', async () => {
    const css = '@media (min-width: 640px){.a{height:100vh}} @media (hover:hover){.b{color:red}}'
    const ergebnis = await postcss([breitenAlsContainer()]).process(css, { from: undefined })
    expect(ergebnis.css).toContain('@container msb (min-width: 640px)')
    expect(ergebnis.css).toContain('height:100cqh')
    expect(ergebnis.css).toContain('@media (hover:hover)')
  })
})
