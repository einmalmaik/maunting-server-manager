/**
 * Was die Übersetzungsleiste (`Uebersetzungsleiste.tsx`) zeigt: Sprachen
 * wählen, Zustimmung zum Laden, Fortschritt, „Original anzeigen“.
 */
import { Languages } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button, Dropdown, ProgressBar } from '@/Singra/UI'
import { formatBytes } from '@/lib/format'

import { Leiste } from '../seite/FormularRahmen'
import { useUebersetzung, type Eintrag } from './ablauf'
import { quellen, weg } from './modelle'

/** Die Sprachen, in die der Browser übersetzt: die der App. */
const ZIELE = ['de', 'en']

export function Leisteninhalt({ tab, e }: { tab: string; e: Eintrag }) {
  const { t, i18n } = useTranslation()
  const { waehlen, starten, original, schliessen } = useUebersetzung.getState()
  const namen = new Intl.DisplayNames([i18n.language], { type: 'language' })
  const name = (s: string) => (s ? (namen.of(s) ?? s) : '')
  const leiste = (text: string, kinder?: React.ReactNode) => (
    <Leiste
      text={text}
      name={t('browser.uebersetzung.name')}
      symbol={Languages}
      schliessenText={t('browser.uebersetzung.schliessen')}
      onSchliessen={() => schliessen(tab)}
    >
      {kinder}
    </Leiste>
  )

  switch (e.stand.art) {
    case 'erkennen':
      return leiste(t('browser.uebersetzung.erkennen'))
    case 'laden': {
      const { geladen, gesamt } = e.stand
      const prozent = gesamt ? Math.round((geladen / gesamt) * 100) : 0
      return leiste(
        t('browser.uebersetzung.laden', { geladen: formatBytes(geladen), gesamt: formatBytes(gesamt) }),
        <ProgressBar value={prozent} ariaLabel={t('browser.uebersetzung.ladenName')} className="w-32" />,
      )
    }
    case 'uebersetzen':
      return leiste(t('browser.uebersetzung.laeuft', { count: e.stand.texte }))
    case 'fertig':
      return leiste(
        t('browser.uebersetzung.fertig', { von: name(e.von), nach: name(e.nach) }),
        <Button size="sm" variant="secondary" onClick={() => original(tab)}>
          {t('browser.uebersetzung.original')}
        </Button>,
      )
    case 'fehler':
      return leiste(
        t(`browser.uebersetzung.fehler.${e.stand.grund}`),
        <Button size="sm" variant="secondary" onClick={() => void starten(tab)}>
          {t('browser.uebersetzung.nochmal')}
        </Button>,
      )
    case 'wahl': {
      const { fehlt } = e.stand
      const moeglich = !!weg(e.von, e.nach)
      const text = !moeglich
        ? t('browser.uebersetzung.waehlen')
        : fehlt
          ? t('browser.uebersetzung.zustimmung', { groesse: formatBytes(fehlt) })
          : t('browser.uebersetzung.bereit')
      return leiste(
        text,
        <>
          <Dropdown
            aria-label={t('browser.uebersetzung.von')}
            placeholder={t('browser.uebersetzung.vonLeer')}
            value={e.von || null}
            onChange={(von) => void waehlen(tab, { von })}
            options={quellen(e.nach).map((s) => ({ value: s, label: name(s) }))}
            searchable
          />
          <Dropdown
            aria-label={t('browser.uebersetzung.nach')}
            value={e.nach}
            onChange={(nach) => void waehlen(tab, { nach })}
            options={ZIELE.map((s) => ({ value: s, label: name(s) }))}
          />
          <Button size="sm" disabled={!moeglich} onClick={() => void starten(tab)}>
            {fehlt ? t('browser.uebersetzung.ladenUndUebersetzen') : t('browser.uebersetzung.uebersetzen')}
          </Button>
        </>,
      )
    }
  }
}
