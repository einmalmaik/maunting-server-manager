import { useEffect, useState } from 'react'
import { getVersion } from '@tauri-apps/api/app'
import { useTranslation } from 'react-i18next'

import { Button } from '@/Singra/UI'
import { DATENSCHUTZ_VERSION } from '@/pages/datenschutzStand'

import { istTauri } from '../services/nativ'
import { useTabsStore } from '../services/tabsStore'
import { Abschnitt, Aktionszeile } from './bausteine'
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
      <Aktionszeile name={t('browser.einstellungen.datenschutz')} hinweis={t('browser.einstellungen.datenschutzStand', { version: DATENSCHUTZ_VERSION })}>
        <Button variant="secondary" size="sm" onClick={() => einstellungen(DATENSCHUTZ_TEIL)}>
          {t('browser.einstellungen.oeffnen')}
        </Button>
      </Aktionszeile>
    </Abschnitt>
  )
}
