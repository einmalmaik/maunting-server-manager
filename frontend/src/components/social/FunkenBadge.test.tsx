import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'

import { FunkenBadge, funkenText, stundenBis } from './FunkenBadge'
import { STUNDE_MS, type FunkenZustand } from '@/services/funkenService'

vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({
    t: (k: string, o?: Record<string, unknown>) => (o ? `${k} ${JSON.stringify(o)}` : k),
  }),
}))

const JETZT = Date.parse('2026-09-26T12:00:00Z')

function zustand(weiter: Partial<FunkenZustand>): FunkenZustand {
  return {
    partnerId: 2,
    status: 'active',
    streakCount: 14,
    lostCount: 0,
    deadlineAt: JETZT + 30 * STUNDE_MS,
    cycleStartAt: JETZT - 2 * STUNDE_MS,
    meSent: false,
    partnerSent: false,
    erledigt: false,
    lastRestoredAt: null,
    canRestore: false,
    restoreAvailableAt: null,
    record: 14,
    ...weiter,
  }
}

function zeige(z: FunkenZustand, onWiederherstellen?: () => void, interaktiv = false) {
  return render(
    <FunkenBadge
      zustand={z}
      name="Max"
      jetzt={JETZT}
      interaktiv={interaktiv}
      onWiederherstellen={onWiederherstellen}
    />,
  )
}

describe('FunkenBadge', () => {
  it('aktiv: die Badge der Oberfläche in Warnfarbe, ohne Browser-Tooltip', () => {
    const { container } = zeige(zustand({}))
    const el = container.querySelector('[data-funke="active"]')
    expect(el?.textContent).toBe('14')
    expect(el?.className).toContain('bg-status-warning/10')
    expect(el?.hasAttribute('title')).toBe(false)
  })

  it('Puffer: pulsierende Sanduhr, Restzeit für Screenreader', () => {
    const { container } = zeige(zustand({ status: 'grace', deadlineAt: JETZT + 5 * STUNDE_MS }))
    const el = container.querySelector('[data-funke="grace"]')
    expect(el?.querySelector('.animate-pulse')).not.toBeNull()
    expect(el?.getAttribute('aria-label')).toContain('messenger.streak.buffer {"hours":5}')
  })

  it('Verlängerung: Flamme mit Kurzhinweis', () => {
    const { container } = zeige(zustand({ status: 'extended', deadlineAt: JETZT + 70 * STUNDE_MS }))
    const el = container.querySelector('[data-funke="extended"]')
    expect(el?.textContent).toContain('messenger.streak.extendedShort')
    expect(el?.className).toContain('bg-status-destructive/10')
  })

  it('in der Chatliste nicht anklickbar — die Zeile ist schon ein Knopf', () => {
    zeige(zustand({ status: 'expired', streakCount: 0, lostCount: 30, canRestore: true, deadlineAt: null }), vi.fn())
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('interaktiv: öffnet ein Blatt, ohne in die Zeile durchzuklicken, und stellt von dort wieder her', () => {
    const retten = vi.fn()
    const zeile = vi.fn()
    render(
      <div onClick={zeile}>
        <FunkenBadge
          zustand={zustand({ status: 'expired', streakCount: 0, lostCount: 30, canRestore: true, deadlineAt: null })}
          name="Max"
          jetzt={JETZT}
          interaktiv
          onWiederherstellen={retten}
        />
      </div>,
    )
    fireEvent.click(screen.getByRole('button'))
    expect(zeile).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog')).toBeTruthy()
    fireEvent.click(screen.getByText('messenger.streak.restoreTitle'))
    expect(retten).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('erloschen ohne Wiederherstellung: kein Eintrag dafür im Blatt', () => {
    zeige(
      zustand({ status: 'expired', streakCount: 0, lostCount: 30, canRestore: false, deadlineAt: null }),
      vi.fn(),
      true,
    )
    fireEvent.click(screen.getByRole('button'))
    expect(screen.queryByText('messenger.streak.restoreTitle')).toBeNull()
  })

  it('ohne Funken nichts', () => {
    const { container } = zeige(zustand({ status: 'inactive', streakCount: 0 }))
    expect(container.innerHTML).toBe('')
    const offen = zeige(zustand({ status: 'pending', streakCount: 0 }))
    expect(offen.container.innerHTML).toBe('')
  })
})

describe('funkenText', () => {
  const t = (k: string, o?: Record<string, unknown>) => (o ? `${k} ${JSON.stringify(o)}` : k)

  it('sagt, wer dran ist', () => {
    expect(funkenText(zustand({ meSent: true }), 'Max', JETZT, t)).toContain('waitingForPartner')
    expect(funkenText(zustand({ meSent: false }), 'Max', JETZT, t)).toContain('waitingForYou')
    expect(funkenText(zustand({ erledigt: true, cycleStartAt: JETZT + 3 * STUNDE_MS }), 'Max', JETZT, t)).toContain(
      'doneToday {"hours":3}',
    )
  })

  it('nennt die Sperrfrist der Wiederherstellung in Tagen', () => {
    const z = zustand({
      status: 'expired',
      lostCount: 5,
      canRestore: false,
      restoreAvailableAt: JETZT + 10 * 24 * STUNDE_MS,
    })
    expect(funkenText(z, 'Max', JETZT, t)).toContain('restoreCooldown {"count":10}')
  })

  it('rundet angefangene Stunden auf', () => {
    expect(stundenBis(JETZT + 90 * 60_000, JETZT)).toBe(2)
    expect(stundenBis(JETZT - 1, JETZT)).toBe(1)
  })
})
