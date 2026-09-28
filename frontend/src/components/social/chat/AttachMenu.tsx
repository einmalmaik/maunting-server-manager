import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Calendar as CalendarIcon,
  Camera,
  Image as ImageIcon,
  Paperclip,
  Plus,
  StickyNote,
  type LucideIcon,
} from 'lucide-react'

interface AttachMenuProps {
  /** Ohne das Recht, Anhänge zu senden, bleibt der Knopf gesperrt. */
  erlaubt: boolean
  onKamera: () => void
  onFoto: () => void
  onDokument: () => void
  onNotiz: () => void
  onTermin: () => void
}

/** Der Plus-Knopf in der Eingabeleiste samt seinem Menü. */
export function AttachMenu({ erlaubt, onKamera, onFoto, onDokument, onNotiz, onTermin }: AttachMenuProps) {
  const { t } = useTranslation()
  const [offen, setOffen] = useState(false)
  const huelle = useRef<HTMLDivElement>(null)

  // Ein Klick daneben schliesst das Menü.
  useEffect(() => {
    if (!offen) return
    const beiKlick = (e: MouseEvent) => {
      if (huelle.current && !huelle.current.contains(e.target as Node)) setOffen(false)
    }
    document.addEventListener('mousedown', beiKlick)
    return () => document.removeEventListener('mousedown', beiKlick)
  }, [offen])

  const eintraege: { Icon: LucideIcon; titel: string; hinweis: string; aria: string; aktion: () => void }[] = [
    { Icon: Camera, titel: t('social.camera.take'), hinweis: t('messenger.cameraSnapshot'), aria: t('messenger.attachPhoto'), aktion: onKamera },
    { Icon: ImageIcon, titel: t('messenger.photo'), hinweis: t('messenger.fromGallery'), aria: t('messenger.pickPhoto'), aktion: onFoto },
    { Icon: Paperclip, titel: t('messenger.document'), hinweis: t('messenger.sendEncrypted'), aria: t('messenger.attachFile'), aktion: onDokument },
    { Icon: StickyNote, titel: t('messenger.attachNote'), hinweis: t('messenger.fromNotes'), aria: t('messenger.shareNote'), aktion: onNotiz },
    { Icon: CalendarIcon, titel: t('messenger.attachEvent'), hinweis: t('messenger.fromCalendar'), aria: t('messenger.shareEvent'), aktion: onTermin },
  ]
  const knopfText = erlaubt ? t('messenger.addAttachment') : t('messenger.attachNoRight')

  return (
    <div className="relative shrink-0" ref={huelle}>
      <button
        type="button"
        disabled={!erlaubt}
        onClick={() => setOffen((vorher) => !vorher)}
        className={`w-11 h-11 sm:w-8 sm:h-8 shrink-0 flex items-center justify-center rounded-full transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
          offen ? 'bg-surface-container-highest text-primary' : 'text-on-surface-variant hover:text-primary'
        }`}
        title={knopfText}
        aria-label={knopfText}
      >
        <Plus className={`w-4 h-4 transition-transform duration-200 ${offen ? 'rotate-45 text-primary' : ''}`} />
      </button>

      {offen && (
        <div className="absolute bottom-10 left-0 z-30 min-w-[210px] p-1.5 rounded-2xl bg-surface-container-high/95 backdrop-blur-md border border-outline-variant/30 shadow-xl space-y-1 animate-slide-up">
          {eintraege.map(({ Icon, titel, hinweis, aria, aktion }) => (
            <button
              key={aria}
              type="button"
              onClick={() => {
                setOffen(false)
                aktion()
              }}
              className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-left hover:bg-surface-container-highest/80 transition-colors group"
              aria-label={aria}
            >
              <div className="w-7 h-7 rounded-lg bg-primary/15 text-primary flex items-center justify-center shrink-0 group-hover:scale-105 transition-transform">
                <Icon className="w-4 h-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-xs font-semibold text-primary">{titel}</div>
                <div className="text-label-sm text-on-surface-variant/70">{hinweis}</div>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
