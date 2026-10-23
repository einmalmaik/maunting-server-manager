/**
 * Die Steuerpakete der Verlaufsübergabe an ein eigenes Gerät.
 *
 * Sie reisen durch dieselbe Geräte-Mailbox wie Gruppenschlüssel und
 * Chatgeheimnisse (`gruppe`/`geraet`) und werden dort an genau ein Gerät
 * versiegelt. Die Typen stehen an einer Stelle, damit der Router in
 * `gruppen/geraeteSteuerung.ts` nicht bei jedem neuen Paket eine zweite,
 * abweichende Liste führen muss.
 *
 * `history_sync` ist der Textverlauf. Ein Paket ohne `transferId` stammt von
 * einem Stand vor der Empfangsquittung und wird weiterhin ohne Manifest
 * angenommen; neue Pakete tragen `transferId`, `teil` und `teile` und werden
 * erst nach dem vollständigen Empfang quittiert.
 */

export const VERLAUF_SYNC_TYP = 'history_sync'
export const VERLAUF_MANIFEST_TYP = 'history_manifest'
export const VERLAUF_PART_ACK_TYP = 'history_part_ack'
export const VERLAUF_DONE_TYP = 'history_done'
export const VERLAUF_MEDIA_TYP = 'history_media'
export const VERLAUF_MEDIA_ACK_TYP = 'history_media_ack'

const VERLAUFS_TYPEN: ReadonlySet<string> = new Set([
  VERLAUF_SYNC_TYP,
  VERLAUF_MANIFEST_TYP,
  VERLAUF_PART_ACK_TYP,
  VERLAUF_DONE_TYP,
  VERLAUF_MEDIA_TYP,
  VERLAUF_MEDIA_ACK_TYP,
])

/** Gehört dieses Steuerpaket zur Verlaufsübergabe? */
export function istVerlaufsTyp(typ: unknown): typ is string {
  return typeof typ === 'string' && VERLAUFS_TYPEN.has(typ)
}
