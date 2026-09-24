/**
 * Die Bausteine der Messenger-Seitenleiste.
 *
 * Die Chatzeilen lesen Ungelesen, Anheften, Archiv, Stumm und Blockiert selbst
 * aus dem Store. Bis 09/2026 reichte die Seite diese Werte hinein; hier steht,
 * dass die Zeile dieselben Abzeichen zeigt wie vorher.
 */

import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import type { ChatGroupItem } from '@/api/social'
import type { ChatContact } from '@/components/social/sidebar/ConversationListItem'
import { useMessengerNotificationStore } from '@/stores/messengerNotificationStore'
import { ContactFilterTabs } from './ContactFilterTabs'
import { ContactListItem, GroupListItem } from './ConversationListItem'

beforeAll(async () => {
  await i18n.changeLanguage('de')
})

afterEach(() => {
  useMessengerNotificationStore.setState({
    unreadCounts: {},
    pinnedChats: [],
    archivedChats: [],
    mentionedChats: [],
    mutedChats: {},
    blockedUserIds: [],
  })
})

function kontakt(teil: Partial<ChatContact> = {}): ChatContact {
  return {
    listKey: 'k-5',
    id: 5,
    userId: 5,
    username: 'dora',
    status: 'online',
    isFriend: true,
    ...teil,
  }
}

function gruppe(): ChatGroupItem {
  return {
    id: 9,
    name: null as unknown as string,
    invite_code: null,
    owner_user_id: 1,
    member_count: 3,
    role: 'member',
    created_at: '2026-09-01T00:00:00Z',
  } as ChatGroupItem
}

const zeile = {
  ausgewaehlt: false,
  entwurf: '',
  onOeffnen: () => {},
  onAnheften: () => {},
  onArchivieren: () => {},
  onMenue: () => {},
}

describe('Chatzeilen', () => {
  it('zeigt Ungelesen ab 100 als 99+ und markiert Anheften, Stumm und Erwähnung', () => {
    useMessengerNotificationStore.setState({
      unreadCounts: { m1: 140 },
      pinnedChats: ['m1'],
      mentionedChats: ['m1'],
      mutedChats: { m1: 0 },
    })
    const { container } = render(<ContactListItem kontakt={kontakt()} mid="m1" {...zeile} />)
    expect(screen.getByText('99+')).toBeInTheDocument()
    expect(screen.getByTitle(i18n.t('messenger.youWereMentioned'))).toBeInTheDocument()
    expect(container.querySelector('.lucide-pin')).not.toBeNull()
    expect(container.querySelector('.lucide-bell-off')).not.toBeNull()
  })

  it('zeigt ohne Postfach keine Abzeichen und löst keine Geste aus', () => {
    useMessengerNotificationStore.setState({ unreadCounts: { m1: 3 } })
    const onMenue = vi.fn()
    render(<ContactListItem kontakt={kontakt()} mid={undefined} {...zeile} onMenue={onMenue} />)
    expect(screen.queryByText('3')).not.toBeInTheDocument()
    fireEvent.click(screen.getByLabelText(i18n.t('messenger.chatActions')))
    expect(onMenue).not.toHaveBeenCalled()
  })

  it('ersetzt die zweite Zeile durch einen angefangenen Entwurf', () => {
    render(<GroupListItem gruppe={gruppe()} titel="Versiegelt" mid="g1" {...zeile} entwurf="halb fertig" />)
    expect(screen.getByText('halb fertig')).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('messenger.encryptedGroup'))).not.toBeInTheDocument()
    expect(screen.getByText('Versiegelt')).toBeInTheDocument()
  })

  it('kennzeichnet Blockierte und blendet archivierte Chats ab', () => {
    useMessengerNotificationStore.setState({ blockedUserIds: [5], archivedChats: ['m1'] })
    render(<ContactListItem kontakt={kontakt()} mid="m1" {...zeile} />)
    expect(screen.getByText('Blockiert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /dora/ }).className).toContain('opacity-60')
  })

  it('öffnet den Chat beim Klick', () => {
    const onOeffnen = vi.fn()
    render(<ContactListItem kontakt={kontakt()} mid="m1" {...zeile} onOeffnen={onOeffnen} />)
    fireEvent.click(screen.getByRole('button', { name: /dora/ }))
    expect(onOeffnen).toHaveBeenCalledTimes(1)
  })
})

describe('ContactFilterTabs', () => {
  it('zählt Freunde, Teams und Öffentliche aus den Kontakten', () => {
    const onFilter = vi.fn()
    render(
      <ContactFilterTabs
        filter="all"
        onFilter={onFilter}
        gruppenAnzahl={0}
        kontakte={[
          kontakt(),
          kontakt({ listKey: 'k-6', userId: 6, isFriend: false, teamName: 'Dev' }),
          kontakt({ listKey: 'k-7', userId: 7, isFriend: false, isPublicUser: true }),
        ]}
      />,
    )
    const freunde = screen.getByLabelText(i18n.t('messenger.filterFriends', { count: 1 }))
    expect(freunde).toHaveTextContent('1')
    // Ohne Gruppen steht am Gruppenreiter keine Zahl.
    expect(screen.getByLabelText(i18n.t('messenger.filterGroups', { count: 0 })).textContent).toBe('')
    fireEvent.click(screen.getByLabelText(i18n.t('messenger.filterTeams', { count: 1 })))
    expect(onFilter).toHaveBeenCalledWith('teams')
  })
})
