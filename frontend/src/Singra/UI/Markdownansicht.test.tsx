import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import i18n from '@/i18n'
import { Markdownansicht } from './Markdownansicht'

describe('Markdownansicht', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de')
  })

  it('lädt kein Bild, weder aus dem Netz noch als data-URI', () => {
    const { container } = render(
      <Markdownansicht text={'![Zähler](https://tracker.example/p.gif)\n\n![](//fremd.example/x.png)\n\n![Punkt](data:image/png;base64,AAAA)'} />,
    )
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText(i18n.t('common.markdown.bildMitText', { text: 'Zähler' }))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('common.markdown.bild'))).toBeInTheDocument()
  })

  it('gibt rohes HTML als Text aus', () => {
    const { container } = render(<Markdownansicht text={'<img src="https://tracker.example/a.gif"><script>alert(1)</script>'} />)
    expect(container.querySelector('img, script')).toBeNull()
  })

  it('öffnet Links getrennt und ohne Referrer', () => {
    render(<Markdownansicht text="[Doku](https://example.org/doku)" />)
    const link = screen.getByRole('link', { name: 'Doku' })
    expect(link).toHaveAttribute('target', '_blank')
    expect(link.getAttribute('rel')).toContain('noreferrer')
  })

  it('zeigt Tabellen aus GFM', () => {
    render(<Markdownansicht text={'| a | b |\n|---|---|\n| 1 | 2 |'} />)
    expect(screen.getByRole('table')).toHaveTextContent('12')
  })
})
