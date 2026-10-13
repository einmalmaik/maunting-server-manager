/**
 * Die Downloads dieser Sitzung. „Im Ordner zeigen“ öffnet den Explorer an der
 * Datei; Rust lässt das nur innerhalb des Download-Ordners zu. Unter Android
 * öffnet er die Download-Liste des Systems.
 *
 * Laufende Downloads zeigen echten Fortschritt (Balken, Bytes, Rate, „ca.“-
 * Restzeit) statt eines unbestimmten Ladens. Ab 15 Sekunden ohne neue Bytes
 * steht ein Wartehinweis dabei — ohne Schuldzuweisung und ohne automatischen
 * Abbruch. „Abbrechen“ bricht den Transfer wirklich ab (`DownloadOperation.Cancel`);
 * die Datei-Löschen-Schaltfläche gibt es erst am abgeschlossenen Eintrag.
 */
import { useEffect, useState } from 'react'
import { CheckCircle2, ExternalLink, FileDown, FolderOpen, Loader2, ShieldAlert, Trash2, X, XCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button, Kurzinfo, ProgressBar } from '@/Singra/UI'

import { Zustandsflaeche } from '@/Singra/UI/Zustandsflaeche'

import { type Download, useDownloadsStore } from '../services/downloadsStore'
import { nativ } from '../services/nativ'
import { istAndroid } from '../services/plattform'

/** Ab so vielen Sekunden ohne neue Bytes steht ein Wartehinweis dabei. */
const STILLSTAND_S = 15

function dateiname(pfad: string | null, url: string): string {
  if (pfad) return pfad.split(/[\\/]/).pop() || pfad
  try {
    return decodeURIComponent(new URL(url).pathname.split('/').pop() || url)
  } catch {
    return url
  }
}

function bytesText(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '–'
  if (bytes < 1024) return `${bytes} B`
  const einheiten = ['KB', 'MB', 'GB', 'TB']
  let wert = bytes / 1024
  let einheit = einheiten[0]
  for (const e of einheiten) {
    einheit = e
    if (wert < 1024 || e === 'TB') break
    wert /= 1024
  }
  return `${wert.toFixed(wert < 10 ? 1 : 0)} ${einheit}`
}

function dauerText(sekunden: number): string {
  const s = Math.max(0, Math.round(sekunden))
  if (s < 60) return `${s} s`
  const minuten = Math.floor(s / 60)
  if (minuten < 60) return `${minuten} min`
  return `${Math.floor(minuten / 60)} h ${minuten % 60} min`
}

function laeuft(d: Download): boolean {
  return d.stand === 'start' || d.stand === 'pruefung'
}

export function DownloadsPanel() {
  const { t } = useTranslation()
  const downloads = useDownloadsStore((s) => s.downloads)
  const gesehen = useDownloadsStore((s) => s.gesehen)
  const leeren = useDownloadsStore((s) => s.leeren)
  const loeschen = useDownloadsStore((s) => s.loeschen)
  const android = istAndroid()
  const [jetzt, setJetzt] = useState(() => Date.now())
  const etwasLaeuft = downloads.some(laeuft)

  useEffect(() => {
    gesehen()
  }, [gesehen, downloads.length])

  useEffect(() => {
    if (!etwasLaeuft) return
    const tick = window.setInterval(() => setJetzt(Date.now()), 5000)
    return () => window.clearInterval(tick)
  }, [etwasLaeuft])

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

  const abbrechen = async (nr: number) => {
    try {
      await nativ.downloadAbbrechen(nr)
    } catch (fehler) {
      console.warn('[MSB] Download abbrechen fehlgeschlagen:', fehler)
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
          const aktiv = laeuft(d)
          const anteil =
            d.empfangen != null && d.gesamt != null && d.gesamt > 0
              ? Math.max(0, Math.min(100, (d.empfangen / d.gesamt) * 100))
              : null
          const wartetSeit =
            aktiv && d.letzteBytesZeit != null ? Math.max(0, Math.round((jetzt - d.letzteBytesZeit) / 1000)) : null
          const steht = wartetSeit != null && wartetSeit >= STILLSTAND_S
          const details: string[] = []
          if (d.empfangen != null) {
            details.push(
              d.gesamt != null
                ? t('browser.downloads.von', { empfangen: bytesText(d.empfangen), gesamt: bytesText(d.gesamt) })
                : t('browser.downloads.nurEmpfangen', { empfangen: bytesText(d.empfangen) }),
            )
          }
          if (d.bytesProSekunde != null) {
            details.push(t('browser.downloads.rate', { rate: bytesText(Math.round(d.bytesProSekunde)) }))
          }
          if (d.restSekunden != null) {
            details.push(t('browser.downloads.eta', { zeit: dauerText(d.restSekunden) }))
          }
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
                {aktiv && (details.length > 0 || anteil != null) && (
                  <span className="mt-1 block">
                    <ProgressBar
                      value={anteil}
                      ariaLabel={t('browser.downloads.fortschrittName', { name })}
                      data-testid={`download-fortschritt-${d.nr}`}
                    />
                    {details.length > 0 && (
                      <span className="mt-0.5 block text-label-sm text-on-surface-variant">{details.join(' · ')}</span>
                    )}
                  </span>
                )}
                {steht && (
                  <span className="mt-0.5 block text-label-sm text-on-surface-variant">
                    {t('browser.downloads.wartet', { zeit: dauerText(wartetSeit ?? 0) })}
                  </span>
                )}
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
                {aktiv ? (
                  <Kurzinfo text={t('browser.downloads.abbrechen')} seite="ende">
                    <button
                      type="button"
                      onClick={() => void abbrechen(d.nr)}
                      aria-label={t('browser.downloads.abbrechenName', { name })}
                      className="flex h-7 w-7 [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-11 items-center justify-center rounded-md text-on-surface-variant hover:bg-surface-container-highest hover:text-status-destructive"
                    >
                      <X className="h-4 w-4" aria-hidden="true" />
                    </button>
                  </Kurzinfo>
                ) : (
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
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
