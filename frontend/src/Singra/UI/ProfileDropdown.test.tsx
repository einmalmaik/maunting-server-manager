import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import { ProfileDropdown, type ProfileDropdownItem } from './ProfileDropdown'

// Die Sprache festlegen: die Behauptungen unten prüfen deutsche Texte, und
// ohne diese Zeile entscheidet navigator.language der Testumgebung.
beforeAll(async () => {
  await i18n.changeLanguage('de')
})

describe('ProfileDropdown', () => {
  const dummyUser = {
    username: 'admin',
    email: 'admin@example.test',
    avatar_url: null,
  }

  const dummyItems: ProfileDropdownItem[] = [
    {
      key: 'profile',
      label: 'Profil',
      onClick: vi.fn(),
    },
    {
      key: 'logout',
      label: 'Abmelden',
      onClick: vi.fn(),
      tone: 'danger',
    },
  ]

  it('renders avatar and username in full trigger variant', () => {
    render(
      <ProfileDropdown
        user={dummyUser}
        items={dummyItems}
        triggerVariant="full"
        status="online"
      />
    )

    expect(screen.getByText('admin')).toBeInTheDocument()
    expect(screen.getByText('admin@example.test')).toBeInTheDocument()
  })

  it('opens menu on click and displays status options (Online, Abwesend, Unsichtbar)', () => {
    const handleStatusChange = vi.fn()

    render(
      <ProfileDropdown
        user={dummyUser}
        items={dummyItems}
        triggerVariant="full"
        status="online"
        onStatusChange={handleStatusChange}
      />
    )

    const trigger = screen.getByRole('button', { name: i18n.t('common.openUserMenu') })
    fireEvent.click(trigger)

    const menu = screen.getByRole('menu')
    expect(menu).toBeInTheDocument()

    // Status options
    expect(screen.getByRole('button', { name: /Online/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Abwesend/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Unsichtbar/i })).toBeInTheDocument()

    // Menu items
    expect(screen.getByText('Profil')).toBeInTheDocument()
    expect(screen.getByText('Abmelden')).toBeInTheDocument()

    // Change status to away
    const awayBtn = screen.getByRole('button', { name: /Abwesend/i })
    fireEvent.click(awayBtn)
    expect(handleStatusChange).toHaveBeenCalledWith('away')
  })

  it('nennt den Status in der App-Sprache, auch auf Englisch', async () => {
    // Im Emulator (02.10.2026, App auf Englisch) stand „Abwesend“ und „Unsichtbar“.
    await i18n.changeLanguage('en')
    try {
      render(
        <ProfileDropdown user={{ username: null }} items={[]} triggerVariant="full" status="away" onStatusChange={vi.fn()} />,
      )
      fireEvent.click(screen.getByRole('button', { name: i18n.t('common.openUserMenu') }))
      const menue = screen.getByRole('menu')
      expect(within(menue).getByRole('button', { name: 'Away' })).toHaveAttribute('aria-pressed', 'true')
      expect(within(menue).getByRole('button', { name: 'Invisible' })).toHaveAttribute('aria-pressed', 'false')
      expect(menue.textContent).not.toMatch(/Abwesend|Unsichtbar|Benutzer/)
      expect(within(menue).getByText('User')).toBeInTheDocument()
    } finally {
      await i18n.changeLanguage('de')
    }
  })

  it('calls item onClick when clicked and closes dropdown', () => {
    render(
      <ProfileDropdown
        user={dummyUser}
        items={dummyItems}
        triggerVariant="avatar"
        status="online"
      />
    )

    const trigger = screen.getByRole('button', { name: i18n.t('common.openUserMenu') })
    fireEvent.click(trigger)

    const profileItem = screen.getByText('Profil')
    fireEvent.click(profileItem)

    expect(dummyItems[0].onClick).toHaveBeenCalled()
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('correctly aligns right-0 when trigger is near right edge on mobile viewport', () => {
    // Set mobile viewport width
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 360 })
    Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: 640 })

    const { container } = render(
      <ProfileDropdown
        user={dummyUser}
        items={dummyItems}
        triggerVariant="avatar"
        placement="bottom-right"
      />
    )

    const triggerContainer = container.firstChild as HTMLElement
    // Mock getBoundingClientRect for trigger near right edge
    vi.spyOn(triggerContainer, 'getBoundingClientRect').mockReturnValue({
      left: 290,
      right: 330,
      top: 10,
      bottom: 50,
      width: 40,
      height: 40,
      x: 290,
      y: 10,
      toJSON: () => {},
    })

    const trigger = screen.getByRole('button', { name: i18n.t('common.openUserMenu') })
    fireEvent.click(trigger)

    const menu = screen.getByRole('menu')
    expect(menu).toBeInTheDocument()
    // Should have right-0 so it expands to the left within viewport
    expect(menu.className).toContain('right-0')
  })
})
