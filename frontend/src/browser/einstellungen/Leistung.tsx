/**
 * Leistung: wann Tabs im Hintergrund schlafen, ob lange ruhende ihren
 * Speicher ganz freigeben, und Seiten, die immer wach bleiben. Durchgesetzt
 * in Rust (`tabs/ruhe.rs`), geschickt von `services/leistung.ts`.
 */
import { useTranslation } from 'react-i18next'

import { SCHLAFEN_NACH, useEinstellungenStore, type SchlafenNach } from '../services/einstellungenStore'
import { Abschnitt, Auswahlzeile, Schalterzeile } from './bausteine'
import { Hostliste } from './Hostliste'

const AUSNAHMEN_MAX = 200

export function Leistung() {
  const { t } = useTranslation()
  const schlafenNach = useEinstellungenStore((s) => s.schlafenNach)
  const speicherSparen = useEinstellungenStore((s) => s.speicherSparen)
  const ausnahmen = useEinstellungenStore((s) => s.schlafAusnahmen)
  const setzen = useEinstellungenStore((s) => s.setzen)
  return (
    <>
      <Abschnitt titel={t('browser.einstellungen.hintergrundTabs')}>
        <Auswahlzeile<string>
          name={t('browser.einstellungen.schlafenNach')}
          hinweis={t('browser.einstellungen.schlafenHinweis')}
          wert={String(schlafenNach)}
          optionen={SCHLAFEN_NACH.map((m) => ({ value: String(m), label: m ? t('browser.einstellungen.minuten', { count: m }) : t('browser.einstellungen.nie') }))}
          aendern={(v) => setzen({ schlafenNach: Number(v) as SchlafenNach })}
        />
        <Schalterzeile
          name={t('browser.einstellungen.speicherSparen')}
          hinweis={t('browser.einstellungen.speicherSparenHinweis')}
          an={speicherSparen}
          aendern={(an) => setzen({ speicherSparen: an })}
        />
      </Abschnitt>
      <Hostliste
        titel={t('browser.einstellungen.wachBleiben')}
        hinweis={t('browser.einstellungen.wachBleibenHinweis')}
        eintraege={ausnahmen}
        setzen={(liste) => setzen({ schlafAusnahmen: liste })}
        max={AUSNAHMEN_MAX}
      />
    </>
  )
}
