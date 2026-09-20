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
import { Archive, ArchiveRestore, Pin, PinOff } from 'lucide-react'

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
    <div className="relative overflow-hidden rounded-xl">
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
    </div>
  )
}
