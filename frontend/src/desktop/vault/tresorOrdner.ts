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

/** Ziele für den Verschieben-Dialog, mit vollem Pfad als Beschriftung. */
export function zielOrdner(item: VaultItem, ordner: VaultItem[]): Array<{ value: string; label: string }> {
  return ordner
    .map((o) => ({ o, kette: pfadVon(o.id, ordner) }))
    .filter(({ kette }) => !kette.some((k) => k.id === item.id))
    .map(({ o, kette }) => ({ value: o.id, label: kette.map((k) => k.service).join(' / ') }))
    .sort((a, b) => a.label.localeCompare(b.label))
}

/**
 * Frühere Fassungen einer Datei für die Versionsliste, neueste zuerst. Als
 * Zeitpunkt steht, wann die Fassung abgelöst wurde, wie beim Server-Dateimanager.
 */
export function fassungenVon(item: VaultItem): Fassung[] {
  return (item.datei?.frueher ?? []).map((fassung) => ({ id: fassung.original.id, zeit: fassung.ersetzt, groesse: fassung.original.echt }))
}
