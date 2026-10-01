/**
 * Ordner der Tresor-Dateien als reine Funktionen: Weg vom Stamm, mögliche
 * Ziele und ob ein Eintrag irgendwohin verschoben werden darf. Ein Ordner
 * darf nie in sich selbst oder in einen seiner Unterordner wandern, sonst
 * hinge der Teilbaum an nichts mehr und verschwände aus der Ansicht.
 */
import type { Fassung } from '@/Singra/UI'
import type { VaultItem } from './vaultStore'

/** Höchstens so viele Ebenen werden verfolgt; schützt vor kaputten Ketten. */
const TIEFE = 50

/** Ordner vom Stamm bis `id` (einschließlich); leer für den Stamm. */
export function pfadVon(id: string | undefined, ordner: VaultItem[]): VaultItem[] {
  const kette: VaultItem[] = []
  let aktuell = id
  while (aktuell && kette.length < TIEFE) {
    const o = ordner.find((x) => x.id === aktuell)
    if (!o) break
    kette.unshift(o)
    aktuell = o.ordner
  }
  return kette
}

/**
 * Ob `item` in den Ordner `ziel` (`undefined` = Stamm) verschoben werden darf:
 * nicht dorthin, wo er schon liegt, und kein Ordner unter sich selbst.
 */
export function darfVerschieben(item: VaultItem, ziel: string | undefined, ordner: VaultItem[]): boolean {
  if ((item.ordner ?? undefined) === ziel) return false
  if (item.category !== 'ordner') return true
  return !pfadVon(ziel, ordner).some((o) => o.id === item.id)
}

/**
 * Ob mehrere Einträge zusammen nach `ziel` dürfen: keiner ist ein Ordner auf
 * dem Weg dorthin, und mindestens einer wechselt wirklich den Ort. Wer schon
 * im Ziel liegt, bleibt einfach liegen.
 */
export function darfAlleVerschieben(items: VaultItem[], ziel: string | undefined, ordner: VaultItem[]): boolean {
  const weg = pfadVon(ziel, ordner)
  return items.some((i) => darfVerschieben(i, ziel, ordner)) && items.every((i) => !weg.some((o) => o.id === i.id))
}

/** Ziele für den Verschieben-Dialog, mit vollem Pfad als Beschriftung; ohne die Teilbäume gewählter Ordner. */
export function zielOrdner(items: VaultItem[], ordner: VaultItem[]): Array<{ value: string; label: string }> {
  const gewaehlt = new Set(items.map((i) => i.id))
  return ordner
    .map((o) => ({ o, kette: pfadVon(o.id, ordner) }))
    .filter(({ kette }) => !kette.some((k) => gewaehlt.has(k.id)))
    .map(({ o, kette }) => ({ value: o.id, label: kette.map((k) => k.service).join(' / ') }))
    .sort((a, b) => a.label.localeCompare(b.label))
}

/**
 * Die gewählten Einträge ohne die, deren Ordner ebenfalls gewählt ist.
 * Sammelaktionen gelten nur für diese: ein Kind, das mit seinem Ordner in den
 * Papierkorb ginge, bekäme ein eigenes `trashedAt` und bliebe beim
 * Wiederherstellen des Ordners im Papierkorb zurück.
 */
export function obersteAuswahl(ids: Iterable<string>, items: VaultItem[]): VaultItem[] {
  const gewaehlt = new Set(ids)
  const ordner = items.filter((i) => i.category === 'ordner')
  return items.filter((i) => gewaehlt.has(i.id) && !pfadVon(i.ordner, ordner).some((o) => gewaehlt.has(o.id)))
}

/** Eine Datei samt ihrem Pfad relativ zur Auswahl, etwa „Verträge/Miete/2024.pdf“. */
export interface DateiMitPfad {
  item: VaultItem
  pfad: string
}

/**
 * Alle Dateien in und unter den Einträgen, mit Pfad für ein Zip. Gleiche
 * Namen im selben Ordner bekommen „ (2)“ vor der Endung, sonst überschriebe
 * beim Entpacken die eine die andere. `items` sind die sichtbaren Einträge.
 */
export function dateienUnter(oberste: VaultItem[], items: VaultItem[]): DateiMitPfad[] {
  const ergebnis: DateiMitPfad[] = []
  const vergeben = new Set<string>()
  const eindeutig = (pfad: string) => {
    let kandidat = pfad
    for (let n = 2; vergeben.has(kandidat.toLowerCase()); n++) {
      const schnitt = pfad.lastIndexOf('.') > pfad.lastIndexOf('/') ? pfad.lastIndexOf('.') : pfad.length
      kandidat = `${pfad.slice(0, schnitt)} (${n})${pfad.slice(schnitt)}`
    }
    vergeben.add(kandidat.toLowerCase())
    return kandidat
  }
  const sammeln = (item: VaultItem, vorsatz: string, tiefe: number) => {
    // Ein Schrägstrich im Namen wäre im Zip ein Ordner, den es nicht gibt.
    const name = item.service.split('/').join('_')
    if (item.category === 'datei' && item.datei) ergebnis.push({ item, pfad: eindeutig(vorsatz + name) })
    if (item.category !== 'ordner' || tiefe >= TIEFE) return
    const kinder = items.filter((k) => k.ordner === item.id && (k.category === 'ordner' || k.category === 'datei'))
    for (const kind of kinder.sort((a, b) => a.service.localeCompare(b.service))) sammeln(kind, `${vorsatz}${name}/`, tiefe + 1)
  }
  for (const item of oberste) sammeln(item, '', 0)
  return ergebnis
}

/**
 * Frühere Fassungen einer Datei für die Versionsliste, neueste zuerst. Als
 * Zeitpunkt steht, wann die Fassung abgelöst wurde, wie beim Server-Dateimanager.
 */
export function fassungenVon(item: VaultItem): Fassung[] {
  return (item.datei?.frueher ?? []).map((fassung) => ({ id: fassung.original.id, zeit: fassung.ersetzt, groesse: fassung.original.echt }))
}
