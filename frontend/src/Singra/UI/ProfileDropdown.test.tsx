import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import { ProfileDropdown, type ProfileDropdownItem } from './ProfileDropdown'
import { fakeLayout } from '@/test/fakeLayout'

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

  it('bleibt am rechten Rand eines Telefons im Fenster', () => {
    const layout = fakeLayout({
      fenster: { breite: 360, hoehe: 640 },
      anker: { left: 290, top: 10, width: 40, height: 40 },
      popover: { width: 256, height: 300 },
    })
    try {
      render(<ProfileDropdown user={dummyUser} items={dummyItems} triggerVariant="avatar" placement="bottom-right" />)
      fireEvent.click(screen.getByRole('button', { name: i18n.t('common.openUserMenu') }))
      const menu = screen.getByRole('menu')
      expect(menu.style.left).toBe(`${330 - 256}px`)
      expect(menu.style.top).toBe(`${50 + 8}px`)
      expect(layout.imFenster(menu)).toBe(true)
    } finally {
      layout.aufraeumen()
    }
  })

  it('klappt nach oben, wenn das gemessene Menü unten nicht passt, und hängt an body', () => {
    // Bis 02.10.2026 entschied eine feste Schwelle von 200 px: bei 248 px Platz
    // blieb ein 400 px hohes Menü unten, im Stapelkontext der Shell.
    const layout = fakeLayout({
      fenster: { breite: 1024, hoehe: 768 },
      anker: { left: 100, top: 480, width: 40, height: 40 },
      popover: { width: 256, height: 400 },
    })
    try {
      render(<ProfileDropdown user={dummyUser} items={dummyItems} triggerVariant="avatar" placement="bottom-left" />)
      fireEvent.click(screen.getByRole('button', { name: i18n.t('common.openUserMenu') }))
      const menu = screen.getByRole('menu')
      expect(menu.parentElement).toBe(document.body)
      expect(menu.style.top).toBe(`${480 - 8 - 400}px`)
      expect(layout.imFenster(menu)).toBe(true)
    } finally {
      layout.aufraeumen()
    }
  })

  it('schließt bei Klick daneben, nicht bei Klick ins Menü', () => {
    const onStatusChange = vi.fn()
    render(<ProfileDropdown user={dummyUser} items={dummyItems} status="online" onStatusChange={onStatusChange} />)
    fireEvent.click(screen.getByRole('button', { name: i18n.t('common.openUserMenu') }))
    fireEvent.mouseDown(screen.getByRole('button', { name: /Abwesend/i }))
    fireEvent.click(screen.getByRole('button', { name: /Abwesend/i }))
    expect(onStatusChange).toHaveBeenCalledWith('away')
    expect(screen.getByRole('menu')).toBeInTheDocument()
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })
})
