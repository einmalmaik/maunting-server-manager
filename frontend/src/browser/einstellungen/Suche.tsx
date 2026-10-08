import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Input } from '@/Singra/UI'

import { MarkenSymbol } from '../marken'
import { useEinstellungenStore } from '../services/einstellungenStore'
import { searxngBasis, SUCHMASCHINEN, type SuchmaschinenId } from '../services/searchEngines'
import { Abschnitt, Auswahlzeile } from './bausteine'

export function Suche() {
  const { t } = useTranslation()
  const suchmaschine = useEinstellungenStore((s) => s.suchmaschine)
  const searxngUrl = useEinstellungenStore((s) => s.searxngUrl)
  const setzen = useEinstellungenStore((s) => s.setzen)
  const [adresse, setAdresse] = useState(searxngUrl ?? '')
  const gueltig = !adresse.trim() || searxngBasis(adresse) !== null
  return (
    <Abschnitt titel={t('browser.einstellungen.kategorie.suche')}>
      <Auswahlzeile<SuchmaschinenId>
        name={t('browser.start.suchmaschine')}
        hinweis={t('browser.einstellungen.suchmaschineHinweis')}
        wert={suchmaschine}
        optionen={SUCHMASCHINEN.map((s) => ({ value: s.id, label: s.name, icon: <MarkenSymbol marke={s.marke} /> }))}
        aendern={(v) => setzen({ suchmaschine: v })}
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
