import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { leererTab, type Tab } from '../services/tab'
import { useTabsStore } from '../services/tabsStore'
import { LesemodusKnopf } from './LesemodusKnopf'

describe('LesemodusKnopf', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('wird nicht gerendert, wenn die Seite weder lesbar noch im Lesemodus ist', () => {
    const tab: Tab = {
      ...leererTab('tab-1'),
      url: 'https://example.com/',
      lesbar: false,
      lesemodus: false,
    }
    const { container } = render(<LesemodusKnopf tab={tab} />)
    expect(container.firstChild).toBeNull()
  })

  it('wird gerendert, wenn tab.lesbar wahr ist, und schaltet per Klick um', () => {
    const lesemodusUmschalten = vi.fn()
    useTabsStore.setState({ lesemodusUmschalten } as any)

    const tab: Tab = {
      ...leererTab('tab-2'),
      url: 'https://example.com/artikel',
      lesbar: true,
      lesemodus: false,
    }

    render(<LesemodusKnopf tab={tab} />)
    const button = screen.getByRole('button')
    expect(button).toBeInTheDocument()
    expect(button).toHaveAttribute('aria-pressed', 'false')

    fireEvent.click(button)
    expect(lesemodusUmschalten).toHaveBeenCalledWith('tab-2')
  })

  it('wird als aktiv markiert, wenn tab.lesemodus wahr ist', () => {
    const tab: Tab = {
      ...leererTab('tab-3'),
      url: 'https://example.com/artikel',
      lesbar: true,
      lesemodus: true,
    }

    render(<LesemodusKnopf tab={tab} />)
    const button = screen.getByRole('button')
    expect(button).toBeInTheDocument()
    expect(button).toHaveAttribute('aria-pressed', 'true')
  })
})
