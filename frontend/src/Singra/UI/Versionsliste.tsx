/**
 * Frühere Fassungen einer Datei: Zeitpunkt, Größe und „Wiederherstellen“.
 *
 * Woher die Fassungen kommen (Server, Tresor), weiß die Liste nicht. Ohne
 * `onWiederherstellen` zeigt sie nur an. Auf dunklem Grund (Lichtbox) setzt
 * `aufDunkel` die Farben auf Weiß.
 */
import { useTranslation } from 'react-i18next'
import { LoaderCircle } from 'lucide-react'
import { cx } from '@/utils/classNames'
import { Button } from '@/components/ui/Button'
import { formatBytes } from '@/components/server/fileHelpers'

export interface Fassung {
  id: string
  /** Zeitpunkt in Millisekunden. */
  zeit: number
  groesse: number
}

export interface VersionslisteProps {
  versionen: Fassung[]
  onWiederherstellen?: (id: string) => void
  /** Fassung, die gerade zurückgeholt wird; solange sind alle Knöpfe gesperrt. */
  laeuft?: string | null
  aufDunkel?: boolean
}

export function Versionsliste({ versionen, onWiederherstellen, laeuft = null, aufDunkel = false }: VersionslisteProps) {
  const { t, i18n } = useTranslation()
  const datum = new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium', timeStyle: 'short' })
  const haupt = aufDunkel ? 'text-white/90' : 'text-on-surface'
  const neben = aufDunkel ? 'text-white/55' : 'text-on-surface-variant'

  if (versionen.length === 0) return <p className={cx('text-label-sm', neben)}>{t('common.versionen.leer')}</p>
  return (
    <ul aria-label={t('common.versionen.titel')} className="max-h-56 space-y-1 overflow-y-auto pr-1">
      {versionen.map((fassung) => (
        <li
          key={fassung.id}
          className={cx('flex items-center gap-2 rounded-md px-2 py-1.5', aufDunkel ? 'hover:bg-white/10' : 'hover:bg-surface-container-highest/70')}
        >
          <div className="min-w-0 flex-1">
            <p className={cx('text-label-sm', haupt)}>{datum.format(fassung.zeit)}</p>
            <p className={cx('font-mono text-label-sm', neben)}>{formatBytes(fassung.groesse)}</p>
          </div>
          {onWiederherstellen && (
            <Button
              variant="ghost"
              type="button"
              disabled={laeuft !== null}
              onClick={() => onWiederherstellen(fassung.id)}
              className={cx('h-7 text-label-sm', aufDunkel && 'text-white/85 hover:bg-white/10 hover:text-white')}
            >
              {laeuft === fassung.id && <LoaderCircle className="mr-1 h-3 w-3 animate-spin" aria-hidden />}
              {t('common.versionen.wiederherstellen')}
            </Button>
          )}
        </li>
      ))}
    </ul>
  )
}
