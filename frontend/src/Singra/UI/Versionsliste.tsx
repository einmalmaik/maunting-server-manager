/**
 * Frühere Fassungen einer Datei oder eines Textes: Zeitpunkt, Größe oder
 * Wortlaut, und „Wiederherstellen“.
 *
 * Woher die Fassungen kommen (Server, Tresor, Gedächtnis), weiß die Liste
 * nicht. Ohne `onWiederherstellen` zeigt sie nur an. Auf dunklem Grund
 * (Lichtbox) setzt `aufDunkel` die Farben auf Weiß.
 */
import { useTranslation } from 'react-i18next'
import { LoaderCircle } from 'lucide-react'
import { cx } from '@/utils/classNames'
import { Button } from '@/components/ui/Button'
import { formatBytes, formatZeitpunkt } from '@/lib/format'

export interface Fassung {
  id: string
  /** Zeitpunkt in Millisekunden. */
  zeit: number
  /** Größe in Byte — bei Dateien. */
  groesse?: number
  /** Der Wortlaut der Fassung — bei kurzen Texten statt der Größe. */
  text?: string
  /** Eine Zeile neben dem Zeitpunkt, etwa wer geändert hat. */
  hinweis?: string
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
  const haupt = aufDunkel ? 'text-white/90' : 'text-on-surface'
  const neben = aufDunkel ? 'text-white/55' : 'text-on-surface-variant'

  if (versionen.length === 0) return <p className={cx('text-label-sm', neben)}>{t('common.versionen.leer')}</p>
  const mitText = versionen.some((fassung) => fassung.text !== undefined)
  return (
    <ul
      aria-label={t('common.versionen.titel')}
      className={cx('space-y-1 overflow-y-auto pr-1', mitText ? 'max-h-[60vh]' : 'max-h-56')}
    >
      {versionen.map((fassung) => (
        <li
          key={fassung.id}
          className={cx(
            'flex gap-2 rounded-md px-2 py-1.5',
            fassung.text !== undefined ? 'items-start' : 'items-center',
            aufDunkel ? 'hover:bg-white/10' : 'hover:bg-surface-container-highest/70',
          )}
        >
          <div className="min-w-0 flex-1">
            <p className={cx('text-label-sm', haupt)}>
              {formatZeitpunkt(fassung.zeit, i18n.language)}
              {fassung.hinweis && <span className={neben}> · {fassung.hinweis}</span>}
            </p>
            {fassung.groesse !== undefined && (
              <p className={cx('font-mono text-label-sm', neben)}>{formatBytes(fassung.groesse)}</p>
            )}
            {fassung.text !== undefined && (
              <p className={cx('mt-0.5 whitespace-pre-wrap break-words text-sm', haupt)}>{fassung.text}</p>
            )}
          </div>
          {onWiederherstellen && (
            <Button
              variant="ghost"
              size="sm"
              fingerziel
              type="button"
              disabled={laeuft !== null}
              onClick={() => onWiederherstellen(fassung.id)}
              className={cx('text-label-sm', aufDunkel && 'text-white/85 hover:bg-white/10 hover:text-white')}
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
