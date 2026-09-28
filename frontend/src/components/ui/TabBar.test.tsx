import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Globe, Mail } from 'lucide-react'
import { TabBar, type TabDef } from './TabBar'

interface TestTab {
  general: 'general'
  email: 'email'
}

const tabs: TabDef<TestTab[keyof TestTab]>[] = [
  { id: 'general', labelKey: 'settings.tabs.general', icon: Globe },
  { id: 'email', labelKey: 'settings.tabs.email', icon: Mail },
]

// react-i18next liefert in Tests den Key direkt als Rueckgabe, das reicht
// fuer die Pruefung von Rendering und Verhalten.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

describe('TabBar', () => {
  it('rendert alle Tabs mit i18n-Keys und markiert den aktiven Tab', () => {
    render(<TabBar tabs={tabs} active="general" onChange={() => {}} ariaLabel="Settings" />)

    const tablist = screen.getByRole('tablist', { name: 'Settings' })
    expect(tablist).toBeInTheDocument()

    const general = screen.getByRole('tab', { name: 'settings.tabs.general' })
    const email = screen.getByRole('tab', { name: 'settings.tabs.email' })

    expect(general).toHaveAttribute('aria-selected', 'true')
    expect(email).toHaveAttribute('aria-selected', 'false')
  })

  it('ruft onChange mit der Tab-ID beim Klick auf', () => {
    const onChange = vi.fn()
    render(<TabBar tabs={tabs} active="general" onChange={onChange} ariaLabel="Settings" />)

    fireEvent.click(screen.getByRole('tab', { name: 'settings.tabs.email' }))
    expect(onChange).toHaveBeenCalledWith('email')
  })

  it('rendert danger-Tabs mit der Danger-Variante', () => {
    const dangerTabs: TabDef<'danger'>[] = [
      { id: 'danger', labelKey: 'profile.tabs.danger', icon: Mail, variant: 'danger' },
    ]
    render(<TabBar tabs={dangerTabs} active="danger" onChange={() => {}} ariaLabel="Danger" />)

    const danger = screen.getByRole('tab', { name: 'profile.tabs.danger' })
    expect(danger.className).toMatch(/status-destructive/)
  })

  it('zeigt eine Zahl am Reiter, auch die Null', () => {
    const gezaehlt: TabDef<'a' | 'b'>[] = [
      { id: 'a', labelKey: 'teams.members', icon: Mail, badge: 3 },
      { id: 'b', labelKey: 'teams.tabServers', icon: Globe, badge: 0 },
    ]
    render(<TabBar tabs={gezaehlt} active="a" onChange={() => {}} ariaLabel="Team" embedded />)

    expect(screen.getByRole('tab', { name: /teams\.members/ })).toHaveTextContent('3')
    expect(screen.getByRole('tab', { name: /teams\.tabServers/ })).toHaveTextContent('0')
    expect(screen.getByRole('tablist', { name: 'Team' }).className).not.toMatch(/msm-card/)
  })
})
