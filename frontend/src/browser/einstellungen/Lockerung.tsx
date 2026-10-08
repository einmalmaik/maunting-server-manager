/**
 * Eine Lockerung des Jugend- und Suchtschutzes, die noch wartet: was sich
 * ändern soll, und entweder der Countdown oder der Text zum Abtippen.
 * „Schutz behalten“ nimmt sie jederzeit zurück. Ob die Hürde genommen ist,
 * prüft Rust; Einfügen sperrt hier nur die Oberfläche.
 */
import { useState } from 'react'
import { Clock } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

import { Button, Textarea } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import type { Huerde, SchutzRegeln, SchutzStand } from '../services/nativ'
import { dauerText } from '../services/schutz'

export function huerdeName(t: TFunction, h: Huerde): string {
  if (h.art === 'abtippen') return t('browser.schutz.huerdeAbtippen')
  return h.minuten >= 60 ? t('browser.schutz.huerdeStunden', { count: h.minuten / 60 }) : t('browser.schutz.huerdeMinuten', { count: h.minuten })
}

/** Was der Antrag gegenüber den geltenden Regeln lockert, als Sätze. */
export function lockerungen(t: TFunction, alt: SchutzRegeln, ziel: SchutzRegeln): string[] {
  if (!ziel.aktiv) return [t('browser.schutz.lockerung.aus')]
  return [
    ...alt.kategorien.filter((k) => !ziel.kategorien.includes(k)).map((k) => t('browser.schutz.lockerung.nichtMehr', { name: t(`browser.schutz.kategorie.${k}`) })),
    ...alt.eigene.filter((h) => !ziel.eigene.includes(h)).map((host) => t('browser.schutz.lockerung.nichtMehr', { name: host })),
    ...ziel.ausnahmen.filter((h) => !alt.ausnahmen.includes(h)).map((host) => t('browser.schutz.lockerung.erlauben', { host })),
    ...(JSON.stringify(alt.huerde) !== JSON.stringify(ziel.huerde) ? [t('browser.schutz.lockerung.huerde', { name: huerdeName(t, ziel.huerde) })] : []),
  ]
}

function gleich(a: string, b: string): boolean {
  const woerter = (s: string) => s.trim().split(/\s+/).join(' ')
  return woerter(a) === woerter(b)
}

function Abtippen({ text, rest, bestaetigen }: { text: string; rest: number; bestaetigen: (text: string) => Promise<unknown> }) {
  const { t } = useTranslation()
  const [eingabe, setEingabe] = useState('')
  const [fehler, setFehler] = useState<string | null>(null)
  const sperren = (e: React.SyntheticEvent) => e.preventDefault()

  const senden = () => {
    if (!gleich(text, eingabe)) {
      setFehler(t('browser.schutz.textFalsch'))
      return
    }
    bestaetigen(eingabe).catch((e: unknown) => {
      setFehler(String(e) === 'zu_schnell' ? t('browser.schutz.zuSchnell', { zeit: dauerText(rest) }) : t('browser.schutz.textFalsch'))
    })
  }

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault()
        senden()
      }}
    >
      <p className="text-body-sm text-on-surface">{t('browser.schutz.abtippenHinweis')}</p>
      <p className="select-none rounded-lg bg-surface-container px-3 py-2 font-mono text-body-sm leading-relaxed text-on-surface" onCopy={sperren}>
        {text}
      </p>
      <Textarea
        aria-label={t('browser.schutz.abtippenFeld')}
        value={eingabe}
        rows={3}
        onChange={(e) => {
          setEingabe(e.target.value)
          setFehler(null)
        }}
        onPaste={sperren}
        onDrop={sperren}
        autoComplete="off"
        autoCorrect="off"
        spellCheck={false}
        error={fehler ?? undefined}
      />
      <div>
        <Button type="submit" variant="secondary">
          {t('browser.schutz.lockern')}
        </Button>
      </div>
    </form>
  )
}

export function Lockerung({
  stand,
  rest,
  abbrechen,
  bestaetigen,
}: {
  stand: SchutzStand
  rest: number
  abbrechen: () => Promise<unknown>
  bestaetigen: (text: string) => Promise<unknown>
}) {
  const { t } = useTranslation()
  const antrag = stand.antrag
  if (!antrag) return null
  const liste = lockerungen(t, stand.regeln, antrag.ziel)

  return (
    <section className="msm-card flex flex-col gap-3 border border-status-warning/40 p-5" aria-labelledby="schutz-lockerung">
      <div className="flex items-center gap-2">
        <Clock className="h-5 w-5 shrink-0 text-status-warning" aria-hidden="true" />
        <h2 id="schutz-lockerung" className="font-headline text-title-md text-on-surface">
          {t('browser.schutz.wartetTitel')}
        </h2>
      </div>
      <ul className="list-disc pl-5 text-body-sm text-on-surface">
        {liste.map((satz) => (
          <li key={satz}>{satz}</li>
        ))}
      </ul>
      {antrag.text ? (
        <Abtippen text={antrag.text} rest={rest} bestaetigen={bestaetigen} />
      ) : (
        <p className="text-body-sm text-on-surface">
          {t('browser.schutz.giltIn')} <span className="font-mono tabular-nums">{dauerText(rest)}</span>
        </p>
      )}
      <div>
        <Button type="button" variant="primary" onClick={() => void abbrechen().catch(() => toast.error(t('browser.schutz.fehler')))}>
          {t('browser.schutz.behalten')}
        </Button>
      </div>
    </section>
  )
}
