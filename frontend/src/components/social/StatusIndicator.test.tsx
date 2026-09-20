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
  it('renders online dot with proper title', () => {
    render(<StatusDot status="online" />)
    expect(screen.getByTitle(i18n.t('social.status.online'))).toBeInTheDocument()
  })

  it('renders away dot with proper title', () => {
    render(<StatusDot status="away" />)
    expect(screen.getByTitle(i18n.t('social.status.away'))).toBeInTheDocument()
  })

  it('renders invisible dot by default', () => {
    render(<StatusDot status="invisible" />)
    expect(screen.getByTitle(i18n.t('social.status.offline'))).toBeInTheDocument()
  })
})
