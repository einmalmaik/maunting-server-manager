import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Input } from '@/Singra/UI'

import { useEinstellungenStore } from '../services/einstellungenStore'
import { searxngBasis } from '../services/searchEngines'
import { useSuchwahl } from '../services/suchwahl'
import { Abschnitt, Auswahlzeile } from './bausteine'

export function Suche() {
  const { t } = useTranslation()
  const suchmaschine = useEinstellungenStore((s) => s.suchmaschine)
  const searxngUrl = useEinstellungenStore((s) => s.searxngUrl)
  const setzen = useEinstellungenStore((s) => s.setzen)
  const suchwahl = useSuchwahl()
  const [adresse, setAdresse] = useState(searxngUrl ?? '')
  const gueltig = !adresse.trim() || searxngBasis(adresse) !== null
  return (
    <Abschnitt titel={t('browser.einstellungen.kategorie.suche')}>
      <Auswahlzeile
        name={t('browser.start.suchmaschine')}
        hinweis={t('browser.einstellungen.suchmaschineHinweis')}
        wert={suchwahl.wert}
        optionen={suchwahl.optionen}
        aendern={suchwahl.aendern}
      />
      {suchmaschine === 'searxng' && (
        <Input
          label={t('browser.einstellungen.searxngAdresse')}
          placeholder="https://search.example.org"
          value={adresse}
          error={gueltig ? undefined : t('browser.einstellungen.searxngUngueltig')}
          onChange={(e) => setAdresse(e.target.value)}
          onBlur={() => gueltig && setzen({ searxngUrl: searxngBasis(adresse) })}
        />
      )}
    </Abschnitt>
  )
}
