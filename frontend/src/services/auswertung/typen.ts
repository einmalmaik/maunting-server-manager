/** Gemeinsame Typen der Umschlag-Handler. */

/**
 * Der Urheber, gegen eine Behauptung aus der Nutzlast geprüft.
 *
 * `null` heißt verwerfen. Die Prüfung selbst bleibt in der Hauptschleife; die
 * Handler bekommen nur das Ergebnis.
 */
export type UrheberVon = (
  behauptetRoh: unknown,
) => Promise<number | undefined | null>
