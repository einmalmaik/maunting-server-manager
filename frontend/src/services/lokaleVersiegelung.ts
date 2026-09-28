/**
 * Das Siegel über den lokalen Messenger-Ablagen.
 *
 * Der Messenger ist Ende-zu-Ende verschlüsselt: der Server sieht nur Umschläge.
 * Auf dem Gerät galt das bis 09/2026 nicht. Vier IndexedDB-Ablagen lagen offen
 * da — der entschlüsselte Verlauf, der private Geräteschlüssel, die
 * Ratchet-Sitzungen und die Gruppenschlüssel. Wer an das Browser- oder
 * WebView-Profil kam, las mit und konnte sich als das Gerät ausgeben. Die
 * Verschlüsselung auf dem Weg war dicht, die Ablage am Ziel nicht.
 *
 * Diese Datei ist die eine Stelle, an der ein Datensatz zu- und wieder aufgeht.
 * Sie kennt keine der vier Ablagen; sie kennt einen Datensatz, die Felder, die
 * als Schlüssel im Klartext bleiben müssen, und den Schlüssel im Arbeitsspeicher.
 *
 * Zwei Dinge sind hier Absicht:
 *
 * **Ohne Siegel ändert sich nichts.** Wer keinen PIN eingerichtet hat, schreibt
 * und liest wie vorher. Das Siegel ist ein Angebot, keine Pflicht, und ein
 * ausgeschaltetes darf nicht als halb eingeschaltetes durchscheinen.
 *
 * **Zu heißt zu.** Ist das Siegel an und kein Schlüssel im Speicher, wirft jeder
 * Schreibweg und jeder Lesweg liefert nichts. Ein Schreibweg, der in diesem
 * Zustand stillschweigend Klartext ablegt, wäre schlimmer als gar kein Siegel:
 * er sähe von außen aus wie Schutz.
 */

import { decryptString, encryptString } from '@msdis/shield/aead'

import { angemeldetesKonto } from '@/lib/angemeldetesKonto'

/**
 * Merker im localStorage, ob für dieses Konto auf diesem Gerät ein PIN
 * eingerichtet ist.
 *
 * Er steht bewusst nicht in der IndexedDB: die Ablagen fragen bei **jedem**
 * Zugriff danach, und zwar bevor sie eine Transaktion öffnen. Eine synchrone
 * Antwort ist hier keine Bequemlichkeit, sondern Voraussetzung — eine
 * IndexedDB-Transaktion schließt sich, sobald der Ereignisumlauf leerläuft, und
 * ein `await` mitten drin bricht sie ab.
 *
 * Die Kontokennung gehört in den Namen, nicht in den Wert. Zwei Konten auf
 * einem Rechner haben verschiedene PINs, und wer sich abmeldet und jemand
 * anderen anmeldet, darf dessen Messenger nicht für versiegelt halten.
 */
function siegelAktivKey(): string | null {
  const konto = angemeldetesKonto()
  return konto === null ? null : `mss:messenger_siegel_aktiv:konto:${konto}`
}

/** Kennzeichnet eine versiegelte Zeile. Unversiegelte Zeilen haben kein `v`. */
export const SIEGEL_VERSION = 1

/** Die Form, in der eine versiegelte Zeile in der IndexedDB liegt. */
export interface VersiegelteZeile {
  v: typeof SIEGEL_VERSION
  blob: string
  [klarfeld: string]: unknown
}

/**
 * Der Inhaltsschlüssel, solange entsperrt ist.
 *
 * Nur hier, nur im Arbeitsspeicher, nicht extrahierbar. `messengerSperre`
 * setzt ihn beim Entsperren und nimmt ihn beim Sperren wieder weg.
 */
let inhaltsSchluessel: CryptoKey | null = null

/** Wird gerufen, wenn ein Lesezugriff auf eine Zeile trifft, die zu ist. */
let beiVerschlossenemZugriff: (() => void) | null = null

export function setzeInhaltsSchluessel(schluessel: CryptoKey | null): void {
  inhaltsSchluessel = schluessel
}

/**
 * Meldet der Sperre, dass jemand an eine verschlossene Zeile geraten ist.
 *
 * Die Ablagen liefern in dem Fall nichts zurück, und das sieht für den
 * Aufrufer aus wie „nichts da". Ohne diesen Rückkanal bliebe ein falsch
 * gesetzter Sperrzustand unsichtbar, bis jemandem ein leerer Verlauf auffällt.
 */
export function setzeVerschlussMelder(melder: (() => void) | null): void {
  beiVerschlossenemZugriff = melder
}

/** Ist für das angemeldete Konto auf diesem Gerät ein PIN eingerichtet? */
export function siegelAktiv(): boolean {
  try {
    if (typeof localStorage === 'undefined') return false
    const key = siegelAktivKey()
    return key !== null && localStorage.getItem(key) === 'true'
  } catch {
    return false
  }
}

export function setzeSiegelAktiv(aktiv: boolean): void {
  try {
    if (typeof localStorage === 'undefined') return
    const key = siegelAktivKey()
    if (key === null) return
    if (aktiv) localStorage.setItem(key, 'true')
    else localStorage.removeItem(key)
  } catch {
    /* Ein privates Fenster ohne Ablage darf den Messenger nicht lahmlegen. */
  }
}

/**
 * Darf gerade gelesen und geschrieben werden?
 *
 * Kein Siegel: immer. Siegel an: nur mit Schlüssel im Speicher.
 */
export function istOffen(): boolean {
  return !siegelAktiv() || inhaltsSchluessel !== null
}

/** Der Fehler, den ein Schreibversuch im verschlossenen Zustand auslöst. */
export class MessengerVerschlossenError extends Error {
  constructor() {
    super('Der Messenger ist gesperrt. Ohne PIN wird nichts geschrieben.')
    this.name = 'MessengerVerschlossenError'
  }
}

/**
 * Versiegelt eine Zeile für die Ablage.
 *
 * `klarFelder` sind die Felder, die IndexedDB als Schlüssel oder Index braucht
 * und die deshalb lesbar bleiben müssen. Alles andere wandert in `blob`.
 *
 * `aad` bindet den Geheimtext an seinen Platz. Zwei Zeilen zu tauschen ergibt
 * damit keine gültige Zeile mehr, sondern einen Entschlüsselungsfehler.
 */
export async function versiegleZeile<T extends Record<string, any>>(
  datensatz: T,
  klarFelder: readonly string[],
  aad: string,
): Promise<Record<string, unknown>> {
  if (!siegelAktiv()) return { ...datensatz }
  if (!inhaltsSchluessel) throw new MessengerVerschlossenError()

  const klar: Record<string, unknown> = {}
  const geheim: Record<string, unknown> = {}
  for (const [feld, wert] of Object.entries(datensatz)) {
    if (klarFelder.includes(feld)) klar[feld] = wert
    else geheim[feld] = wert
  }

  const blob = await encryptString(JSON.stringify(geheim), inhaltsSchluessel, aad)
  return { ...klar, v: SIEGEL_VERSION, blob }
}

/**
 * Öffnet eine Zeile aus der Ablage.
 *
 * Versteht beide Formen: die versiegelte und die alte im Klartext. Genau das
 * macht die Umstellung wiederanlauffähig — bricht sie in der Mitte ab, liegen
 * beide Formen nebeneinander und beide sind lesbar.
 *
 * Gibt `null` zurück, wenn die Zeile nicht zu öffnen ist. Das ist kein
 * verschluckter Fehler, sondern die einzige ehrliche Antwort: ein falscher
 * Schlüssel, eine manipulierte Zeile und eine fehlende Zeile sind von hier aus
 * nicht zu unterscheiden, und ein Aufrufer, der das Gegenteil glaubt, baut auf
 * Sand.
 */
export async function entsiegleZeile<T extends Record<string, any>>(
  zeile: Record<string, any> | null | undefined,
  aad: string,
): Promise<T | null> {
  if (!zeile) return null

  // Alte Form: liegt im Klartext da, wie vor der Umstellung.
  if (zeile.v !== SIEGEL_VERSION || typeof zeile.blob !== 'string') {
    return zeile as T
  }

  if (!inhaltsSchluessel) {
    beiVerschlossenemZugriff?.()
    return null
  }

  try {
    const { v: _v, blob, ...klar } = zeile
    const geheim = JSON.parse(await decryptString(blob, inhaltsSchluessel, aad))
    return { ...klar, ...geheim } as T
  } catch {
    return null
  }
}

/** Öffnet mehrere Zeilen und lässt die aus, die nicht aufgehen. */
export async function entsiegleZeilen<T extends Record<string, any>>(
  zeilen: readonly Record<string, any>[],
  aad: (zeile: Record<string, any>) => string,
): Promise<T[]> {
  const offen = await Promise.all(zeilen.map((z) => entsiegleZeile(z, aad(z))))
  return offen.filter((z): z is Record<string, any> => z !== null) as T[]
}

/**
 * Wirft, wenn gerade nicht geschrieben werden darf.
 *
 * Für Schreibwege, die ihre Zeilen selbst zusammenbauen und deshalb nicht über
 * `versiegleZeile` laufen.
 */
export function fordereOffen(): void {
  if (!istOffen()) throw new MessengerVerschlossenError()
}
