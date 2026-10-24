/**
 * Symmetrisches Ver- und Entschluesseln von Gruppennachrichten.
 *
 * Der Umschlag nennt die Schluesselkennung im Klartext; alles andere ist
 * AES-GCM mit den Gruppen-AAD-Daten.
 */

import { decryptString, encryptString } from '@msdis/shield/aead'

import { ablage } from './gruppenAblage'
import { schluesselZumSenden } from './gruppenVerteilung'
import {
  GRUPPE_PREFIX,
  gebundeneDaten,
  lieseGruppenKopf,
  mitSchluessel,
  type GruppenKontext,
  type GruppenLesung,
} from './typen'

export async function verschluesseleFuerGruppe(
  kontext: GruppenKontext,
  klartext: string,
): Promise<string> {
  const eintrag = await schluesselZumSenden(kontext)
  const chiffre = await mitSchluessel(eintrag, (key) =>
    encryptString(klartext, key, gebundeneDaten(kontext.groupId, eintrag.keyId))
  )
  return `${GRUPPE_PREFIX}${eintrag.keyId}.${chiffre}`
}

export async function entschluesseleGruppenUmschlag(
  groupId: number,
  umschlag: string,
): Promise<GruppenLesung> {
  const kopf = lieseGruppenKopf(umschlag)
  if (!kopf) return { art: 'unbekannt' }

  const eintrag = await ablage.lies(groupId, kopf.keyId)
  if (!eintrag) return { art: 'kein-schluessel', keyId: kopf.keyId }

  try {
    const text = await mitSchluessel(eintrag, (key) =>
      decryptString(kopf.rumpf, key, gebundeneDaten(groupId, kopf.keyId))
    )
    return { art: 'klartext', text }
  } catch (fehler) {
    const grund = fehler instanceof Error ? fehler.name : 'unbekannt'
    return { art: 'bruch', keyId: kopf.keyId, grund }
  }
}
