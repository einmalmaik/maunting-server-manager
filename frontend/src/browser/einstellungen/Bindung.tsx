/**
 * Die Selbstbindung des Jugend- und Suchtschutzes: für die gewählte Zeit
 * lässt sich nichts lockern, nicht einmal beantragen, danach nur per Antrag.
 * Ohne Bindung gilt Lockern sofort. Verlängern geht jederzeit, verkürzen nie.
 * Durchgesetzt in Rust (`Schutz::binden`).
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button, Dropdown } from '@/Singra/UI'
import { confirm } from '@/stores/confirmStore'
import { toast } from '@/stores/toastStore'

import { dauerText } from '../services/schutz'
import { Aktionszeile } from './bausteine'
import { fehlerText } from './Lockerung'

/** Wie `BINDUNGEN` in `schutz.rs`. */
const TAGE = ['1', '7', '30', '90'] as const

export function Bindung({
  gebunden,
  ungebunden,
  binden,
}: {
  gebunden: number
  ungebunden: boolean
  binden: (tage: number) => Promise<unknown>
}) {
  const { t } = useTranslation()
  const [tage, setTage] = useState<string>('7')
  const name = (w: string) => t('browser.schutz.bindungTage', { count: Number(w) })

  const los = async () => {
    const ja = await confirm({
      title: t('browser.schutz.bindungFrage', { dauer: name(tage) }),
      message: t('browser.schutz.bindungText'),
      confirmText: t(gebunden > 0 ? 'browser.schutz.verlaengern' : 'browser.schutz.binden'),
    })
    if (ja) await binden(Number(tage)).catch((e: unknown) => toast.error(fehlerText(t, e)))
  }

  return (
    <Aktionszeile
      name={t('browser.schutz.bindung')}
      hinweis={
        gebunden > 0
          ? t('browser.schutz.gebundenNoch', { zeit: dauerText(t, gebunden) })
          : t(ungebunden ? 'browser.schutz.nieGebunden' : 'browser.schutz.nichtGebunden')
      }
    >
      <Dropdown
        aria-label={t('browser.schutz.bindungDauer')}
        value={tage}
        onChange={setTage}
        options={TAGE.map((w) => ({ value: w, label: name(w) }))}
        className="w-36"
      />
      <Button type="button" variant="secondary" onClick={() => void los()}>
        {t(gebunden > 0 ? 'browser.schutz.verlaengern' : 'browser.schutz.binden')}
      </Button>
    </Aktionszeile>
  )
}
