/**
 * Zugangsdaten im Posteingang: der Browser speichert sie bei gesperrtem
 * Tresor (`browser/services/tresorGesperrt.ts`), ohne einen geheimen
 * Schlüssel zu kennen. Eine App mit offenem Tresor macht daraus einen
 * Eintrag. Ein gleicher Eintrag (Adresse, Benutzer, Passwort) entsteht nicht
 * zweimal; ein geändertes Passwort wird ein neuer Eintrag, der alte bleibt.
 */

import { hostVon } from './hostVon'
import { eingangLoeschen } from './tresorBlobApi'
import { useVaultStore } from './vaultStore'

export interface EingangZugang {
  art: 'zugang'
  /** Adresse der Seite, aus dem Tab, nicht aus der Seite. */
  url: string
  benutzer: string
  passwort: string
  /** Wann im Browser gespeichert wurde (ms). */
  zeit: number
}

const URL_MAX = 2048
const BENUTZER_MAX = 512
const PASSWORT_MAX = 1024

export function istZugang(wert: unknown): wert is EingangZugang {
  if (!wert || typeof wert !== 'object') return false
  const z = wert as Record<string, unknown>
  return (
    z.art === 'zugang' &&
    typeof z.url === 'string' &&
    z.url.length <= URL_MAX &&
    /^https?:\/\//.test(z.url) &&
    hostVon(z.url) !== '' &&
    typeof z.benutzer === 'string' &&
    z.benutzer.length <= BENUTZER_MAX &&
    typeof z.passwort === 'string' &&
    z.passwort.length > 0 &&
    z.passwort.length <= PASSWORT_MAX &&
    typeof z.zeit === 'number' &&
    Number.isFinite(z.zeit)
  )
}

/** Macht aus dem Datensatz einen Eintrag. Weg ist der Datensatz erst, wenn der Eintrag beim Server liegt (`tresorEingang.ts`). */
export async function zugangUebernehmen(bucket: string, eingangId: string, z: EingangZugang): Promise<void> {
  const store = useVaultStore.getState()
  const host = hostVon(z.url)
  const gleich = store.items.some(
    (i) => !i.trashedAt && (i.category ?? 'login') === 'login' && hostVon(i.url) === host && i.username === z.benutzer && i.password === z.passwort,
  )
  if (gleich) {
    await eingangLoeschen(bucket, eingangId)
    return
  }
  await store.saveItem({ id: eingangId, service: host, category: 'login', url: z.url, username: z.benutzer, password: z.passwort })
}
