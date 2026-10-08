/**
 * Jugend- und Suchtschutz: Kategorien, eigene Sperren, Ausnahmen, Bindung
 * und Wartezeit gegen schnelles Lockern, dazu die Serie ohne Sperrtreffer.
 * Die Schalter zeigen den Wunsch; was davon erst nach der Wartezeit gilt,
 * steht in `Lockerung`. Durchgesetzt in Rust (`schild/schutz.rs`,
 * `schild/sperre.rs`).
 */
import { ShieldAlert } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { toast } from '@/stores/toastStore'

import type { SchutzKategorie } from '../services/nativ'
import { istAndroid } from '../services/plattform'
import { dauerText, uebrig, useSchutz } from '../services/schutz'
import { Abschnitt, Auswahlzeile, Schalterzeile } from './bausteine'
import { Bindung } from './Bindung'
import { Hostliste } from './Hostliste'
import { fehlerText, Lockerung, wartezeitName } from './Lockerung'

const KATEGORIEN: SchutzKategorie[] = ['erwachsene', 'gluecksspiel', 'sozial', 'spiele', 'shopping']
/** Wie `WARTEZEITEN` in `schutz.rs`. */
const WARTEZEITEN = ['24', '72', '168'] as const
/** Wie `HOSTS_MAX` in `schutz.rs`. */
const HOSTS_MAX = 500

export function Jugendschutz() {
  const { t } = useTranslation()
  const { stand, wunsch, vergangen, aendern, binden, abbrechen, bestaetigen } = useSchutz()
  if (!stand || !wunsch) return null

  const setzen = (teil: Parameters<typeof aendern>[0]) => void aendern(teil).catch((e: unknown) => toast.error(fehlerText(t, e)))
  const umschalten = (k: SchutzKategorie, an: boolean) =>
    setzen((w) => ({ kategorien: an ? [...w.kategorien, k] : w.kategorien.filter((x) => x !== k) }))
  const ungeladen = stand.listen.filter((l) => l.nachgeladen && l.alter_sekunden === null && stand.regeln.aktiv && stand.regeln.kategorien.includes(l.kategorie))
  const abkuehlen = uebrig(stand.abkuehlen_sekunden, vergangen)

  return (
    <>
      {stand.beschaedigt && (
        <section className="msm-card flex items-start gap-2 border border-status-destructive/40 p-5" role="alert">
          <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-status-destructive" aria-hidden="true" />
          <p className="text-body-sm text-on-surface">{t('browser.schutz.beschaedigt')}</p>
        </section>
      )}
      <Lockerung stand={stand} vergangen={vergangen} abbrechen={abbrechen} bestaetigen={bestaetigen} />
      <Abschnitt titel={t('browser.einstellungen.kategorie.jugendschutz')}>
        <Schalterzeile name={t('browser.schutz.an')} hinweis={t('browser.schutz.anHinweis')} an={wunsch.aktiv} aendern={(an) => setzen({ aktiv: an })} />
        {stand.regeln.aktiv && (
          <p className="text-body-sm text-on-surface">
            {t('browser.schutz.serie', { count: stand.serie.tage })}{' '}
            <span className="text-on-surface-variant">{t('browser.schutz.rekord', { count: stand.serie.rekord })}</span>
          </p>
        )}
        <p className="text-label-sm text-on-surface-variant">{t('browser.schutz.grenzen')}</p>
      </Abschnitt>
      {stand.regeln.aktiv && (
        <Abschnitt titel={t('browser.schutz.huerdeTitel')}>
          <Bindung gebunden={uebrig(stand.gebunden_sekunden, vergangen)} binden={binden} />
          <Auswahlzeile<string>
            name={t('browser.schutz.wartezeit')}
            hinweis={t('browser.schutz.wartezeitHinweis')}
            wert={String(wunsch.wartezeit_stunden)}
            optionen={WARTEZEITEN.map((w) => ({ value: w, label: wartezeitName(t, Number(w)) }))}
            aendern={(w) => setzen({ wartezeit_stunden: Number(w) })}
          />
          {abkuehlen > 0 && <p className="text-label-sm text-on-surface-variant">{t('browser.schutz.abkuehlen', { zeit: dauerText(t, abkuehlen) })}</p>}
        </Abschnitt>
      )}
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
    </>
  )
}
