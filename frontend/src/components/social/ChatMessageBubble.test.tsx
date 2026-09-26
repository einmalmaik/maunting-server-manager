import React from 'react'
import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import i18n from '@/i18n'
import {
  ChatMessageBubble,
  MessageErrorBoundary,
  safeFormatDate,
  safeFormatTime,
  type ChatMessage,
} from './ChatMessageBubble'

describe('ChatMessageBubble Resilienz & Fehlerbehandlung', () => {
  describe('safeFormatDate & safeFormatTime', () => {
    it('formatiert gueltige Datumsangaben', () => {
      const validIso = '2026-09-25T14:30:00.000Z'
      expect(safeFormatDate(validIso)).not.toBe('')
      expect(safeFormatTime(validIso)).not.toBe('')
    })

    it('faengt ungueltige Datumsangaben ab ohne RangeError zu werfen', () => {
      expect(safeFormatDate('invalid-date')).toBe('')
      expect(safeFormatDate(null)).toBe('')
      expect(safeFormatDate(undefined, {}, 'Fallback')).toBe('Fallback')

      expect(safeFormatTime('invalid-date')).toBe('')
      expect(safeFormatTime(null)).toBe('')
      expect(safeFormatTime(undefined, {}, 'Fallback')).toBe('Fallback')
    })
  })

  describe('MessageErrorBoundary', () => {
    it('faengt Rendering-Fehler ab und rendert die Fallback-Anzeige', () => {
      const ProblemKind = () => {
        throw new Error('Explosion beim Rendern')
      }

      // Suppress React error boundary console output in test
      const spy = vi.spyOn(console, 'warn').mockImplementation(() => {})

      render(
        <MessageErrorBoundary>
          <ProblemKind />
        </MessageErrorBoundary>
      )

      expect(screen.getByText('Nachricht konnte nicht dargestellt werden')).toBeDefined()
      spy.mockRestore()
    })
  })

  describe('ChatMessageBubble Rendering mit korrupten Daten', () => {
    const defaultKontext = {
      activeGroup: null,
      activeContact: { username: 'Bob' },
      eigeneId: 1,
      eigenerName: 'Alice',
      readReceiptsEnabled: true,
      importedAttachmentIds: new Set<string>(),
    }

    const defaultAktionen = {
      onReagieren: vi.fn(),
      onAntworten: vi.fn(),
      onWeiterleiten: vi.fn(),
      onKopieren: vi.fn(),
      onBearbeiten: vi.fn(),
      onLoeschen: vi.fn(),
      onMarkieren: vi.fn(),
      onAnheften: vi.fn(),
      onAuswaehlen: vi.fn(),
      onMenue: vi.fn(),
      onSpringeZu: vi.fn(),
      onViewImage: vi.fn(),
      onImportNote: vi.fn(),
      onImportCalendar: vi.fn(),
    }

    const defaultTon = {
      playingAudioId: null,
      audioCurrentTime: 0,
      audioPlaybackRate: 1,
      onTogglePlay: vi.fn(),
      onCycleRate: vi.fn(),
      onSeek: vi.fn(),
    }

    const defaultAuswahl = {
      aktiv: false,
      gewaehlt: false,
      onUmschalten: vi.fn(),
    }

    it('zeigt in Gruppen keine Quittungshaken, im Direktchat schon', () => {
      // Gruppen verschicken seit 26.09.2026 keine Quittungen mehr. Ein
      // einzelner Haken hiesse dort „noch nicht zugestellt“.
      const eigene: ChatMessage = {
        id: 5,
        senderId: 1,
        text: 'Von mir',
        createdAt: '2026-09-25T14:30:00.000Z',
        isSelf: true,
        isRead: true,
        isDelivered: true,
      }
      const zeige = (kontext: typeof defaultKontext, msg: ChatMessage = eigene) =>
        render(
          <ChatMessageBubble
            msg={msg}
            kontext={kontext}
            ton={defaultTon}
            aktionen={defaultAktionen}
            auswahl={defaultAuswahl}
            medienBindung={() => ({} as any)}
          />
        )
      const gruppe = { ...defaultKontext, activeGroup: { id: 77, name: 'Gruppe', members: [] } as any }

      const imDirektchat = zeige(defaultKontext)
      expect(screen.getByTitle(i18n.t('messenger.stateRead'))).toBeDefined()
      imDirektchat.unmount()

      const inDerGruppe = zeige(gruppe)
      expect(screen.queryByTitle(i18n.t('messenger.stateRead'))).toBeNull()
      expect(screen.queryByTitle(i18n.t('messenger.stateUndelivered'))).toBeNull()
      inDerGruppe.unmount()

      // Die Uhr für „wartet noch“ bleibt auch in der Gruppe.
      zeige(gruppe, { ...eigene, isRead: false, status: 'queued' })
      expect(screen.getByTitle(i18n.t('messenger.stateQueued'))).toBeDefined()
    })

    it('rendert ohne Absturz bei ungueltigem createdAt Zeitstempel', () => {
      const msg: ChatMessage = {
        id: 1,
        senderId: 2,
        text: 'Hallo Welt',
        createdAt: 'ungueltiges-datum',
        isSelf: false,
      }

      render(
        <ChatMessageBubble
          msg={msg}
          kontext={defaultKontext}
          ton={defaultTon}
          aktionen={defaultAktionen}
          auswahl={defaultAuswahl}
          medienBindung={() => ({} as any)}
        />
      )

      expect(screen.getByText('Hallo Welt')).toBeDefined()
    })

    it('rendert ohne Absturz bei ungueltigem calendarAttachment.start', () => {
      const msg: ChatMessage = {
        id: 2,
        senderId: 2,
        text: 'Termin',
        createdAt: '2026-09-25T12:00:00Z',
        isSelf: false,
        calendarAttachment: {
          title: 'Kaputter Termin',
          start: 'invalid-iso-date',
          end: 'invalid-iso-date',
          description: 'Test Beschreibung',
        },
      }

      render(
        <ChatMessageBubble
          msg={msg}
          kontext={defaultKontext}
          ton={defaultTon}
          aktionen={defaultAktionen}
          auswahl={defaultAuswahl}
          medienBindung={() => ({} as any)}
        />
      )

      expect(screen.getByText('Kaputter Termin')).toBeDefined()
    })

    it('rendert ohne Absturz wenn antwortAuf.auszug ein Objekt statt String ist', () => {
      const msg: ChatMessage = {
        id: 3,
        senderId: 2,
        text: 'Antwort auf Objekt',
        createdAt: '2026-09-25T12:00:00Z',
        isSelf: false,
        antwortAuf: {
          clientUuid: 'ref-1',
          absenderId: 1,
          auszug: { text: 'Injected Object Payload' } as any,
        },
      }

      render(
        <ChatMessageBubble
          msg={msg}
          kontext={defaultKontext}
          ton={defaultTon}
          aktionen={defaultAktionen}
          auswahl={defaultAuswahl}
          medienBindung={() => ({} as any)}
        />
      )

      expect(screen.getByText('Antwort auf Objekt')).toBeDefined()
      expect(screen.getByText('{"text":"Injected Object Payload"}')).toBeDefined()
    })
  })
})
