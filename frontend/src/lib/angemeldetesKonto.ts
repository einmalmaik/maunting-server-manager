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

export function setzeAngemeldetesKonto(id: number | null): void {
  konto = typeof id === 'number' && Number.isFinite(id) ? id : null
}

export function angemeldetesKonto(): number | null {
  return konto
}
