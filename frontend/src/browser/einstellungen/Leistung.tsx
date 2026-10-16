/**
 * Leistung: wann Tabs im Hintergrund schlafen, ob lange ruhende ihren
 * Speicher ganz freigeben, und Seiten, die immer wach bleiben. Durchgesetzt
 * in Rust (`tabs/ruhe.rs`), geschickt von `services/leistung.ts`.
 *
 * Ebenso Steuerung der GPU-Hardwarebeschleunigung (`konfig.rs`).
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/Singra/UI'

import { SCHLAFEN_NACH, useEinstellungenStore, type SchlafenNach } from '../services/einstellungenStore'
import { useGeraetKonfig } from '../services/geraetKonfig'
import { nativ } from '../services/nativ'
import { Abschnitt, Auswahlzeile, Schalterzeile } from './bausteine'
import { Hostliste } from './Hostliste'

const AUSNAHMEN_MAX = 200

export function Leistung() {
  const { t } = useTranslation()
  const schlafenNach = useEinstellungenStore((s) => s.schlafenNach)
  const speicherSparen = useEinstellungenStore((s) => s.speicherSparen)
  const ausnahmen = useEinstellungenStore((s) => s.schlafAusnahmen)
  const setzen = useEinstellungenStore((s) => s.setzen)

  const konfig = useGeraetKonfig((s) => s.konfig)
  const konfigLaden = useGeraetKonfig((s) => s.laden)
  const konfigAendern = useGeraetKonfig((s) => s.aendern)

  const [urspruenglicheGpu, setUrspruenglicheGpu] = useState<boolean | null>(null)

  useEffect(() => {
    void konfigLaden()
  }, [konfigLaden])

  useEffect(() => {
    if (konfig && urspruenglicheGpu === null) {
      setUrspruenglicheGpu(konfig.hardwarebeschleunigung ?? true)
    }
  }, [konfig, urspruenglicheGpu])

  const gpuAktiv = konfig?.hardwarebeschleunigung ?? true
  const neustartErforderlich = urspruenglicheGpu !== null && gpuAktiv !== urspruenglicheGpu

  return (
    <>
      <Abschnitt titel={t('browser.einstellungen.grafikLeistung')}>
        <Schalterzeile
          name={t('browser.einstellungen.hardwarebeschleunigung')}
          hinweis={t('browser.einstellungen.hardwarebeschleunigungHinweis')}
          an={gpuAktiv}
          aendern={(an) => void konfigAendern({ hardwarebeschleunigung: an })}
        />
        {neustartErforderlich && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-outline-variant/30 bg-surface-container-high/50 p-3.5">
            <p className="text-body-sm text-on-surface">
              {t('browser.einstellungen.hardwarebeschleunigungNeustart')}
            </p>
            <Button
              type="button"
              size="sm"
              onClick={() => void nativ.neustarten()}
            >
              {t('browser.einstellungen.neustarten')}
            </Button>
          </div>
        )}
      </Abschnitt>

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
