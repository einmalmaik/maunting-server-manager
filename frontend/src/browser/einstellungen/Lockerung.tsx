/**
 * Eine Lockerung des Jugend- und Suchtschutzes: was sich ändern soll, wie
 * lange sie noch wartet und danach das Fenster von einer Stunde, in dem sie
 * sich bestätigen lässt. „Schutz behalten“ nimmt sie jederzeit zurück. Ob es
 * soweit ist, misst Rust an der Uhrzeit aus dem Netz (`schild/schutz.rs`).
 */
import { Clock } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

import { Button } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import type { SchutzRegeln, SchutzStand } from '../services/nativ'
import { dauerText, uebrig } from '../services/schutz'

const FEHLER = ['ohne_netz', 'gebunden', 'abkuehlen', 'zu_frueh', 'verfallen', 'kein_antrag'] as const

/** Ein Fehlercode aus `schutz.rs` als Satz. */
export function fehlerText(t: TFunction, fehler: unknown): string {
  const code = String(fehler)
  return (FEHLER as readonly string[]).includes(code) ? t(`browser.schutz.fehlerCode.${code}`) : t('browser.schutz.fehler')
}

export function wartezeitName(t: TFunction, stunden: number): string {
  return stunden >= 48 ? t('browser.schutz.wartezeitTage', { count: stunden / 24 }) : t('browser.schutz.wartezeitStunden', { count: stunden })
}

/** Was der Antrag gegenüber den geltenden Regeln lockert, als Sätze. */
export function lockerungen(t: TFunction, alt: SchutzRegeln, ziel: SchutzRegeln): string[] {
  if (!ziel.aktiv) return [t('browser.schutz.lockerung.aus')]
  return [
    ...alt.kategorien.filter((k) => !ziel.kategorien.includes(k)).map((k) => t('browser.schutz.lockerung.nichtMehr', { name: t(`browser.schutz.kategorie.${k}`) })),
    ...alt.eigene.filter((h) => !ziel.eigene.includes(h)).map((host) => t('browser.schutz.lockerung.nichtMehr', { name: host })),
    ...ziel.ausnahmen.filter((h) => !alt.ausnahmen.includes(h)).map((host) => t('browser.schutz.lockerung.erlauben', { host })),
    ...(ziel.wartezeit_stunden < alt.wartezeit_stunden ? [t('browser.schutz.lockerung.wartezeit', { name: wartezeitName(t, ziel.wartezeit_stunden) })] : []),
  ]
}

export function Lockerung({
  stand,
  vergangen,
  abbrechen,
  bestaetigen,
}: {
  stand: SchutzStand
  vergangen: number
  abbrechen: () => Promise<unknown>
  bestaetigen: () => Promise<unknown>
}) {
  const { t } = useTranslation()
  const antrag = stand.antrag
  if (!antrag) return null
  const liste = lockerungen(t, stand.regeln, antrag.ziel)
  const rest = uebrig(antrag.rest_sekunden, vergangen)
  const fenster = rest > 0 ? antrag.fenster_sekunden : uebrig(antrag.fenster_sekunden, vergangen - antrag.rest_sekunden)
  const melden = (e: unknown) => toast.error(fehlerText(t, e))

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
      {rest > 0 ? (
        <>
          <p className="text-body-sm text-on-surface">
            {t('browser.schutz.bestaetigenIn')} <span className="font-mono tabular-nums">{dauerText(t, rest)}</span>
          </p>
          <p className="text-label-sm text-on-surface-variant">{t('browser.schutz.fensterHinweis')}</p>
        </>
      ) : fenster > 0 ? (
        <p className="text-body-sm text-on-surface">
          {t('browser.schutz.fensterOffen')} <span className="font-mono tabular-nums">{dauerText(t, fenster)}</span>
        </p>
      ) : (
        <p className="text-body-sm text-on-surface">{t('browser.schutz.fehlerCode.verfallen')}</p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="primary" onClick={() => void abbrechen().catch(melden)}>
          {t('browser.schutz.behalten')}
        </Button>
        {rest === 0 && fenster > 0 && (
          <Button type="button" variant="secondary" onClick={() => void bestaetigen().catch(melden)}>
            {t('browser.schutz.lockern')}
          </Button>
        )}
      </div>
    </section>
  )
}
