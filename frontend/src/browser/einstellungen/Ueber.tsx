import { useEffect, useState } from 'react'
import { getVersion } from '@tauri-apps/api/app'
import { useTranslation } from 'react-i18next'

import { Button } from '@/Singra/UI'
import { DATENSCHUTZ_VERSION } from '@/pages/datenschutzStand'

import { installieren, updatesMoeglich } from '../services/aktualisierung'
import { useEinstellungenStore } from '../services/einstellungenStore'
import { istTauri, nativ } from '../services/nativ'
import { useTabsStore } from '../services/tabsStore'
import { Abschnitt, Aktionszeile, Schalterzeile } from './bausteine'
import { DATENSCHUTZ_TEIL } from './kategorien'

export function Ueber() {
  const { t } = useTranslation()
  const einstellungen = useTabsStore((s) => s.einstellungen)
  const [version, setVersion] = useState<string | null>(null)

  useEffect(() => {
    if (istTauri()) void getVersion().then(setVersion).catch(() => null)
  }, [])

  return (
    <Abschnitt titel={t('browser.einstellungen.kategorie.ueber')}>
      <div className="flex items-center gap-3">
        <img src="/msp.png" alt="" aria-hidden="true" className="h-12 w-12 rounded-full" />
        <div>
          <p className="font-headline text-title-md text-on-surface">Maunting Secure Browser</p>
          {version && <p className="text-label-sm text-on-surface-variant">{t('browser.einstellungen.version', { version })}</p>}
        </div>
      </div>
      {updatesMoeglich() && <Updates />}
      <Aktionszeile name={t('browser.einstellungen.datenschutz')} hinweis={t('browser.einstellungen.datenschutzStand', { version: DATENSCHUTZ_VERSION })}>
        <Button variant="secondary" size="sm" onClick={() => einstellungen(DATENSCHUTZ_TEIL)}>
          {t('browser.einstellungen.oeffnen')}
        </Button>
      </Aktionszeile>
    </Abschnitt>
  )
}

type Stand = { art: 'offen' } | { art: 'sucht' } | { art: 'aktuell' } | { art: 'neu'; version: string } | { art: 'fehler' }

function Updates() {
  const { t } = useTranslation()
  const suchen = useEinstellungenStore((s) => s.updatesSuchen)
  const setzen = useEinstellungenStore((s) => s.setzen)
  const [stand, setStand] = useState<Stand>({ art: 'offen' })

  const pruefen = () => {
    setStand({ art: 'sucht' })
    nativ
      .updatePruefen()
      .then((neu) => setStand(neu ? { art: 'neu', version: neu } : { art: 'aktuell' }))
      .catch(() => setStand({ art: 'fehler' }))
  }

  const hinweis =
    stand.art === 'neu'
      ? t('browser.update.da', { version: stand.version })
      : stand.art === 'aktuell'
        ? t('browser.update.aktuell')
        : stand.art === 'fehler'
          ? t('browser.update.nichtErreicht')
          : t('browser.update.hinweis')

  return (
    <>
      <Aktionszeile name={t('browser.update.name')} hinweis={hinweis}>
        {stand.art === 'neu' ? (
          <Button size="sm" onClick={() => void installieren()}>
            {t('browser.update.installieren')}
          </Button>
        ) : (
          <Button variant="secondary" size="sm" disabled={stand.art === 'sucht'} onClick={pruefen}>
            {t('browser.update.pruefen')}
          </Button>
        )}
      </Aktionszeile>
      <Schalterzeile
        name={t('browser.update.beimStart')}
        hinweis={t('browser.update.beimStartHinweis')}
        an={suchen}
        aendern={(an) => setzen({ updatesSuchen: an })}
      />
    </>
  )
}
