/**
 * Anpinnen und Archivieren mit dem Daumen.
 *
 * Nach rechts wischen heftet an, nach links archiviert. Das ist die Bedienung,
 * die am Telefon erwartet wird, und deshalb steht sie hier — einmal, um die
 * Zeile herum, statt in der Gruppen- und der Kontaktliste getrennt.
 *
 * **Die Geste ist nie der einzige Weg.** Derselbe Aufruf hängt im Langdruck-
 * Menü, das diese Hülle mit öffnet. Wer die Geste nicht kennt oder seine Hände
 * nicht frei bewegen kann, kommt darüber genauso hin.
 *
 * Gerechnet wird in `lib/gesten.ts`; hier steht nur, was davon ans DOM geht.
 */

import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Archive, ArchiveRestore, MoreHorizontal, Pin, PinOff } from 'lucide-react'

import {
  erkenneWischen,
  rueckmeldung,
  wischWeg,
  WISCH_SCHWELLE_PX,
  type Wischrichtung,
} from '@/lib/gesten'
import { useLangdruck } from '@/hooks/useLangdruck'

export interface ChatZeilenGesteProps {
  angeheftet: boolean
  archiviert: boolean
  onAnheften: () => void
  onArchivieren: () => void
  /** Öffnet das Menü mit denselben Aktionen — der Weg ohne Geste. */
  onMenue: () => void
  children: React.ReactNode
}

export function ChatZeilenGeste({
  angeheftet,
  archiviert,
  onAnheften,
  onArchivieren,
  onMenue,
  children,
}: ChatZeilenGesteProps) {
  const { t } = useTranslation()
  const [weg, setWeg] = useState(0)
  const start = useRef<{ x: number; y: number } | null>(null)
  /** Ob für diese Bewegung schon gerüttelt wurde. */
  const gemeldet = useRef(false)

  const langdruck = useLangdruck(onMenue)

  const richtung: Wischrichtung = erkenneWischen(weg, 0)

  const beiDown = (e: React.PointerEvent) => {
    langdruck.onPointerDown(e)
    if (e.pointerType === 'mouse') return
    start.current = { x: e.clientX, y: e.clientY }
    gemeldet.current = false
  }

  const beiMove = (e: React.PointerEvent) => {
    langdruck.onPointerMove(e)
    const angefasst = start.current
    if (!angefasst) return
    const dx = e.clientX - angefasst.x
    const dy = e.clientY - angefasst.y
    const erkannt = erkenneWischen(dx, dy)
    if (!erkannt && Math.abs(dx) < WISCH_SCHWELLE_PX / 2) {
      // Solange unklar ist, ob gewischt oder gescrollt wird, bleibt die Zeile
      // stehen. Sonst zappelt sie bei jedem Scrollen.
      setWeg(0)
      return
    }
    if (Math.abs(dx) < Math.abs(dy)) {
      start.current = null
      setWeg(0)
      return
    }
    if (erkannt && !gemeldet.current) {
      gemeldet.current = true
      rueckmeldung()
    }
    setWeg(wischWeg(dx))
  }

  const beiUp = () => {
    langdruck.onPointerUp()
    const erkannt = erkenneWischen(weg, 0)
    start.current = null
    setWeg(0)
    if (erkannt === 'rechts') onAnheften()
    else if (erkannt === 'links') onArchivieren()
  }

  const beiCancel = () => {
    langdruck.onPointerCancel()
    start.current = null
    setWeg(0)
  }

  return (
    <div className="group relative overflow-hidden rounded-xl">
      {/* Was unter der Zeile zum Vorschein kommt, während sie wandert. */}
      {weg !== 0 && (
        <div className="absolute inset-0 flex items-center justify-between px-4 pointer-events-none">
          <span
            className={`flex items-center gap-1.5 text-xs font-semibold transition-opacity ${
              weg > 0 ? 'opacity-100' : 'opacity-0'
            } ${richtung === 'rechts' ? 'text-primary' : 'text-on-surface-variant/60'}`}
          >
            {angeheftet ? <PinOff className="w-4 h-4" /> : <Pin className="w-4 h-4" />}
            <span>{angeheftet ? t('messenger.unpin') : t('messenger.pin')}</span>
          </span>
          <span
            className={`flex items-center gap-1.5 text-xs font-semibold transition-opacity ${
              weg < 0 ? 'opacity-100' : 'opacity-0'
            } ${richtung === 'links' ? 'text-tertiary' : 'text-on-surface-variant/60'}`}
          >
            <span>{archiviert ? t('messenger.unarchive') : t('messenger.archive')}</span>
            {archiviert ? <ArchiveRestore className="w-4 h-4" /> : <Archive className="w-4 h-4" />}
          </span>
        </div>
      )}

      <div
        style={{ transform: weg ? `translateX(${weg}px)` : undefined }}
        className={`relative bg-surface ${weg ? '' : 'transition-transform duration-150'}`}
        onPointerDown={beiDown}
        onPointerMove={beiMove}
        onPointerUp={beiUp}
        onPointerCancel={beiCancel}
        onContextMenu={langdruck.onContextMenu}
      >
        {children}
      </div>

      {/*
       * Der Weg ohne Geste — und mit der Maus der einzige.
       *
       * Der Modulkopf verspricht ihn seit jeher („Die Geste ist nie der
       * einzige Weg"), eingelöst war er nicht: das Menü hing allein am
       * Langdruck, und `useLangdruck` tut bei `pointerType === 'mouse'`
       * ausdrücklich nichts. Am Rechner gab es damit überhaupt keinen Zugang
       * zu Anheften, Archivieren und Stummschalten — kein Knopf, kein
       * Kontextmenü, und wischen kann eine Maus auch nicht.
       *
       * Dieselbe Bauart wie an der Nachrichtenblase: sichtbar beim Überfahren
       * oder per Tastaturfokus. Solange er unsichtbar ist, nimmt er auch keine
       * Klicks an — er liegt über dem Ungelesen-Abzeichen, und ein Tippen dort
       * soll den Chat öffnen. Am Telefon wird er nie eingeblendet; dort führt
       * der Langdruck zum selben Menü, per Tabulator ist er trotzdem
       * erreichbar.
       */}
      <div
        className="absolute right-2 top-1/2 -translate-y-1/2 opacity-0 pointer-events-none
          transition-opacity group-hover:opacity-100 group-hover:pointer-events-auto
          focus-within:opacity-100 focus-within:pointer-events-auto"
      >
        <button
          type="button"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation()
            onMenue()
          }}
          className="w-8 h-8 rounded-md bg-surface-container-high/90 text-on-surface-variant
            hover:text-primary transition-colors flex items-center justify-center"
          aria-label={t('messenger.chatActions')}
          title={t('messenger.chatActions')}
        >
          <MoreHorizontal className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}
