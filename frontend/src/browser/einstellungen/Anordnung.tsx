/**
 * Reihenfolge und Sichtbarkeit der Einträge (Module, Lesezeichen, Verlauf,
 * Downloads) für Leiste, Menü und Symbole oben.
 */
import { useTranslation } from 'react-i18next'

import { ALLE_EINTRAEGE, geordnet, leistenziel } from '../leiste/eintraege'
import { useEinstellungenStore, type Leistenziel } from '../services/einstellungenStore'
import { Ordnungsliste, verschoben } from './Ordnungsliste'

export function Anordnung() {
  const { t } = useTranslation()
  const anordnung = useEinstellungenStore((s) => s.anordnung)
  const ausgeblendet = useEinstellungenStore((s) => s.ausgeblendet)
  const setzen = useEinstellungenStore((s) => s.setzen)
  const umschalten = useEinstellungenStore((s) => s.umschalten)
  const liste = geordnet(ALLE_EINTRAEGE, anordnung)

  return (
    <Ordnungsliste
      art="leiste"
      eintraege={liste.map((e) => ({ schluessel: leistenziel(e), name: t(e.name), symbol: e.symbol, an: !ausgeblendet.includes(leistenziel(e)) }))}
      verschieben={(von, nach) => setzen({ anordnung: verschoben(liste.map(leistenziel), von, nach) })}
      umschalten={(s) => umschalten(s as Leistenziel)}
    />
  )
}
