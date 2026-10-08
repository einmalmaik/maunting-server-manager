import { useEffect, useState } from 'react'
import { open as ordnerWaehlen } from '@tauri-apps/plugin-dialog'
import { useTranslation } from 'react-i18next'

import { Button } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import { useGeraetKonfig } from '../services/geraetKonfig'
import { nativ } from '../services/nativ'
import { istAndroid } from '../services/plattform'
import { Abschnitt, Aktionszeile, Schalterzeile } from './bausteine'

/** Unter Android legt der Download-Dienst des Systems ab: kein Ordner, keine Rückfrage, kein Virenschutz. */
function DownloadsAndroid() {
  const { t } = useTranslation()
  return (
    <Abschnitt titel={t('browser.einstellungen.kategorie.downloads')}>
      <p className="text-body-sm text-on-surface-variant">{t('browser.einstellungen.downloadsAndroid')}</p>
    </Abschnitt>
  )
}

export function Downloads() {
  return istAndroid() ? <DownloadsAndroid /> : <DownloadsDesktop />
}

function DownloadsDesktop() {
  const { t } = useTranslation()
  const konfig = useGeraetKonfig((s) => s.konfig)
  const aendern = useGeraetKonfig((s) => s.aendern)
  const [ordner, setOrdner] = useState<string | null>(null)

  useEffect(() => {
    void nativ.downloadOrdner().then(setOrdner).catch(() => null)
  }, [konfig?.download_ordner])

  const setzen = (download_ordner: string | null) =>
    aendern({ download_ordner }).catch((e) => toast.error(e instanceof Error ? e.message : String(e)))

  const waehlen = async () => {
    const gewaehlt = await ordnerWaehlen({ directory: true, multiple: false }).catch(() => null)
    if (typeof gewaehlt === 'string') await setzen(gewaehlt)
  }

  return (
    <Abschnitt titel={t('browser.einstellungen.kategorie.downloads')}>
      <Aktionszeile name={t('browser.einstellungen.ordner')} hinweis={ordner ?? '…'}>
        {konfig?.download_ordner && (
          <Button variant="ghost" size="sm" onClick={() => void setzen(null)}>
            {t('browser.einstellungen.ordnerStandard')}
          </Button>
        )}
        <Button variant="secondary" size="sm" onClick={() => void waehlen()}>
          {t('browser.einstellungen.ordnerWaehlen')}
        </Button>
      </Aktionszeile>
      <Schalterzeile
        name={t('browser.einstellungen.downloadFragen')}
        hinweis={t('browser.einstellungen.downloadFragenHinweis')}
        an={konfig?.download_fragen ?? false}
        aendern={(download_fragen) => void aendern({ download_fragen }).catch((e) => toast.error(e instanceof Error ? e.message : String(e)))}
      />
      <p className="text-label-sm text-on-surface-variant">{t('browser.einstellungen.virenschutzHinweis')}</p>
    </Abschnitt>
  )
}
