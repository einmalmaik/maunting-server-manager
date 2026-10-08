/**
 * Verlauf und Browserdaten: wie lange der Verlauf bleibt und Löschen nach
 * Zeitraum. Gelöscht wird beides zugleich, Seitendaten in Rust
 * (`seitendaten_loeschen`), der Verlauf hier.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, confirm } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import { useEinstellungenStore, type VerlaufFrist } from '../services/einstellungenStore'
import { nativ } from '../services/nativ'
import { istAndroid } from '../services/plattform'
import { useVerlaufStore } from '../services/verlaufStore'
import { Abschnitt, Auswahlzeile } from './bausteine'

const STUNDE = 60 * 60 * 1000

type Zeitraum = 'stunde' | 'tag' | 'woche' | 'monat' | 'alles'

const ZEITRAEUME: Record<Zeitraum, number | null> = {
  stunde: STUNDE,
  tag: 24 * STUNDE,
  woche: 7 * 24 * STUNDE,
  monat: 28 * 24 * STUNDE,
  alles: null,
}

const FRISTEN: VerlaufFrist[] = ['immer', 'woche', 'monat', 'halbjahr', 'jahr']

export function VerlaufDaten() {
  const { t } = useTranslation()
  const frist = useEinstellungenStore((s) => s.verlaufBehalten)
  const setzen = useEinstellungenStore((s) => s.setzen)
  // Android löscht Browserdaten nur ganz (`browserdaten.rs`).
  const android = istAndroid()
  const [gewaehlt, setZeitraum] = useState<Zeitraum>('stunde')
  const zeitraum = android ? 'alles' : gewaehlt

  const loeschen = async () => {
    const dauer = ZEITRAEUME[zeitraum]
    const ja = await confirm({
      title: t('browser.einstellungen.datenLoeschenTitel'),
      message: t('browser.einstellungen.datenLoeschenText', { zeitraum: t(`browser.einstellungen.zeitraum.${zeitraum}`) }),
      confirmText: t('browser.einstellungen.loeschen'),
      danger: true,
    })
    if (!ja) return
    const seit = dauer === null ? undefined : Date.now() - dauer
    try {
      await nativ.seitendatenLoeschen(seit)
      useVerlaufStore.getState().verlaufLeeren(seit)
      toast.success(t('browser.einstellungen.datenGeloescht'))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <Abschnitt titel={t('browser.einstellungen.kategorie.verlauf')}>
      <Auswahlzeile<VerlaufFrist>
        name={t('browser.einstellungen.verlaufBehalten')}
        hinweis={t('browser.einstellungen.verlaufBehaltenHinweis')}
        wert={frist}
        optionen={FRISTEN.map((f) => ({ value: f, label: t(`browser.einstellungen.frist.${f}`) }))}
        aendern={(v) => setzen({ verlaufBehalten: v })}
      />
      {android ? (
        <p className="text-body-sm text-on-surface-variant">{t('browser.einstellungen.datenLoeschenAndroid')}</p>
      ) : (
        <Auswahlzeile<Zeitraum>
          name={t('browser.einstellungen.datenLoeschen')}
          hinweis={t('browser.einstellungen.datenLoeschenHinweis')}
          wert={zeitraum}
          optionen={(Object.keys(ZEITRAEUME) as Zeitraum[]).map((z) => ({ value: z, label: t(`browser.einstellungen.zeitraum.${z}`) }))}
          aendern={setZeitraum}
        />
      )}
      <Button variant="secondary" size="sm" className="self-end" onClick={() => void loeschen()}>
        {t('browser.einstellungen.loeschen')}
      </Button>
    </Abschnitt>
  )
}
