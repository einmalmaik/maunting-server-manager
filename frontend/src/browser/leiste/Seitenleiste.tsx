/**
 * Die schmale Leiste links oder rechts der Seite: die Einträge in der
 * Reihenfolge des Nutzers (`eintraege.ts`), unten die Einstellungen. Ein Klick
 * öffnet das Panel, ein zweiter schließt es; die Einstellungen öffnen ihre
 * Seite im Tab.
 *
 * Ohne Kopplung stehen die MSM-Module trotzdem da und zeigen, wie man koppelt.
 * Gekoppelt fällt weg, was das Panel abgeschaltet hat oder wofür das Konto
 * kein Recht hat, wie in MSS.
 */
import { useTranslation } from 'react-i18next'

import { istGekoppelt, useSitzung } from '../services/sitzung'
import { useLeistenEintraege, useLeistenZahlen, useLeistenZiele } from './eintraege'
import { useLeistenname, useLeistennameAufraeumen, type Richtung } from './leistenname'
import { EINSTELLUNGEN_EINTRAG, KOPPELN_EINTRAG, type Leisteneintrag } from './module'

/**
 * Nur das Symbol; den Namen sagt `aria-label`, für Maus und Tastatur eine
 * Blase daneben, die Windows über die Seite zeichnet (`leistenname.ts`).
 * Der offene Eintrag trägt einen Strich am äußeren Rand.
 */
function Eintrag({
  eintrag,
  offen,
  zahl,
  richtung,
  beiKlick,
}: {
  eintrag: Pick<Leisteneintrag, 'symbol' | 'name'>
  offen: boolean
  zahl?: number
  richtung: Richtung
  beiKlick: () => void
}) {
  const { t } = useTranslation()
  const Symbol = eintrag.symbol
  const name = t(eintrag.name)
  const namen = useLeistenname(name, richtung)
  const strich = richtung === 'rechts' ? 'before:-left-2 before:rounded-r-full' : 'before:-right-2 before:rounded-l-full'
  return (
    <button
      type="button"
      onClick={beiKlick}
      aria-label={zahl ? t('browser.leiste.mitZahl', { name, zahl }) : name}
      aria-pressed={offen}
      {...namen}
      className={`relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl transition-colors before:absolute before:top-2.5 before:bottom-2.5 before:w-[3px] before:transition-opacity ${strich} ${
        offen
          ? 'bg-primary/15 text-primary before:bg-primary before:opacity-100'
          : 'text-on-surface-variant before:opacity-0 hover:bg-surface-container-high hover:text-on-surface'
      }`}
    >
      <Symbol className="h-5 w-5" aria-hidden="true" />
      {!!zahl && (
        <span aria-hidden="true" className="absolute -right-1 -top-1 min-w-[1.125rem] rounded-full bg-primary px-1 text-center text-label-sm leading-[1.125rem] text-on-primary ring-2 ring-surface-container">
          {zahl > 99 ? '99+' : zahl}
        </span>
      )}
    </button>
  )
}

/** `seite`: wo die Leiste steht; die Namen erscheinen auf der Seite zur Seite hin. */
export function Seitenleiste({ seite }: { seite: 'links' | 'rechts' }) {
  const { t } = useTranslation()
  const stand = useSitzung((s) => s.stand)
  const eintraege = useLeistenEintraege()
  const zahlen = useLeistenZahlen()
  const ziele = useLeistenZiele()
  const richtung: Richtung = seite === 'links' ? 'rechts' : 'links'
  useLeistennameAufraeumen()

  return (
    <nav aria-label={t('browser.leiste.name')} className="flex w-14 shrink-0 flex-col items-center gap-1 overflow-y-auto bg-surface-container py-2 msm-ohne-rollbalken">
      {eintraege.map((e) => (
        <Eintrag key={e.id} eintrag={e} offen={ziele.offen === e.id} zahl={zahlen[e.id]} richtung={richtung} beiKlick={() => ziele.panel(e.id)} />
      ))}
      <div className="flex-1" />
      {!istGekoppelt(stand) && stand !== 'pruefen' && (
        <Eintrag eintrag={KOPPELN_EINTRAG} offen={ziele.offen === 'koppeln'} richtung={richtung} beiKlick={() => ziele.panel('koppeln')} />
      )}
      <Eintrag eintrag={EINSTELLUNGEN_EINTRAG} offen={ziele.einstellungenOffen} richtung={richtung} beiKlick={ziele.einstellungen} />
    </nav>
  )
}
