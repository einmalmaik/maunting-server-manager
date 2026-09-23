/**
 * Die Einladungskarte einer Gruppe — verschlüsselt, mit dem Schlüssel im Link.
 *
 * **Das Problem.** Eine Einladung zeigt eine Vorschau: Name, Beschreibung,
 * Logo. Bis 09/2026 kam die aus `chat_groups.name` und wurde vom Server
 * ausgeliefert, ohne Anmeldung — er musste sie also kennen. Sobald der
 * Gruppenname in Stufe 6 in den verschlüsselten Block wandert, kann er das
 * nicht mehr, und die Karte bliebe leer. Genau daran scheitern die meisten
 * Versuche, so etwas nachträglich zu verschlüsseln: an der einen Stelle, die
 * den Klartext noch brauchte.
 *
 * **Die Lösung steht seit 1995 im Browser.** Alles hinter der Raute schickt er
 * **nie** an den Server — kein Request-Ziel, kein Referer, kein Zugriffslog.
 * Der Link heisst deshalb `…/chat/join/<code>#k=<schlüssel>`, und die Karte
 * entschlüsselt sich im Browser des Eingeladenen.
 *
 * **Woher der Schlüssel kommt.** Aus dem Gruppengeheimnis, mit einem dritten
 * Vorsatz neben Mailbox und Nachweis. Das spart eine eigene Verteilung — jedes
 * Mitglied hat das Geheimnis ohnehin und kann denselben Link bauen.
 *
 * Und es gibt nichts preis: der Eingeladene bekommt `sha256(vorsatz +
 * geheimnis)` und kommt davon nicht zurück zum Geheimnis. Er kann also weder
 * die Mailbox-Kennung noch den Besitznachweis bilden — er sieht die Karte und
 * sonst nichts, bis er beigetreten ist und das Geheimnis regulär zugestellt
 * bekommt.
 *
 * **Gebunden an den Einladungscode.** Der Code steht im AAD. Wer die Karte
 * einer Gruppe unter dem Code einer anderen unterschiebt, bekommt einen
 * Tag-Bruch statt einer fremden Vorschau. Und wird der Code gewechselt — das
 * ist, was „Einladung zurückziehen" heisst —, passt die alte Karte nicht mehr:
 * ein weitergereichter Altlink zeigt dann nichts, und das ist der Zweck.
 */

import { decryptString, encryptString, importAesGcmRawKey } from '@msdis/shield/aead'
import { sha256Hex } from '@msdis/shield/integrity'

/** Der dritte Wert aus dem Gruppengeheimnis, neben Mailbox und Nachweis. */
const EINLADUNG_VORSATZ = 'msm:gruppe:einladung:'

export const EINLADUNG_PREFIX = 'sv-einladung-v1:'

/** Was auf der Karte steht. Alles optional — eine Gruppe braucht nichts davon. */
export interface EinladungsInhalt {
  name?: string | null
  beschreibung?: string | null
  /**
   * Das Logo als Data-URL, also mitverschlüsselt.
   *
   * Nicht als Adresse: eine Adresse müsste der Server ausliefern, und dann
   * wäre das Logo wieder öffentlich — mitsamt der Auskunft, dass jemand die
   * Einladung gerade ansieht. Der Preis ist Grösse; deshalb schrumpft
   * `logoAlsDatenUrl` das Bild, bevor es hier landet.
   */
  logo?: string | null
}

/** Der Einladungsschlüssel aus dem Gruppengeheimnis. 64 Hexzeichen. */
export async function einladungsschluesselAus(geheimnis: string): Promise<string> {
  return sha256Hex(new TextEncoder().encode(EINLADUNG_VORSATZ + geheimnis))
}

/** Bindet eine Karte an genau einen Einladungscode. */
function kartenAad(inviteCode: string): string {
  return `msm:einladung:${inviteCode}`
}

/**
 * Der Schlüssel als AES-Schlüssel.
 *
 * Er ist 64 Hexzeichen, also 32 Bytes — dieselbe Länge, die der
 * Gruppenschlüssel hat. Umgerechnet wird hier und nicht beim Aufrufer, damit
 * es nur eine Stelle gibt, die weiss, wie aus dem Wert im Link ein Schlüssel
 * wird.
 */
async function alsSchluessel(hex: string) {
  const roh = new Uint8Array(32)
  for (let i = 0; i < 32; i += 1) roh[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16)
  try {
    return await importAesGcmRawKey(roh, ['encrypt', 'decrypt'])
  } finally {
    roh.fill(0)
  }
}

/** Ist das ein Einladungsschlüssel, wie er im Link steht? */
export function istEinladungsschluessel(wert: string): boolean {
  return /^[0-9a-f]{64}$/.test(wert)
}

/**
 * Versiegelt eine Karte für einen Einladungscode.
 *
 * Wirft, wenn der Schlüssel nicht stimmt — anders als beim Lesen ist ein
 * Fehler hier kein Alltagsfall, sondern ein Programmierfehler.
 */
export async function baueEinladungsKarte(
  geheimnis: string,
  inviteCode: string,
  inhalt: EinladungsInhalt,
): Promise<string> {
  const schluessel = await alsSchluessel(await einladungsschluesselAus(geheimnis))
  const klartext = JSON.stringify({
    v: 1,
    name: inhalt.name ?? null,
    beschreibung: inhalt.beschreibung ?? null,
    logo: inhalt.logo ?? null,
  })
  return EINLADUNG_PREFIX + (await encryptString(klartext, schluessel, kartenAad(inviteCode)))
}

/**
 * Öffnet eine Karte. `null` heisst schlicht „geht nicht" — falscher Schlüssel,
 * falscher Code, kein Karteninhalt, Altbestand.
 *
 * Kein Werfen und keine Unterscheidung nach Grund: der Aufrufer zeigt in
 * jedem dieser Fälle dasselbe, nämlich eine Karte ohne Vorschau. Eine
 * Fehlermeldung, die „falscher Schlüssel" von „diese Gruppe gibt es nicht"
 * unterscheidet, wäre ein Auskunftsdienst über fremde Einladungscodes.
 */
export async function lieseEinladungsKarte(
  umschlag: string | null | undefined,
  schluesselHex: string | null | undefined,
  inviteCode: string,
): Promise<EinladungsInhalt | null> {
  if (!umschlag || !schluesselHex) return null
  if (!umschlag.startsWith(EINLADUNG_PREFIX)) return null
  if (!istEinladungsschluessel(schluesselHex)) return null

  try {
    const schluessel = await alsSchluessel(schluesselHex)
    const klartext = await decryptString(
      umschlag.slice(EINLADUNG_PREFIX.length),
      schluessel,
      kartenAad(inviteCode),
    )
    const roh = JSON.parse(klartext)
    if (!roh || typeof roh !== 'object') return null
    return {
      name: typeof roh.name === 'string' ? roh.name : null,
      beschreibung: typeof roh.beschreibung === 'string' ? roh.beschreibung : null,
      logo: typeof roh.logo === 'string' && istUnbedenklicheDatenUrl(roh.logo) ? roh.logo : null,
    }
  } catch {
    return null
  }
}

/**
 * Nur Bild-Data-URLs, und nur die vier Formate, die der Upload ohnehin nimmt.
 *
 * Die Karte kommt von einem anderen Menschen. Ohne diese Prüfung liesse sich
 * `logo` auf `data:text/html,…` setzen, und ein `<img src>` wäre zwar harmlos,
 * ein späterer Aufrufer mit `<a href>` oder `window.open` aber nicht. Die
 * Schranke gehört hierher, ans Lesen, und nicht an jede Anzeige.
 */
function istUnbedenklicheDatenUrl(wert: string): boolean {
  return /^data:image\/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+$/.test(wert)
}

/**
 * Hängt den Schlüssel an einen Einladungslink.
 *
 * Hinter der Raute, und das ist der ganze Trick: der Browser schickt nichts
 * davon an den Server.
 */
export function mitSchluessel(url: string, schluesselHex: string): string {
  return `${url}#k=${schluesselHex}`
}

/**
 * Liest den Schlüssel aus einem Link — egal ob aus einem Nachrichtentext oder
 * aus `location.hash`.
 */
export function schluesselAusLink(link: string): string | null {
  const treffer = /#k=([0-9a-f]{64})\b/.exec(link)
  return treffer ? treffer[1] : null
}

/**
 * Holt das heute öffentlich ausgelieferte Gruppenlogo und macht eine
 * verschlüsselbare Data-URL daraus.
 *
 * Der Übergang: bis Stufe 6 liegt das Logo als Datei hinter
 * `/api/social/groups/avatar/<name>` und wird ohne Anmeldung ausgeliefert.
 * Für die **Einladung** hört das hier auf — was der Eingeladene sieht, kommt
 * aus der verschlüsselten Karte, nicht von dieser Adresse. Wer die Karte baut,
 * ist Mitglied und kommt an die Datei heran; der Eingeladene nicht mehr.
 *
 * `null` bei allem, was schiefgeht. Ein Logo ist Zierde, eine Einladung ohne
 * Logo immer noch eine Einladung.
 */
export async function gruppenLogoAlsDatenUrl(
  avatarUrl: string | null | undefined,
): Promise<string | null> {
  if (!avatarUrl) return null
  try {
    const { apiUrl } = await import('@/config/api')
    const antwort = await fetch(apiUrl(avatarUrl), { credentials: 'include' })
    if (!antwort.ok) return null
    return await logoAlsDatenUrl(await antwort.blob())
  } catch {
    return null
  }
}

/**
 * Verkleinert ein Bild und gibt es als Data-URL zurück.
 *
 * Die Karte reist als eine Zeile in der Datenbank und einmal je Abruf über die
 * Leitung, verschlüsselt und base64-kodiert. Ein 5-MB-Logo wären daraus rund
 * 6,7 MB — je Einladungsvorschau. 128 Pixel reichen für ein Rund von 44
 * Pixeln, und WebP drückt den Rest.
 */
export async function logoAlsDatenUrl(quelle: Blob, kante = 128): Promise<string | null> {
  if (typeof document === 'undefined' || typeof createImageBitmap !== 'function') return null
  try {
    const bild = await createImageBitmap(quelle)
    const seite = Math.min(kante, Math.max(bild.width, bild.height))
    const leinwand = document.createElement('canvas')
    leinwand.width = seite
    leinwand.height = seite
    const stift = leinwand.getContext('2d')
    if (!stift) return null
    // Mittig zuschneiden statt stauchen: ein verzerrtes Logo sieht nach Fehler
    // aus, ein beschnittenes nach Absicht.
    const kurz = Math.min(bild.width, bild.height)
    stift.drawImage(
      bild,
      (bild.width - kurz) / 2,
      (bild.height - kurz) / 2,
      kurz,
      kurz,
      0,
      0,
      seite,
      seite,
    )
    bild.close()
    const url = leinwand.toDataURL('image/webp', 0.8)
    return istUnbedenklicheDatenUrl(url) ? url : null
  } catch {
    return null
  }
}
