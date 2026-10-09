import { useTranslation } from 'react-i18next'

import { normalizePanelLanguage, panelLanguageCodes, type PanelLanguageCode } from '@/config/panelLocales'

import { useEinstellungenStore } from '../services/einstellungenStore'
import { istTauri } from '../services/nativ'
import { Abschnitt, Auswahlzeile } from './bausteine'
import { Standardbrowser } from './Standardbrowser'
import { UebersetzungEinstellungen } from '../uebersetzung/UebersetzungEinstellungen'

export function Allgemein() {
  const { t, i18n } = useTranslation()
  const beimStart = useEinstellungenStore((s) => s.beimStart)
  const setzen = useEinstellungenStore((s) => s.setzen)
  return (
    <>
      <Abschnitt titel={t('browser.einstellungen.kategorie.allgemein')}>
        <Auswahlzeile
          name={t('browser.einstellungen.beimStart')}
          wert={beimStart}
          optionen={[
            { value: 'letzte', label: t('browser.einstellungen.start.letzte') },
            { value: 'startseite', label: t('browser.einstellungen.start.startseite') },
          ]}
          aendern={(v) => setzen({ beimStart: v })}
        />
        <Auswahlzeile<PanelLanguageCode>
          name={t('browser.einstellungen.sprache')}
          wert={normalizePanelLanguage(i18n.language)}
          optionen={panelLanguageCodes.map((code) => ({ value: code, label: t(`browser.einstellungen.spracheName.${code}`) }))}
          aendern={(code) => void i18n.changeLanguage(code)}
        />
        {istTauri() && <Standardbrowser />}
      </Abschnitt>
      <UebersetzungEinstellungen />
    </>
  )
}
