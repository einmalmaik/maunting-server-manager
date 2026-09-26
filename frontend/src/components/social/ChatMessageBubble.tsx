/**
 * Eine Nachricht im Verlauf.
 *
 * Stand bis 09/2026 mitten in `pages/Messenger.tsx`: rund 450 Zeilen JSX, an der
 * tiefsten Stelle zweiundzwanzig Ebenen eingerückt, zwischen Kontaktliste,
 * Aufnahmesteuerung und Gruppenverwaltung. Jede Ergänzung an der Blase — eine
 * Reaktionsleiste, ein Zitatkopf, ein Auswahlhaken — hätte dort weitere Ebenen
 * bedeutet, und niemand hätte sie mehr gefunden.
 *
 * Herausgelöst ohne Verhaltensänderung. Die Requisiten sind zu Gruppen
 * zusammengefasst statt einzeln durchgereicht: was zusammen gebraucht wird,
 * steht zusammen.
 *
 * **Ein Einstieg, zwei Wege.** Alles, was mit einer Nachricht geht, steht im
 * Menü: Reagieren, Antworten, Weiterleiten, Kopieren, Markieren, Anheften,
 * Auswählen, Bearbeiten, Löschen. Am Telefon öffnet langes Drücken es, am
 * Rechner der Knopf neben der Uhrzeit. Die Maus kennt keinen Langdruck, und
 * eine Geste allein findet niemand — es braucht beide.
 */

import React, { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  Calendar as CalendarIcon,
  Check,
  CheckCheck,
  Clock,
  Download,
  Forward,
  MapPin,
  Mic,
  MoreHorizontal,
  Pause,
  Pencil,
  Play,
  Plus,
  Reply,
  Sparkles,
  Star,
  StickyNote,
  Timer,
  Trash2,
} from 'lucide-react'

import { Avatar, Button } from '@/Singra/UI'
import {
  ChatMediaFile,
  ChatMediaImage,
  type AudioAttachment,
  type FileAttachment,
  type ImageAttachment,
  type MedienBindungsKontext,
  type VideoNoteAttachment,
} from '@/components/social/ChatMediaAttachments'
import { CircularVideoNotePlayer } from '@/components/social/CircularVideoNotePlayer'
import { GruppenEinladungsKarte, findeEinladung } from '@/components/social/GruppenEinladungsKarte'
import { STORY_GRADIENTS } from '@/components/social/CreateStoryModal'
import { sanitizeSvg } from '@/lib/sanitizeSvg'
import { useLangdruck } from '@/hooks/useLangdruck'
import { erkenneWischen, rueckmeldung, wischWeg, WISCH_SCHWELLE_PX } from '@/lib/gesten'
import { reaktionsknoepfe, type Reaktionen } from '@/services/reaktionen'
import { hervorzuhebendeWorte, teileText, type Textstueck } from '@/services/erwaehnungen'
import type { ChatGroupItem } from '@/api/social'
// Die Stecknadel ist rot, weil eine Stecknadel rot ist — kein Fehler, keine
// Gefahr. Für so etwas gibt es keinen Status-Token, und genau dafür ist die
// Palette da: der einzige Ort, an dem eine Roh-Farbe stehen darf.
import { farbwahl } from '@/config/farbpalette'

const ORT_TON = farbwahl('rose')

// Was diese Komponente anzeigt, wird hier auch beschrieben — dieselbe Regel,
// nach der die Anhangstypen bei `ChatMediaAttachments` stehen. `Messenger.tsx`
// reicht sie weiter, damit der bisherige Importweg bleibt.

export interface NoteAttachment {
  title: string
  content: string
  color?: string
  category?: string
}

export interface CalendarAttachment {
  title: string
  start: string
  end: string
  description?: string
  location?: string
}

export interface StickerAttachment {
  id: string
  label: string
  svg: string
}

export interface StoryReplyAttachment {
  storyId?: number
  storyContent: string
  storyMediaUrl?: string | null
  storyBackground?: string
  storyUsername?: string
}

/**
 * Das Zitat über einer Antwort.
 *
 * Der Auszug reist **mit**, statt beim Anzeigen nachgeschlagen zu werden. Sonst
 * stünde das Zitat leer, sobald die Ursprungszeile auf diesem Gerät nie ankam
 * (hundert Umschläge weiter) oder inzwischen gelöscht wurde.
 */
export interface AntwortBezug {
  clientUuid: string
  absenderId: number
  absenderName?: string
  /** Höchstens 120 Zeichen, beim Senden gekürzt. */
  auszug: string
}

export interface ChatMessage {
  id: number
  clientUuid?: string
  senderId: number
  senderName?: string
  text: string
  createdAt: string
  isSelf: boolean
  isDelivered?: boolean
  isRead?: boolean
  isEdited?: boolean
  editedAt?: string
  isDeleted?: boolean
  deletedAt?: string
  originalText?: string
  noteAttachment?: NoteAttachment
  calendarAttachment?: CalendarAttachment
  imageAttachment?: ImageAttachment
  audioAttachment?: AudioAttachment
  fileAttachment?: FileAttachment
  stickerAttachment?: StickerAttachment
  storyReply?: StoryReplyAttachment
  videoNoteAttachment?: VideoNoteAttachment
  videoUrl?: string
  status?: 'queued' | 'sent' | 'delivered' | 'read'

  /** Zeichen → wer damit reagiert hat. Siehe `services/reaktionen.ts`. */
  reaktionen?: Reaktionen
  /** Worauf diese Nachricht antwortet. */
  antwortAuf?: AntwortBezug
  /** Konto-Ids, **nicht** Namen: eine Umbenennung darf die Hervorhebung nicht brechen. */
  erwaehnungen?: number[]
  /** Ob `@everyone` gemeint war — und ob der Absender das durfte, prüft der Empfänger. */
  erwaehntAlle?: boolean
  /** Weitergeleitet, also nicht hier entstanden. */
  weitergeleitet?: boolean
  /** Mit Sternchen markiert. Rein lokal, geht nie über den Server. */
  istMarkiert?: boolean
  /** Ab wann diese Nachricht von selbst verschwindet (ISO). */
  verfaelltAm?: string
  /**
   * Eine Zeile des Messengers selbst, kein Gesprächsbeitrag. Bisher nur für den
   * Sitzungsbruch: sie gehört mitten in den Verlauf, weil sie genau dort
   * hingehört, wo die Lücke ist.
   *
   * Solche Zeilen kommen gar nicht erst bis hierher — der Verlauf rendert sie
   * als schmalen Hinweis statt als Sprechblase.
   */
  isSystem?: boolean
}

/** Sekunden als `m:ss`. */
function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${s < 10 ? '0' : ''}${s}`
}

/**
 * Die Balken der Sprachnachricht.
 *
 * Aus der Nachrichtenkennung gewürfelt und damit für dieselbe Nachricht immer
 * gleich. Eine echte Hüllkurve müsste die Aufnahme entschlüsseln, bevor
 * überhaupt jemand auf Abspielen gedrückt hat.
 */
function getWaveformBars(msgId: number, count = 28): number[] {
  const bars: number[] = []
  let seed = (Math.abs(msgId) || 1) * 9301 + 49297
  for (let i = 0; i < count; i++) {
    seed = (seed * 9301 + 49297) % 233280
    const rand = seed / 233280
    // Leiser am Anfang und Ende, die Spitzen in der Mitte — so sieht Sprache aus.
    const pos = i / (count - 1)
    const envelope = Math.sin(pos * Math.PI) * 0.45 + 0.55
    const height = Math.max(0.2, Math.min(1.0, (0.2 + rand * 0.8) * envelope))
    bars.push(height)
  }
  return bars
}

/** Wer schreibt hier mit wem. */
export interface BlasenKontext {
  activeGroup: ChatGroupItem | null
  /** Nur das, was an der Blase gebraucht wird, nicht der ganze Kontakteintrag. */
  activeContact: { username: string; avatarUrl?: string | null } | null
  eigeneId: number
  eigenerName: string
  eigenesBild?: string | null
  /** Ohne Lesebestätigungen bleibt es beim einfachen Haken. */
  readReceiptsEnabled: boolean
  /** Schon in eigene Notizen oder den eigenen Kalender übernommen. */
  importedAttachmentIds: Set<string>
  /** Ob ich von dieser Nachricht gemeint bin — färbt die ganze Blase. */
  michGemeint?: boolean
  /** Hervorgehoben, weil gerade hierher gesprungen oder gesucht wurde. */
  hervorgehoben?: boolean
}

/** Der Auswahlmodus: aus der Kopfzeile wird eine Aktionsleiste. */
export interface AuswahlZustand {
  aktiv: boolean
  gewaehlt: boolean
  onUmschalten: (msg: ChatMessage) => void
}

/** Der Stand der Tonwiedergabe, die für alle Blasen gemeinsam läuft. */
export interface TonZustand {
  playingAudioId: number | null
  audioCurrentTime: number
  audioPlaybackRate: number
  onTogglePlay: (messageId: number, anhang: AudioAttachment, bindung: MedienBindungsKontext) => void
  onCycleRate: (e: React.MouseEvent) => void
  onSeek: (
    messageId: number,
    anhang: AudioAttachment,
    bindung: MedienBindungsKontext,
    e: React.MouseEvent<HTMLDivElement>,
  ) => void
}

/** Was sich an einer Nachricht tun lässt. */
export interface BlasenAktionen {
  onViewImage: (url: string) => void
  onEdit: (msg: ChatMessage) => void
  onDelete: (msg: ChatMessage) => void
  onImportNote: (note: NoteAttachment, itemKey: string) => void
  onImportCalendar: (cal: CalendarAttachment, itemKey: string) => void
  onJoinByInviteCode: (inviteCode: string) => void | Promise<void>
  /** Langes Drücken: das volle Menü zu dieser Nachricht. */
  onMenue: (msg: ChatMessage) => void
  /** Wischen nach rechts, oder aus dem Menü heraus. */
  onAntworten: (msg: ChatMessage) => void
  /** Ein Zeichen in der Leiste antippen schaltet die eigene Reaktion um. */
  onReaktion: (msg: ChatMessage, emoji: string) => void
  /** Klick auf den Zitatkopf: hin zur zitierten Nachricht. */
  onSpringeZu: (clientUuid: string) => void
}

export interface ChatMessageBubbleProps {
  msg: ChatMessage
  kontext: BlasenKontext
  ton: TonZustand
  aktionen: BlasenAktionen
  auswahl: AuswahlZustand
  medienBindung: (msg: ChatMessage) => MedienBindungsKontext
}

export function safeFormatDate(val: unknown, options?: Intl.DateTimeFormatOptions, fallback = ''): string {
  if (!val) return fallback
  const d = new Date(val as string | number)
  return isNaN(d.getTime()) ? fallback : d.toLocaleString([], options)
}

export function safeFormatTime(val: unknown, options?: Intl.DateTimeFormatOptions, fallback = ''): string {
  if (!val) return fallback
  const d = new Date(val as string | number)
  return isNaN(d.getTime()) ? fallback : d.toLocaleTimeString([], options)
}

export class MessageErrorBoundary extends React.Component<
  { children: React.ReactNode; fallback?: React.ReactNode },
  { hasError: boolean }
> {
  constructor(props: { children: React.ReactNode; fallback?: React.ReactNode }) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError() {
    return { hasError: true }
  }

  componentDidCatch(error: any) {
    console.warn('[Messenger] Fehler beim Rendern einer Nachricht abgefangen:', error)
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div className="p-3 my-1 rounded-xl bg-status-destructive/10 border border-status-destructive/30 text-xs text-status-destructive flex items-center gap-2">
            <span>Nachricht konnte nicht dargestellt werden</span>
          </div>
        )
      )
    }
    return this.props.children
  }
}

function ChatMessageBubbleContent({
  msg,
  kontext,
  ton,
  aktionen,
  auswahl,
  medienBindung,
}: ChatMessageBubbleProps) {
  const { t } = useTranslation()
  const {
    activeGroup,
    activeContact,
    eigeneId,
    eigenerName,
    eigenesBild,
    readReceiptsEnabled,
    importedAttachmentIds,
    michGemeint,
    hervorgehoben,
  } = kontext

  /**
   * Am Telefon aufgeklappt, am Rechner beim Darüberfahren.
   */
  // Das Menü gilt für jede Nachricht. Bearbeiten und Löschen stehen darin
  // ohnehin nur bei eigenen — Antworten, Weiterleiten, Markieren und
  // Reagieren betreffen fremde genauso. Im Auswahlmodus ruht es, dort
  // bedeutet jeder Tipper schon etwas.
  const darfHandeln = !msg.isDeleted && !auswahl.aktiv
  const [wischX, setWischX] = useState(0)

  /**
   * Langes Drücken öffnet das Menü, nicht bloß die zwei Knöpfe.
   *
   * Reagieren, Antworten, Weiterleiten, Markieren, Auswählen — das passt nicht
   * mehr neben die Uhrzeit. Am Rechner bleibt die kleine Leiste als Abkürzung
   * unter dem Zeiger; im Auswahlmodus ruht der Langdruck, weil dort jeder
   * Tipper schon etwas bedeutet.
   */
  const langdruck = useLangdruck(
    () => (msg.isDeleted ? undefined : aktionen.onMenue(msg)),
    { aktiv: !msg.isDeleted && !auswahl.aktiv },
  )


  /**
   * Wischen nach rechts antwortet — die wichtigste Geste des Messengers.
   *
   * Der Rechenteil steht in `lib/gesten.ts`: bis zur Schwelle folgt die Blase
   * eins zu eins, danach zäh und gedeckelt. Der Widerstand ist die Rückmeldung
   * „hier ist die Grenze"; ohne ihn muss man raten, ob die Geste schon zählt.
   */
  const wischStart = useRef<{ x: number; y: number } | null>(null)
  const hatGeruettelt = useRef(false)

  const wischBindung = msg.isDeleted
    ? {}
    : {
        onPointerDown: (e: React.PointerEvent) => {
          if (e.pointerType === 'mouse') return
          wischStart.current = { x: e.clientX, y: e.clientY }
          hatGeruettelt.current = false
        },
        onPointerMove: (e: React.PointerEvent) => {
          const start = wischStart.current
          if (!start) return
          const dx = e.clientX - start.x
          const dy = e.clientY - start.y
          // Nur nach rechts, und nur wenn es waagerecht gemeint war.
          if (dx <= 0 || Math.abs(dx) < Math.abs(dy)) {
            if (wischX) setWischX(0)
            return
          }
          const weg = wischWeg(dx)
          setWischX(weg)
          if (!hatGeruettelt.current && weg >= WISCH_SCHWELLE_PX) {
            hatGeruettelt.current = true
            rueckmeldung()
          }
        },
        onPointerUp: (e: React.PointerEvent) => {
          const start = wischStart.current
          wischStart.current = null
          setWischX(0)
          if (!start) return
          if (erkenneWischen(e.clientX - start.x, e.clientY - start.y) === 'rechts') {
            aktionen.onAntworten(msg)
          }
        },
        onPointerCancel: () => {
          wischStart.current = null
          setWischX(0)
        },
      }

  const knoepfe = reaktionsknoepfe(msg.reaktionen, eigeneId)
  const erwaehnungsWorte = activeGroup ? hervorzuhebendeWorte(msg, activeGroup) : new Set<string>()

  return (
    <div
      // Der Anker, über den ein Sprung aus der Suche oder aus einem Zitat
      // hierher findet. Die logische Kennung, nicht die Umschlagkennung: die
      // ist je Zielgerät eine andere.
      data-nachricht={msg.clientUuid || undefined}
      className={`group flex flex-col transition-colors rounded-xl ${
        msg.isSelf ? 'items-end' : 'items-start'
      } ${hervorgehoben ? 'bg-primary/15 ring-1 ring-primary/40' : ''} ${
        auswahl.gewaehlt ? 'bg-primary/10' : ''
      }`}
      style={wischX ? { transform: `translateX(${wischX}px)` } : undefined}
      onClick={auswahl.aktiv ? () => auswahl.onUmschalten(msg) : undefined}
      {...langdruck}
      onPointerDown={(e) => {
        langdruck.onPointerDown(e)
        wischBindung.onPointerDown?.(e)
      }}
      onPointerMove={(e) => {
        langdruck.onPointerMove(e)
        wischBindung.onPointerMove?.(e)
      }}
      onPointerUp={(e) => {
        langdruck.onPointerUp()
        wischBindung.onPointerUp?.(e)
      }}
      onPointerCancel={() => {
        langdruck.onPointerCancel()
        wischBindung.onPointerCancel?.()
      }}
    >
      {/* Der Pfeil, der beim Wischen hinter der Blase auftaucht. */}
      {wischX > 8 && (
        <div
          className="absolute left-2 self-start flex items-center text-primary pointer-events-none"
          style={{ opacity: Math.min(1, wischX / WISCH_SCHWELLE_PX) }}
          aria-hidden="true"
        >
          <Reply className="w-4 h-4" />
        </div>
      )}

      {/* Auswahlmodus: der Haken steht vor der Blase, die ganze Zeile trifft. */}
      {auswahl.aktiv && (
        <div className={`flex items-center gap-2 mb-1 ${msg.isSelf ? 'flex-row-reverse' : ''}`}>
          <span
            className={`w-6 h-6 rounded-full border flex items-center justify-center shrink-0 transition-colors ${
              auswahl.gewaehlt
                ? 'bg-primary border-primary text-on-primary'
                : 'border-outline-variant/60 text-transparent'
            }`}
            aria-hidden="true"
          >
            <Check className="w-3.5 h-3.5" />
          </span>
        </div>
      )}

      {/* Weitergeleitet: steht über der Blase, nicht darin — es ist eine
          Aussage über die Herkunft, kein Teil der Nachricht. */}
      {!msg.isDeleted && msg.weitergeleitet && (
        <div className="flex items-center gap-1 text-label-sm text-on-surface-variant/70 italic mb-0.5 px-1">
          <Forward className="w-3 h-3" />
          <span>Weitergeleitet</span>
        </div>
      )}

      <div
        className={`max-w-[85%] md:max-w-[70%] px-3.5 py-2 rounded-2xl text-xs break-words shadow-sm space-y-2 ${
          msg.isSelf
            ? 'bg-[#0c2e35] text-[#f0fdfa] rounded-br-xs border border-[#164e5c]/60 shadow-sm'
            : 'bg-surface-container-high text-on-surface rounded-bl-xs border border-outline-variant/20 shadow-sm'
        } ${michGemeint ? 'ring-1 ring-primary/70 border-primary/40' : ''}`}
      >
        {/* Group sender name if in group and not self */}
        {activeGroup && !msg.isSelf && (
          <div className="text-label-sm font-bold text-tertiary">
            {msg.senderName || t('messenger.userNumbered', { id: msg.senderId })}
          </div>
        )}

        {/* Zitat über einer Antwort. Der Auszug reist mit der Nachricht, damit
            er auch dann steht, wenn die Ursprungszeile hier nie ankam. */}
        {!msg.isDeleted && msg.antwortAuf && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              aktionen.onSpringeZu(msg.antwortAuf!.clientUuid)
            }}
            className={`w-full text-left flex gap-2 rounded-lg px-2 py-1.5 border-l-2 transition-colors ${
              msg.isSelf
                ? 'bg-black/25 border-white/50 hover:bg-black/35'
                : 'bg-surface-container-highest border-primary/60 hover:bg-surface-container-highest/70'
            }`}
            title={t('messenger.jumpToQuoted')}
          >
            <span className="min-w-0 flex-1 space-y-0.5">
              <span className="block text-label-sm font-semibold text-primary truncate">
                {msg.antwortAuf.absenderName ||
                  (Number(msg.antwortAuf.absenderId) === Number(eigeneId) ? eigenerName : 'Nachricht')}
              </span>
              <span className="block text-label-sm opacity-80 line-clamp-2 leading-snug">
                {typeof msg.antwortAuf.auszug === 'string'
                  ? msg.antwortAuf.auszug
                  : typeof msg.antwortAuf.auszug === 'object' && msg.antwortAuf.auszug !== null
                    ? JSON.stringify(msg.antwortAuf.auszug)
                    : String(msg.antwortAuf.auszug ?? '')}
              </span>
            </span>
          </button>
        )}

        {/* Image Attachment */}
        {!msg.isDeleted && msg.imageAttachment && (
          <ChatMediaImage
            attachment={msg.imageAttachment}
            bindung={medienBindung(msg)}
            onViewImage={aktionen.onViewImage}
            isSelf={msg.isSelf}
          />
        )}

        {/* File Attachment Card */}
        {!msg.isDeleted && msg.fileAttachment && (
          <ChatMediaFile
            attachment={msg.fileAttachment}
            bindung={medienBindung(msg)}
            isSelf={msg.isSelf}
          />
        )}

        {/* Sticker Attachment */}
        {!msg.isDeleted && msg.stickerAttachment && (
          <div className="py-1">
            <div
              className="w-24 h-24 sm:w-28 sm:h-28 drop-shadow-md"
              dangerouslySetInnerHTML={{ __html: sanitizeSvg(msg.stickerAttachment.svg) }}
              title={msg.stickerAttachment.label}
            />
            <div className="text-label-sm opacity-60 text-center mt-1">{msg.stickerAttachment.label}</div>
          </div>
        )}

        {/* Audio / Voice Message Attachment (WhatsApp Style) */}
        {!msg.isDeleted && msg.audioAttachment && (
          <div
            className={`flex items-center gap-2.5 p-2 rounded-2xl min-w-[240px] max-w-[320px] ${
              msg.isSelf ? 'bg-black/20 text-white' : 'bg-surface-container-high/90 text-on-surface'
            }`}
          >
            {/* Sender Profile Picture on Left (WhatsApp-style: transitions to speed toggle button when playing) */}
            {ton.playingAudioId === msg.id ? (
              <button
                type="button"
                onClick={ton.onCycleRate}
                className={`w-10 h-10 rounded-full font-bold text-xs shadow-sm flex items-center justify-center shrink-0 hover:scale-105 active:scale-95 transition-all ${
                  msg.isSelf
                    ? 'bg-white text-[#0c2e35] hover:bg-white/90'
                    : 'bg-primary text-on-primary hover:opacity-90'
                }`}
                title={t('messenger.playbackSpeed')}
                aria-label={t('messenger.playbackSpeed')}
              >
                {ton.audioPlaybackRate}x
              </button>
            ) : (
              <div
                className="relative shrink-0 w-10 h-10 rounded-full cursor-pointer"
                onClick={ton.onCycleRate}
                title={t('messenger.playbackSpeed')}
              >
                <Avatar
                  src={msg.isSelf ? eigenesBild : activeContact?.avatarUrl || null}
                  name={msg.isSelf ? eigenerName : msg.senderName || activeContact?.username || t('messenger.userFallback')}
                  size="md"
                  className="w-10 h-10"
                />
                <button
                  type="button"
                  onClick={ton.onCycleRate}
                  className={`absolute -bottom-1 -right-1 px-1 py-0.5 rounded-full font-bold text-label-sm shadow-sm border border-surface leading-none hover:scale-110 transition-transform ${
                    msg.isSelf ? 'bg-white text-[#0c2e35]' : 'bg-primary text-on-primary'
                  }`}
                  title={t('messenger.playbackSpeed')}
                  aria-label={t('messenger.playbackSpeed')}
                >
                  {ton.audioPlaybackRate}x
                </button>
              </div>
            )}

            {/* Play / Pause Button */}
            <button
              type="button"
              onClick={() => ton.onTogglePlay(msg.id, msg.audioAttachment!, medienBindung(msg))}
              className={`w-8 h-8 rounded-full shrink-0 shadow-sm flex items-center justify-center transition-all ${
                msg.isSelf
                  ? 'bg-white text-[#0c2e35] hover:bg-white/90'
                  : 'bg-primary text-on-primary hover:opacity-90'
              }`}
              aria-label={ton.playingAudioId === msg.id ? 'Pause' : 'Abspielen'}
            >
              {ton.playingAudioId === msg.id ? (
                <Pause className="w-3.5 h-3.5" />
              ) : (
                <Play className="w-3.5 h-3.5 translate-x-0.5" />
              )}
            </button>

            {/* Dynamic Audio Waveform with Click-to-Seek */}
            <div
              className="flex-1 min-w-[130px] space-y-1 cursor-pointer select-none"
              onClick={(e) => ton.onSeek(msg.id, msg.audioAttachment!, medienBindung(msg), e)}
              title={t('messenger.seekHint')}
            >
              <div className="flex items-center gap-[2.5px] h-7 px-0.5">
                {getWaveformBars(msg.id).map((barH, bIdx) => {
                  const count = 28
                  const progress =
                    ton.playingAudioId === msg.id && msg.audioAttachment!.durationSeconds > 0
                      ? ton.audioCurrentTime / msg.audioAttachment!.durationSeconds
                      : 0
                  const barProgress = bIdx / count
                  const isPlayed = barProgress <= progress

                  return (
                    <div
                      key={bIdx}
                      className={`flex-1 rounded-full transition-colors ${
                        isPlayed
                          ? msg.isSelf
                            ? 'bg-white'
                            : 'bg-primary'
                          : msg.isSelf
                          ? 'bg-white/35'
                          : 'bg-on-surface-variant/35'
                      }`}
                      style={{
                        height: `${Math.max(4, Math.round(barH * 24))}px`,
                        minWidth: '2px',
                        maxWidth: '4px',
                      }}
                    />
                  )
                })}
              </div>

              <div className="flex justify-between items-center text-label-sm opacity-80 px-0.5">
                <span>
                  {ton.playingAudioId === msg.id
                    ? formatDuration(ton.audioCurrentTime)
                    : formatDuration(msg.audioAttachment.durationSeconds)}
                </span>
                <span className="flex items-center gap-1 opacity-70">
                  <Mic className="w-2.5 h-2.5" />
                  <span>Sprachnachricht</span>
                </span>
              </div>
            </div>
          </div>
        )}

        {/* Circular Video Note Attachment */}
        {!msg.isDeleted && msg.videoNoteAttachment && (
          <div className="py-1">
            <CircularVideoNotePlayer
              attachment={msg.videoNoteAttachment}
              bindung={medienBindung(msg)}
              videoUrl={msg.videoUrl}
            />
          </div>
        )}

        {/* Note Attachment Card */}
        {!msg.isDeleted && msg.noteAttachment && (
          <div
            className={`p-3 rounded-xl border text-xs shadow-sm space-y-2.5 ${
              msg.isSelf
                ? 'bg-background/80 border-white/20 text-white'
                : 'bg-surface-container-lowest border-outline-variant/50 text-on-surface'
            }`}
          >
            <div
              className={`flex items-center justify-between gap-2 border-b pb-2 ${
                msg.isSelf ? 'border-white/15' : 'border-outline-variant/30'
              }`}
            >
              <div className="flex items-center gap-1.5 font-bold text-xs truncate">
                <StickyNote className="w-3.5 h-3.5 text-status-warning shrink-0" />
                <span className="truncate text-white font-medium">{msg.noteAttachment.title || t('messenger.noteTitleFallback')}</span>
              </div>
              {(() => {
                const noteKey = `note_${msg.id}_${msg.noteAttachment!.title}`
                const isImported = importedAttachmentIds.has(noteKey)
                return (
                  <Button
                    type="button"
                    variant={msg.isSelf ? 'secondary' : 'primary'}
                    size="sm"
                    disabled={isImported}
                    onClick={() => aktionen.onImportNote(msg.noteAttachment!, noteKey)}
                    className={`h-6 px-2.5 text-label-sm gap-1 shrink-0 rounded-full font-medium ${
                      isImported
                        ? 'opacity-60 cursor-default bg-white/10 text-white border-none'
                        : msg.isSelf
                        ? 'bg-white/20 hover:bg-white/30 text-white border-none'
                        : 'bg-primary text-on-primary hover:bg-primary/90'
                    }`}
                    title={isImported ? t('messenger.noteTakenAlready') : t('messenger.takeNote')}
                  >
                    {isImported ? <Check className="w-3 h-3 text-status-success" /> : <Download className="w-3 h-3" />}
                    <span>{isImported ? t('common.taken') : t('common.apply')}</span>
                  </Button>
                )
              })()}
            </div>
            <p className="whitespace-pre-wrap text-label-sm text-white/90 line-clamp-4 leading-relaxed font-sans">
              {msg.noteAttachment.content}
            </p>
          </div>
        )}

        {/* Calendar Attachment Card */}
        {!msg.isDeleted && msg.calendarAttachment && (
          <div
            className={`p-3 rounded-xl border text-xs shadow-sm space-y-2.5 ${
              msg.isSelf
                ? 'bg-background/80 border-white/20 text-white'
                : 'bg-surface-container-lowest border-outline-variant/50 text-on-surface'
            }`}
          >
            <div
              className={`flex items-center justify-between gap-2 border-b pb-2 ${
                msg.isSelf ? 'border-white/15' : 'border-outline-variant/30'
              }`}
            >
              <div className="flex items-center gap-1.5 font-bold text-xs truncate">
                <div className="w-5 h-5 rounded-md bg-primary/20 flex items-center justify-center shrink-0">
                  <CalendarIcon className="w-3.5 h-3.5 text-primary" />
                </div>
                <span className="truncate text-white font-medium">
                  {msg.calendarAttachment.title || t('messenger.eventTitleFallback')}
                </span>
              </div>
              {(() => {
                const calKey = `cal_${msg.id}_${msg.calendarAttachment!.title}`
                const isImported = importedAttachmentIds.has(calKey)
                return (
                  <Button
                    type="button"
                    variant={msg.isSelf ? 'secondary' : 'primary'}
                    size="sm"
                    disabled={isImported}
                    onClick={() => aktionen.onImportCalendar(msg.calendarAttachment!, calKey)}
                    className={`h-6 px-2.5 text-label-sm gap-1 shrink-0 rounded-full font-medium ${
                      isImported
                        ? 'opacity-60 cursor-default bg-white/10 text-white border-none'
                        : msg.isSelf
                        ? 'bg-white/20 hover:bg-white/30 text-white border-none'
                        : 'bg-primary text-on-primary hover:bg-primary/90'
                    }`}
                    title={
                      isImported ? t('messenger.eventTakenAlready') : t('messenger.takeEvent')
                    }
                  >
                    {isImported ? <Check className="w-3 h-3 text-status-success" /> : <Plus className="w-3 h-3" />}
                    <span>{isImported ? t('messenger.eventEntered') : t('messenger.enterEvent')}</span>
                  </Button>
                )
              })()}
            </div>
            <div className="text-label-sm text-white/90 flex items-center gap-1.5 font-medium">
              <Clock className="w-3.5 h-3.5 text-primary shrink-0" />
              <span>
                {safeFormatDate(msg.calendarAttachment.start, {
                  dateStyle: 'short',
                  timeStyle: 'short',
                })}
              </span>
            </div>
            {msg.calendarAttachment.location && (
              <div className="text-label-sm text-white/80 flex items-center gap-1.5">
                <MapPin className={`w-3.5 h-3.5 shrink-0 ${ORT_TON.text}`} />
                <span>{msg.calendarAttachment.location}</span>
              </div>
            )}
            {msg.calendarAttachment.description && (
              <p className="whitespace-pre-wrap text-label-sm text-white/90 line-clamp-3 leading-relaxed font-sans pt-0.5">
                {msg.calendarAttachment.description}
              </p>
            )}
          </div>
        )}

        {/* Quoted Story Reply Preview */}
        {!msg.isDeleted && msg.storyReply && (
          <div
            className={`mb-2 p-2 rounded-xl border flex items-center justify-between gap-2.5 overflow-hidden text-xs select-none transition-all ${
              msg.isSelf
                ? 'bg-black/25 border-white/20 text-white'
                : 'bg-surface-container-highest border-outline-variant/30 text-on-surface'
            }`}
          >
            <div className="min-w-0 flex-1 space-y-0.5">
              <div className="flex items-center gap-1.5 text-label-sm font-semibold text-primary">
                <Sparkles className="w-3 h-3 text-primary shrink-0" />
                <span className="truncate">
                  {t('messenger.storyFrom', {
                    name: msg.storyReply.storyUsername || t('messenger.contactFallback'),
                  })}
                </span>
              </div>
              <p className="line-clamp-2 text-label-sm opacity-85 leading-snug">
                {msg.storyReply.storyContent || t('social.story.fallbackContent')}
              </p>
            </div>
            {msg.storyReply.storyMediaUrl ? (
              <img
                src={msg.storyReply.storyMediaUrl}
                alt="Status"
                className="w-11 h-11 rounded-lg object-cover shrink-0 border border-white/10"
              />
            ) : (
              <div
                className={`w-11 h-11 rounded-lg shrink-0 flex items-center justify-center text-label-sm font-bold text-white shadow-sm ${
                  STORY_GRADIENTS[msg.storyReply.storyBackground || 'gradient-1']?.class || 'bg-surface-container-high'
                }`}
              >
                Status
              </div>
            )}
          </div>
        )}

        {/* Fallback preview for legacy [Antwort auf Status]: messages */}
        {!msg.isDeleted && !msg.storyReply && msg.text.startsWith('[Antwort auf Status]:') && (
          <div
            className={`mb-1.5 p-1.5 px-2 rounded-lg border flex items-center gap-1.5 overflow-hidden text-label-sm select-none ${
              msg.isSelf
                ? 'bg-black/25 border-white/20 text-white'
                : 'bg-surface-container-highest border-outline-variant/30 text-on-surface'
            }`}
          >
            <Sparkles className="w-3 h-3 text-primary shrink-0" />
            <span className="font-semibold text-primary truncate">{t('messenger.statusReply')}</span>
          </div>
        )}

        {/* Text content or Deleted indicator */}
        {msg.isDeleted ? (
          <div className="flex items-center gap-2 py-0.5 italic opacity-85">
            <Trash2 className="w-3.5 h-3.5 shrink-0 opacity-70" />
            <span>{t('messenger.messageWasDeleted')}</span>
          </div>
        ) : (
          msg.text && (
            <div className="space-y-1">
              <p className="leading-relaxed">
                {(() => {
                  const roh = msg.text.startsWith('[Antwort auf Status]:')
                    ? msg.text.replace(/^\[Antwort auf Status\]:\s*"?/, '').replace(/"?$/, '')
                    : msg.text
                  // Ohne Erwähnung bleibt es bei genau einem Textknoten —
                  // dieselbe Ausgabe wie bisher.
                  const stuecke: Textstueck[] = teileText(roh, erwaehnungsWorte)
                  if (stuecke.length === 1 && stuecke[0].art === 'text') return stuecke[0].inhalt
                  return stuecke.map((s, i) =>
                    s.art === 'erwaehnung' ? (
                      <span
                        key={i}
                        className={`font-semibold rounded px-0.5 ${
                          msg.isSelf ? 'bg-white/20 text-white' : 'bg-primary/20 text-primary'
                        }`}
                      >
                        {s.inhalt}
                      </span>
                    ) : (
                      <React.Fragment key={i}>{s.inhalt}</React.Fragment>
                    ),
                  )
                })()}
              </p>
              {(() => {
                // Einladungslink im Text: statt der rohen URL eine
                // Karte mit Logo, Name und Beitreten-Knopf.
                const einladung = findeEinladung(msg.text, window.location.origin)
                if (!einladung) return null
                return (
                  <GruppenEinladungsKarte
                    inviteCode={einladung.code}
                    // Der Schlüssel steht hinter der Raute im Link, und der
                    // Link steht in dieser Nachricht. Ohne ihn bliebe eine
                    // verschlüsselte Karte zu.
                    schluessel={einladung.schluessel}
                    istEigene={msg.isSelf}
                    onJoin={aktionen.onJoinByInviteCode}
                  />
                )
              })()}
              {msg.isEdited && (
                <span className="text-label-sm opacity-70 italic inline-flex items-center gap-1">
                  <Pencil className="w-2.5 h-2.5" />
                  <span>bearbeitet</span>
                </span>
              )}
            </div>
          )
        )}
      </div>

      {/* Die Reaktionsleiste.
          Sitzt unter der Blase und überlappt sie leicht, damit klar ist, wozu
          sie gehört. Ein Tipper schaltet die eigene Reaktion um. Wer sehen
          will, wer reagiert hat, öffnet das Menü — ein Tooltip ist am Telefon
          nichts.

          Sichtbare Pille und Trefferfläche sind getrennt: gemessen war der
          Knopf 34 × 32 Pixel, also unter dem, was ein Daumen sicher trifft.
          Der Knopf ist jetzt 44 × 44 und holt sich diese Höhe über einen
          negativen Außenabstand zurück, damit die Zeile so flach bleibt wie
          vorher. Eine überstehende Trefferfläche käme billiger, würde sich
          aber mit der Nachbarpille überschneiden — dann landet der Tipper auf
          dem falschen Zeichen. */}
      {!msg.isDeleted && knoepfe.length > 0 && (
        <div className={`flex flex-wrap gap-1 -mt-1 px-1 ${msg.isSelf ? 'justify-end' : 'justify-start'}`}>
          {knoepfe.map((k) => (
            <button
              key={k.emoji}
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                aktionen.onReaktion(msg, k.emoji)
              }}
              className="h-11 min-w-11 -my-1.5 flex items-center justify-center"
              aria-label={`${k.emoji}, ${k.anzahl} ${k.anzahl === 1 ? 'Reaktion' : 'Reaktionen'}${k.eigene ? ', eigene' : ''}`}
              aria-pressed={k.eigene}
            >
              <span
                className={`min-h-8 px-2 py-0.5 rounded-full border text-xs flex items-center gap-1 transition-colors ${
                  k.eigene
                    ? 'bg-primary/20 border-primary/50 text-on-surface'
                    : 'bg-surface-container-high border-outline-variant/30 text-on-surface-variant hover:bg-surface-container-highest'
                }`}
              >
                <span aria-hidden="true">{k.emoji}</span>
                {k.anzahl > 1 && <span className="text-label-sm font-semibold tabular-nums">{k.anzahl}</span>}
              </span>
            </button>
          ))}
        </div>
      )}

      <div className="flex items-center justify-end gap-1.5 text-label-sm text-on-surface-variant/60 mt-1 px-1">
        {msg.istMarkiert && (
          <Star className="w-3 h-3 text-status-warning fill-status-warning" aria-label={t('messenger.marked')} />
        )}
        {msg.verfaelltAm && (
          <Timer className="w-3 h-3 opacity-70" aria-label={t('messenger.disappears')} />
        )}
        {/* Der Weg ins Menü für alles, was mit dieser Nachricht geht.
            Am Telefon öffnet langes Drücken dasselbe Menü; hier steht der
            sichtbare Knopf daneben, denn eine Geste allein findet niemand und
            mit der Maus gibt es keinen Langdruck. */}
        {darfHandeln && (
          <div
            // Wo es kein Hover gibt, heißt unsichtbar auch unantastbar: sonst
            // läge am Telefon eine unsichtbare Fläche neben der Uhrzeit.
            className="transition-opacity flex items-center mr-1 opacity-0
              [@media(hover:none)]:pointer-events-none group-hover:opacity-100 focus-within:opacity-100"
            // Ein Druck auf die Leiste selbst darf sie nicht schließen, bevor
            // der Klick angekommen ist.
            onPointerDown={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              onClick={() => aktionen.onMenue(msg)}
              className="w-8 h-8 rounded-md hover:bg-surface-container-highest text-on-surface-variant hover:text-primary transition-colors flex items-center justify-center"
              title={t('common.more')}
              aria-label={t('messenger.messageActions')}
            >
              <MoreHorizontal className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        <span>
          {safeFormatTime(msg.createdAt, {
            hour: '2-digit',
            minute: '2-digit',
          })}
        </span>
        {msg.isSelf &&
          (msg.status === 'queued' ? (
            <span title={t('messenger.stateQueued')} className="inline-flex items-center">
              <Clock className="w-3.5 h-3.5 opacity-60 animate-pulse" />
            </span>
          ) : activeGroup ? (
            // Gruppen verschicken keine Quittungen. Ein einzelner Haken hiesse
            // hier „noch nicht zugestellt“, und das stimmt nicht.
            null
          ) : msg.isRead && readReceiptsEnabled ? (
            <span title={t('messenger.stateRead')} className="inline-flex items-center">
              <CheckCheck className="w-3.5 h-3.5 text-primary" />
            </span>
          ) : msg.isDelivered ? (
            <span title={t('messenger.stateDelivered')} className="inline-flex items-center">
              <CheckCheck className="w-3.5 h-3.5 opacity-60" />
            </span>
          ) : (
            <span
              title={t('messenger.stateUndelivered')}
              className="inline-flex items-center"
            >
              <Check className="w-3.5 h-3.5 opacity-60" />
            </span>
          ))}
      </div>
    </div>
  )
}

export function ChatMessageBubble(props: ChatMessageBubbleProps) {
  return (
    <MessageErrorBoundary>
      <ChatMessageBubbleContent {...props} />
    </MessageErrorBoundary>
  )
}
