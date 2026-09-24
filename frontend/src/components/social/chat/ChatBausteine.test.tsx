/**
 * Die Bausteine des offenen Chats.
 *
 * Am wichtigsten ist das Menü: wer welchen Eintrag sieht. Der Server prüft
 * Löschen, Rollen und Logo ohnehin; das Menü soll aber nicht anbieten, was
 * ohne Recht sicher scheitert, und dem Eigentümer nie „Verlassen" statt
 * „Löschen" zeigen.
 */

import { createRef } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import i18n from '@/i18n'
import type { ChatMessage } from '@/components/social/ChatMessageBubble'
import { ChatActionsMenu, type MenueGruppe } from './ChatActionsMenu'
import { ChatPinnedBar } from './ChatPinnedBar'
import { ChatTimeline } from './ChatTimeline'

beforeAll(async () => {
  await i18n.changeLanguage('de')
})

function menue(teil: Partial<Parameters<typeof ChatActionsMenu>[0]> = {}) {
  const aktionen = {
    schliessen: vi.fn(),
    onStumm: vi.fn(),
    onSicherheitsnummer: vi.fn(),
    onVerfall: vi.fn(),
    onVideoanruf: vi.fn(),
    onHintergrund: vi.fn(),
    onEinladung: vi.fn(),
    onLogo: vi.fn(),
    onRollen: vi.fn(),
    onLoeschen: vi.fn(),
    onVerlassen: vi.fn(),
    onBlockieren: vi.fn(),
  }
  render(
    <ChatActionsMenu
      stumm={false}
      kontakt={null}
      gruppe={null}
      verfallStufe="Aus"
      verfallErlaubt
      {...aktionen}
      {...teil}
    />,
  )
  return aktionen
}

const gruppe = (teil: Partial<MenueGruppe>): MenueGruppe => ({
  hatEinladung: false,
  istEigentuemer: false,
  istAdmin: false,
  logoLaedt: false,
  ...teil,
})

const zeigt = (schluessel: string) => screen.queryByText(i18n.t(schluessel)) !== null

describe('ChatActionsMenu', () => {
  it('bietet dem Eigentümer Logo, Rollen und Löschen, aber kein Verlassen', () => {
    menue({ gruppe: gruppe({ istEigentuemer: true }) })
    expect(zeigt('messenger.changeGroupLogo')).toBe(true)
    expect(zeigt('messenger.manageGroupRoles')).toBe(true)
    expect(zeigt('messenger.deleteGroup')).toBe(true)
    expect(zeigt('messenger.leaveGroup')).toBe(false)
  })

  it('bietet einem Admin Logo und Rollen, aber Verlassen statt Löschen', () => {
    menue({ gruppe: gruppe({ istAdmin: true }) })
    expect(zeigt('messenger.changeGroupLogo')).toBe(true)
    expect(zeigt('messenger.manageGroupRoles')).toBe(true)
    expect(zeigt('messenger.deleteGroup')).toBe(false)
    expect(zeigt('messenger.leaveGroup')).toBe(true)
  })

  it('zeigt einem einfachen Mitglied weder Logo noch Rollen', () => {
    menue({ gruppe: gruppe({}) })
    expect(zeigt('messenger.changeGroupLogo')).toBe(false)
    expect(zeigt('messenger.manageGroupRoles')).toBe(false)
    expect(zeigt('messenger.copyInvite')).toBe(false)
    expect(zeigt('messenger.leaveGroup')).toBe(true)
  })

  it('bietet Videoanruf nur unter Freunden, Sicherheitsnummer und Blockieren jedem Kontakt', () => {
    menue({ kontakt: { istFreund: false, blockiert: false } })
    expect(zeigt('messenger.videoCall')).toBe(false)
    expect(zeigt('messenger.verifySafetyNumber')).toBe(true)
    expect(zeigt('messenger.blockContact')).toBe(true)
  })

  it('zeigt bei einem Blockierten „Blockierung aufheben"', () => {
    menue({ kontakt: { istFreund: true, blockiert: true } })
    expect(zeigt('messenger.unblockContact')).toBe(true)
    expect(zeigt('messenger.videoCall')).toBe(true)
  })

  it('bietet ohne Postfach kein Stummschalten an', () => {
    menue({ stumm: null })
    expect(zeigt('messenger.mute')).toBe(false)
    expect(zeigt('messenger.unmute')).toBe(false)
  })

  it('sperrt die Frist ohne Recht und nennt den Grund', () => {
    menue({ verfallErlaubt: false, verfallStufe: '1 Tag' })
    const eintrag = screen.getByText(i18n.t('messenger.disappearingMessages')).closest('button')!
    expect(eintrag).toBeDisabled()
    expect(eintrag).toHaveTextContent(`1 Tag · ${i18n.t('messenger.retentionNoRight')}`)
  })

  it('schliesst das Blatt vor der Aktion', () => {
    const reihenfolge: string[] = []
    menue({
      gruppe: gruppe({ istEigentuemer: true }),
      schliessen: () => reihenfolge.push('schliessen'),
      onLoeschen: () => reihenfolge.push('loeschen'),
    })
    fireEvent.click(screen.getByText(i18n.t('messenger.deleteGroup')))
    expect(reihenfolge).toEqual(['schliessen', 'loeschen'])
  })
})

describe('ChatPinnedBar', () => {
  it('hat ohne Recht keinen Lösen-Knopf', () => {
    render(<ChatPinnedBar text="Regeln" onOeffnen={() => {}} />)
    expect(screen.queryByLabelText(i18n.t('messenger.unpin'))).not.toBeInTheDocument()
  })

  it('löst, ohne zur Nachricht zu springen', () => {
    const onOeffnen = vi.fn()
    const onLoesen = vi.fn()
    render(<ChatPinnedBar text="Regeln" onOeffnen={onOeffnen} onLoesen={onLoesen} />)
    fireEvent.click(screen.getByLabelText(i18n.t('messenger.unpin')))
    expect(onLoesen).toHaveBeenCalledTimes(1)
    expect(onOeffnen).not.toHaveBeenCalled()
  })
})

describe('ChatTimeline', () => {
  const heute = new Date()
  const gestern = new Date(heute.getTime() - 24 * 3600 * 1000)
  const nachricht = (id: number, datum: Date, teil: Partial<ChatMessage> = {}): ChatMessage => ({
    id,
    senderId: 2,
    text: `Nachricht ${id}`,
    createdAt: datum.toISOString(),
    isSelf: false,
    ...teil,
  })

  function zeichne(nachrichten: ChatMessage[], trennerId: number | null = null) {
    const zeichneNachricht = vi.fn((msg: ChatMessage) => <p>{msg.text}</p>)
    render(
      <ChatTimeline
        scrollRef={createRef()}
        endeRef={createRef()}
        nachrichten={nachrichten}
        laedt={false}
        trennerId={trennerId}
        aktivitaet={null}
        istGruppe={false}
        zeichneNachricht={zeichneNachricht}
      />,
    )
    return zeichneNachricht
  }

  it('setzt je Tag einen Datumstrenner und den Neu-Trenner vor die gemeinte Nachricht', () => {
    zeichne([nachricht(1, gestern), nachricht(2, gestern), nachricht(3, heute)], 3)
    expect(screen.getAllByText(i18n.t('messenger.yesterday'))).toHaveLength(1)
    expect(screen.getAllByText(i18n.t('messenger.today'))).toHaveLength(1)
    const neu = screen.getByText('Neue Nachrichten')
    // Der Trenner steht vor Nachricht 3, nicht vor 2.
    expect(neu.compareDocumentPosition(screen.getByText('Nachricht 3')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(neu.compareDocumentPosition(screen.getByText('Nachricht 2')) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy()
  })

  it('zeichnet eine Systemzeile nie als Sprechblase', () => {
    const zeichneNachricht = zeichne([nachricht(1, heute, { isSystem: true, text: 'Schlüssel geändert' })])
    expect(screen.getByText('Schlüssel geändert')).toBeInTheDocument()
    expect(zeichneNachricht).not.toHaveBeenCalled()
  })

  it('zeigt ohne Nachrichten den Leerhinweis', () => {
    zeichne([])
    expect(screen.getByText(i18n.t('messenger.noMessagesYet'))).toBeInTheDocument()
  })
})
