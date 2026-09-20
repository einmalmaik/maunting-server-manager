import { render, screen } from '@testing-library/react'
import { beforeAll, describe, expect, it } from 'vitest'
import i18n from '@/i18n'
import { DeviceBadge } from './DeviceBadge'

// Die Sprache festlegen: die Behauptungen unten prüfen deutsche Texte, und
// ohne diese Zeile entscheidet navigator.language der Testumgebung.
beforeAll(async () => {
  await i18n.changeLanguage('de')
})

describe('DeviceBadge', () => {
  it('renders web badge by default', () => {
    render(<DeviceBadge showLabel />)
    expect(screen.getByText(i18n.t('social.device.web'))).toBeInTheDocument()
    expect(screen.getByTitle(i18n.t('social.device.web'))).toBeInTheDocument()
  })

  it('renders desktop badge when type is desktop', () => {
    render(<DeviceBadge deviceType="desktop" showLabel />)
    expect(screen.getByText(i18n.t('social.device.desktop'))).toBeInTheDocument()
    expect(screen.getByTitle(i18n.t('social.device.desktop'))).toBeInTheDocument()
  })

  it('renders mobile badge when type is mobile', () => {
    render(<DeviceBadge deviceType="mobile" showLabel />)
    expect(screen.getByText(i18n.t('social.device.mobile'))).toBeInTheDocument()
    expect(screen.getByTitle(i18n.t('social.device.mobile'))).toBeInTheDocument()
  })
})
