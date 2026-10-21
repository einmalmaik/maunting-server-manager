/**
 * Fluechtiger Zustand, den mehrere Teilmodule teilen.
 *
 * Steht allein, damit `gruppenAblage` beim Zuruecksetzen der Testablage nicht
 * das Steuermodul importieren muss und die beiden sich nicht im Kreis ziehen.
 */



const gefragt = new Set<string>()

export function kennstFrage(marke: string): boolean {
  return gefragt.has(marke)
}

export function merkeFrage(marke: string): void {
  gefragt.add(marke)
}

export function vergissFrage(marke: string): void {
  gefragt.delete(marke)
}

export function leereGefragt(): void {
  gefragt.clear()
}

export function fragenMitPraefix(praefix: string): string[] {
  return [...gefragt].filter((marke) => marke.startsWith(praefix))
}
