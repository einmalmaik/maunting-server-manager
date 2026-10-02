import { render, screen } from '@testing-library/react'
import { beforeAll, describe, expect, it } from 'vitest'
import i18n from '@/i18n'
import { StatusDot } from './StatusIndicator'

// Die Sprache festlegen: die Behauptungen unten prüfen deutsche Texte, und
// ohne diese Zeile entscheidet navigator.language der Testumgebung.
beforeAll(async () => {
  await i18n.changeLanguage('de')
})

describe('StatusIndicator', () => {
  it('renders online dot with proper name', () => {
    render(<StatusDot status="online" />)
    expect(screen.getByRole('img', { name: i18n.t('social.status.online') })).toBeInTheDocument()
  })

  it('renders away dot with proper name', () => {
    render(<StatusDot status="away" />)
    expect(screen.getByRole('img', { name: i18n.t('social.status.away') })).toBeInTheDocument()
  })

  it('renders invisible dot by default', () => {
    render(<StatusDot status="invisible" />)
    expect(screen.getByRole('img', { name: i18n.t('social.status.offline') })).toBeInTheDocument()
  })

  it('setzt kein natives title und legt die Lage an die Hülle', () => {
    const { container } = render(<StatusDot status="online" className="absolute bottom-0 right-0" />)
    expect(container.querySelectorAll('[title]')).toHaveLength(0)
    const punkt = screen.getByRole('img', { name: i18n.t('social.status.online') })
    expect(punkt.parentElement).toHaveClass('absolute', 'bottom-0', 'right-0')
    expect(punkt).not.toHaveClass('absolute')
  })
})
