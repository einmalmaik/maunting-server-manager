/**
 * Die Absenderbeglaubigung — ein zweites Schlüsselpaar je Gerät, das nichts
 * verschlüsselt.
 *
 * **Warum es das braucht.** Eine Gruppe teilt sich einen symmetrischen
 * Schlüssel (siehe `gruppenSchluessel.ts`). Damit kann jedes Mitglied jede
 * Nachricht der Gruppe *erzeugen* — der Schlüssel sagt nur „von jemandem aus
 * dieser Gruppe", nie „von Anna". Wer den Absender trotzdem aus der Nutzlast
 * las (`sender_id`), glaubte dem Absender. Ein Mitglied konnte sich damit als
 * jedes andere ausgeben, dauerhaft und im Verlauf aller.
 *
 * Symmetrisch ist das nicht zu schliessen, und das ist keine Umsetzungsfrage:
 * wer einen MAC prüfen kann, kann ihn auch rechnen. Es braucht ein Geheimnis,
 * das nur der Absender hat — also eine Signatur.
 *
 * **Was hier nicht steht.** Das Primitiv selbst: ECDSA P-256 mit SHA-256 kommt
 * aus `@msdis/shield/signing`, samt der Längenprüfung gegen Kurven- und
 * Algorithmusverwechslung. Diese Datei tut nur zwei Dinge, die DIS bewusst
 * offenlässt: sie hält das Schlüsselmaterial im JWK-Format, das der Messenger
 * überall sonst auch benutzt, und sie kodiert die Signatur als Base64 für den
 * Umschlagkopf.
 *
 * **Ein Schlüssel, eine Aufgabe.** Der Geräteschlüssel aus `e2eeGeraet.ts` ist
 * RSA-OAEP und darf nie signieren; dieser hier ist ECDSA und darf nie
 * verschlüsseln. Beide Prüfungen im Backend sind getrennte Funktionen, die
 * einander nicht durchlassen — eine gemeinsame wäre genau die
 * Algorithmusverwechslung, gegen die sie stehen.
 */

import { base64ToBytes, bytesToBase64, subtle, utf8ToBytes } from '@msdis/shield/core'
import { signEcdsaP256, verifyEcdsaP256 } from '@msdis/shield/signing'

/** Dasselbe Format wie `LocalE2eeKeyPair`: zwei JWK-Strings. */
export interface SignaturPaar {
  publicKeyJwk: string
  privateKeyJwk: string
}

const ALGORITHMUS = { name: 'ECDSA', namedCurve: 'P-256' } as const

/**
 * Ein frisches Paar.
 *
 * Extrahierbar, anders als bei `generateEcdsaP256KeyPair` aus DIS: der private
 * Teil muss als JWK in die versiegelte Gerätezeile, sonst überlebt er keinen
 * Neustart. Geschützt ist er dort durch dasselbe Siegel wie der private
 * Geräteschlüssel — ein nicht extrahierbarer Schlüssel, den niemand ablegen
 * kann, wäre bei jedem Start ein anderer und damit keine Identität.
 */
export async function erzeugeSignaturPaar(): Promise<SignaturPaar> {
  const paar = (await subtle().generateKey(ALGORITHMUS, true, ['sign', 'verify'])) as CryptoKeyPair
  const [pub, priv] = await Promise.all([
    subtle().exportKey('jwk', paar.publicKey),
    subtle().exportKey('jwk', paar.privateKey),
  ])
  return { publicKeyJwk: JSON.stringify(pub), privateKeyJwk: JSON.stringify(priv) }
}

/** Signiert einen Klartext und liefert die 64 Bytes `r‖s` als Base64. */
export async function signiere(daten: string, privateKeyJwk: string): Promise<string> {
  const key = await subtle().importKey('jwk', JSON.parse(privateKeyJwk), ALGORITHMUS, false, [
    'sign',
  ])
  return bytesToBase64(await signEcdsaP256(key, utf8ToBytes(daten)))
}

/**
 * Prüft eine Signatur. Liefert `false` statt zu werfen — auch bei kaputtem
 * Schlüssel, falscher Länge oder unlesbarem Base64.
 *
 * Das ist Absicht: der Aufrufer trifft an einer Stelle eine Entscheidung
 * („gilt / gilt nicht"), und ein Wurf aus dem Prüfweg heraus würde dort als
 * technischer Fehler behandelt und die Nachricht womöglich *durchlassen*. Ein
 * ungültiger Schlüssel ist keine ungeprüfte Nachricht, sondern eine
 * abgewiesene.
 */
export async function pruefe(
  daten: string,
  signaturBase64: string,
  publicKeyJwk: string,
): Promise<boolean> {
  try {
    const key = await subtle().importKey('jwk', JSON.parse(publicKeyJwk), ALGORITHMUS, false, [
      'verify',
    ])
    return await verifyEcdsaP256(key, base64ToBytes(signaturBase64), utf8ToBytes(daten))
  } catch {
    return false
  }
}
