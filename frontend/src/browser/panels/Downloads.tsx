/**
 * Die Downloads dieser Sitzung. „Im Ordner zeigen“ öffnet den Explorer an der
 * Datei; Rust lässt das nur innerhalb des Download-Ordners zu.
 */
import { useEffect } from 'react'
import { CheckCircle2, FileDown, FolderOpen, Loader2, XCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button, Kurzinfo } from '@/Singra/UI'
import { Zustandsflaeche } from '@/Singra/UI/Zustandsflaeche'

import { useDownloadsStore } from '../services/downloadsStore'
import { nativ } from '../services/nativ'

function dateiname(pfad: string | null, url: string): string {
  if (pfad) return pfad.split(/[\\/]/).pop() || pfad
  try {
    return decodeURIComponent(new URL(url).pathname.split('/').pop() || url)
  } catch {
    return url
  }
}

export function DownloadsPanel() {
  const { t } = useTranslation()
  const downloads = useDownloadsStore((s) => s.downloads)
  const gesehen = useDownloadsStore((s) => s.gesehen)
  const leeren = useDownloadsStore((s) => s.leeren)

  useEffect(() => {
    gesehen()
  }, [gesehen, downloads.length])

  if (downloads.length === 0) {
    return <Zustandsflaeche art="leer" icon={<FileDown className="h-10 w-10" />} text={t('browser.downloads.leer')} />
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 justify-end p-3">
        <Button variant="ghost" size="sm" onClick={leeren}>
          {t('browser.downloads.listeLeeren')}
        </Button>
      </div>
      <ul className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        {downloads.map((d) => {
          const name = dateiname(d.datei, d.url)
          return (
            <li key={`${d.url}-${d.zeit}`} className="flex items-center gap-3 rounded-md px-2 py-2 hover:bg-surface-container-high">
              {d.stand === 'start' ? (
                <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" aria-hidden="true" />
              ) : d.stand === 'fertig' ? (
                <CheckCircle2 className="h-4 w-4 shrink-0 text-status-success" aria-hidden="true" />
              ) : (
                <XCircle className="h-4 w-4 shrink-0 text-status-destructive" aria-hidden="true" />
              )}
              <span className="min-w-0 flex-1">
                <span className="block truncate text-body-sm text-on-surface">{name}</span>
                <span className="block text-label-sm text-on-surface-variant">{t(`browser.downloads.stand.${d.stand}`)}</span>
              </span>
              {d.stand === 'fertig' && d.datei && (
                <Kurzinfo text={t('browser.downloads.zeigen')} seite="ende">
                  <button
                    type="button"
                    onClick={() => void nativ.downloadZeigen(d.datei!)}
                    aria-label={t('browser.downloads.zeigenName', { name })}
                    className="flex h-7 w-7 items-center justify-center rounded-md text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface"
                  >
                    <FolderOpen className="h-4 w-4" aria-hidden="true" />
                  </button>
                </Kurzinfo>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
