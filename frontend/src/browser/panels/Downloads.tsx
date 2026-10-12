/**
 * Die Downloads dieser Sitzung. „Im Ordner zeigen“ öffnet den Explorer an der
 * Datei; Rust lässt das nur innerhalb des Download-Ordners zu. Unter Android
 * öffnet er die Download-Liste des Systems.
 */
import { useEffect } from 'react'
import { CheckCircle2, ExternalLink, FileDown, FolderOpen, Loader2, ShieldAlert, Trash2, XCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button, Kurzinfo } from '@/Singra/UI'
import { Zustandsflaeche } from '@/Singra/UI/Zustandsflaeche'

import { type Download, useDownloadsStore } from '../services/downloadsStore'
import { nativ } from '../services/nativ'
import { istAndroid } from '../services/plattform'

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
  const loeschen = useDownloadsStore((s) => s.loeschen)
  const android = istAndroid()

  useEffect(() => {
    gesehen()
  }, [gesehen, downloads.length])

  if (downloads.length === 0) {
    return <Zustandsflaeche art="leer" icon={<FileDown className="h-10 w-10" />} text={t('browser.downloads.leer')} />
  }

  const dateiLoeschen = async (d: Download) => {
    try {
      if (d.datei) {
        await nativ.downloadLoeschen(d.datei)
      }
    } catch (fehler) {
      console.warn('[MSB] Datei löschen fehlgeschlagen:', fehler)
    } finally {
      loeschen(d.nr)
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-between p-3 border-b border-surface-container-highest">
        {!android ? (
          <Kurzinfo text={t('browser.downloads.ordnerOeffnen')} seite="anfang">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void nativ.downloadOrdnerOeffnen()}
              className="gap-1.5"
            >
              <FolderOpen className="h-4 w-4" aria-hidden="true" />
              <span>{t('browser.downloads.ordner')}</span>
            </Button>
          </Kurzinfo>
        ) : (
          <div />
        )}
        <Button variant="ghost" size="sm" onClick={leeren}>
          {t('browser.downloads.listeLeeren')}
        </Button>
      </div>
      <ul className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
        {downloads.map((d) => {
          const name = dateiname(d.datei, d.url)
          const istFertig = d.stand === 'fertig' && !!d.datei
          return (
            <li key={d.nr} className="flex items-center gap-3 rounded-md px-2 py-2 hover:bg-surface-container-high transition-colors">
              {d.stand === 'start' || d.stand === 'pruefung' ? (
                <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" aria-hidden="true" />
              ) : d.stand === 'fertig' ? (
                <CheckCircle2 className="h-4 w-4 shrink-0 text-status-success" aria-hidden="true" />
              ) : d.stand === 'blockiert' ? (
                <ShieldAlert className="h-4 w-4 shrink-0 text-status-destructive" aria-hidden="true" />
              ) : (
                <XCircle className="h-4 w-4 shrink-0 text-status-destructive" aria-hidden="true" />
              )}
              <span className="min-w-0 flex-1">
                {istFertig ? (
                  <button
                    type="button"
                    onClick={() => void nativ.downloadOeffnen(d.datei!)}
                    className="block w-full text-left truncate text-body-sm font-medium text-on-surface hover:text-primary hover:underline cursor-pointer"
                  >
                    {name}
                  </button>
                ) : (
                  <span className="block truncate text-body-sm text-on-surface">{name}</span>
                )}
                <span className="block text-label-sm text-on-surface-variant">{t(`browser.downloads.stand.${d.stand}`)}</span>
              </span>
              <div className="flex items-center gap-1 shrink-0">
                {istFertig && (
                  <>
                    <Kurzinfo text={t('browser.downloads.oeffnen')} seite="ende">
                      <button
                        type="button"
                        onClick={() => void nativ.downloadOeffnen(d.datei!)}
                        aria-label={t('browser.downloads.oeffnenName', { name })}
                        className="flex h-7 w-7 [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-11 items-center justify-center rounded-md text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface"
                      >
                        <ExternalLink className="h-4 w-4" aria-hidden="true" />
                      </button>
                    </Kurzinfo>
                    <Kurzinfo text={t(android ? 'browser.downloads.zeigenAndroid' : 'browser.downloads.zeigen')} seite="ende">
                      <button
                        type="button"
                        onClick={() => void nativ.downloadZeigen(d.datei!)}
                        aria-label={t(android ? 'browser.downloads.zeigenNameAndroid' : 'browser.downloads.zeigenName', { name })}
                        className="flex h-7 w-7 [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-11 items-center justify-center rounded-md text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface"
                      >
                        <FolderOpen className="h-4 w-4" aria-hidden="true" />
                      </button>
                    </Kurzinfo>
                  </>
                )}
                <Kurzinfo text={t(d.datei ? 'browser.downloads.dateiLoeschen' : 'browser.downloads.entfernen')} seite="ende">
                  <button
                    type="button"
                    onClick={() => void dateiLoeschen(d)}
                    aria-label={t(d.datei ? 'browser.downloads.dateiLoeschenName' : 'browser.downloads.entfernenName', { name })}
                    className="flex h-7 w-7 [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-11 items-center justify-center rounded-md text-on-surface-variant hover:bg-surface-container-highest hover:text-status-destructive"
                  >
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                  </button>
                </Kurzinfo>
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
