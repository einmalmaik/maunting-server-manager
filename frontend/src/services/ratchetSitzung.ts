/**
 * Der Double Ratchet im Messenger: Sitzung aufbauen, senden, lesen.
 *
 * Die Umsetzung liegt in `services/ratchet/`; diese Datei hält die gewohnten
 * Exporte. Die kryptografischen Primitive, AAD-Zeichenketten und
 * Signaturformate sind unverändert aus `ratchetSitzung.ts` übernommen.
 */

export {
  DR_INIT_TYP,
  DR_PREFIX,
  DrGeraetNichtEingetragenError,
  DrZustellungFehlgeschlagenError,
  drUrheber,
  logischeUuid,
} from './ratchet/format'
export type {
  AufbauErgebnis,
  DrKontext,
  DrLeseOptionen,
  DrLesung,
  DrZustellung,
} from './ratchet/format'
export { verarbeiteBootstrap } from './ratchet/aufbau'
export { baueZustellungen } from './ratchet/versand'
export { liesDrUmschlag, verwirfDrSitzung } from './ratchet/lesen'
export type { KlartextAblage } from './ratchet/lesen'
