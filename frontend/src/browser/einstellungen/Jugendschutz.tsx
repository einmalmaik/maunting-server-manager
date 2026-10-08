/**
 * Jugend- und Suchtschutz: Kategorien, eigene Sperren, Ausnahmen und die
 * Hürde gegen schnelles Lockern. Die Schalter zeigen den Wunsch; was davon
 * erst nach der Hürde gilt, steht in `Lockerung`. Durchgesetzt in Rust
 * (`schild/schutz.rs`, `schild/sperre.rs`).
 */
import { useTranslation } from 'react-i18next'

import { toast } from '@/stores/toastStore'

import type { Huerde, SchutzKategorie, SchutzRegeln } from '../services/nativ'
import { istAndroid } from '../services/plattform'
import { useSchutz } from '../services/schutz'
import { Abschnitt, Auswahlzeile, Schalterzeile } from './bausteine'
import { Hostliste } from './Hostliste'
import { huerdeName, Lockerung } from './Lockerung'

const KATEGORIEN: SchutzKategorie[] = ['erwachsene', 'gluecksspiel', 'sozial', 'spiele', 'shopping']
const HUERDEN = ['5', '15', '60', '1440', 'abtippen'] as const
/** Wie `HOSTS_MAX` in `schutz.rs`. */
const HOSTS_MAX = 500

function huerdeAus(wert: string): Huerde {
  return wert === 'abtippen' ? { art: 'abtippen' } : { art: 'countdown', minuten: Number(wert) }
}

export function Jugendschutz() {
  const { t } = useTranslation()
  const { stand, wunsch, rest, aendern, abbrechen, bestaetigen } = useSchutz()
  if (!stand || !wunsch) return null

  const setzen = (teil: Partial<SchutzRegeln>) => void aendern({ ...wunsch, ...teil }).catch(() => toast.error(t('browser.schutz.fehler')))
  const umschalten = (k: SchutzKategorie, an: boolean) =>
    setzen({ kategorien: an ? [...wunsch.kategorien, k] : wunsch.kategorien.filter((x) => x !== k) })
  const ungeladen = stand.listen.filter((l) => l.nachgeladen && l.alter_sekunden === null && stand.regeln.aktiv && stand.regeln.kategorien.includes(l.kategorie))

  return (
    <>
      <Lockerung stand={stand} rest={rest} abbrechen={abbrechen} bestaetigen={bestaetigen} />
      <Abschnitt titel={t('browser.einstellungen.kategorie.jugendschutz')}>
        <Schalterzeile name={t('browser.schutz.an')} hinweis={t('browser.schutz.anHinweis')} an={wunsch.aktiv} aendern={(an) => setzen({ aktiv: an })} />
        <p className="text-label-sm text-on-surface-variant">{t('browser.schutz.grenzen')}</p>
      </Abschnitt>
      <Abschnitt titel={t('browser.schutz.kategorien')}>
        {KATEGORIEN.map((k) => (
          <Schalterzeile
            key={k}
            name={t(`browser.schutz.kategorie.${k}`)}
            // Unter Android lässt sich die Kopfzeile für YouTube nicht setzen.
            hinweis={t(`browser.schutz.kategorieHinweis.${k === 'erwachsene' && istAndroid() ? 'erwachseneAndroid' : k}`)}
            an={wunsch.kategorien.includes(k)}
            aendern={(an) => umschalten(k, an)}
          />
        ))}
        {ungeladen.length > 0 && <p className="text-label-sm text-on-surface-variant">{t('browser.schutz.listeLaedt')}</p>}
      </Abschnitt>
      <Hostliste
        titel={t('browser.schutz.eigene')}
        hinweis={t('browser.schutz.eigeneHinweis')}
        eintraege={wunsch.eigene}
        setzen={(eigene) => setzen({ eigene })}
        max={HOSTS_MAX}
      />
      <Hostliste
        titel={t('browser.schutz.ausnahmen')}
        hinweis={t('browser.schutz.ausnahmenHinweis')}
        eintraege={wunsch.ausnahmen}
        setzen={(ausnahmen) => setzen({ ausnahmen })}
        max={HOSTS_MAX}
      />
      <Abschnitt titel={t('browser.schutz.huerdeTitel')}>
        <Auswahlzeile<string>
          name={t('browser.schutz.huerde')}
          hinweis={t('browser.schutz.huerdeHinweis')}
          wert={wunsch.huerde.art === 'abtippen' ? 'abtippen' : String(wunsch.huerde.minuten)}
          optionen={HUERDEN.map((w) => ({ value: w, label: huerdeName(t, huerdeAus(w)) }))}
          aendern={(w) => setzen({ huerde: huerdeAus(w) })}
        />
      </Abschnitt>
    </>
  )
}
