/**
 * Wer gerade angemeldet ist — als schmales Blatt, das nichts importiert.
 *
 * `authStore` wäre die naheliegende Quelle, aber er holt sich seinerseits
 * `clearGeraeteMemory` aus `services/e2eeGeraet`. Ein Rückimport von dort wäre
 * ein Zyklus, und die Variante mit `await import(...)` mitten im Sendepfad
 * zerbrach unter drei gleichzeitigen Aufrufen: einer bekam statt der
 * Testfälschung das echte Modul und damit dessen halbe Anwendung hinterher.
 *
 * Also andersherum. Geschrieben wird an genau einer Stelle, in `saveCachedUser`
 * — demselben Trichter, durch den jede Änderung am angemeldeten Benutzer
 * ohnehin läuft. Wer hier einen zweiten Schreibweg baut, bekommt einen
 * Geräteschlüssel unter der falschen Kontokennung.
 */

let konto: number | null = null
const beobachter = new Set<(konto: number | null) => void>()

export function setzeAngemeldetesKonto(id: number | null): void {
  const neu = typeof id === 'number' && Number.isFinite(id) ? id : null
  if (neu === konto) return
  konto = neu
  for (const melden of beobachter) melden(neu)
}

export function angemeldetesKonto(): number | null {
  return konto
}

/** Meldet jeden Wechsel (Anmelden, Abmelden, anderes Konto). Gibt das Abmelden zurück. */
export function beiKontowechsel(melden: (konto: number | null) => void): () => void {
  beobachter.add(melden)
  return () => {
    beobachter.delete(melden)
  }
}
