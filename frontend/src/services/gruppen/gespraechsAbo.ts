/**
 * Meldet jede bekannte Mailbox beim Echtzeitstrom an - Gruppen und Chats.
 *
 * Ohne dieses Abo erfaehrt ein offener Messenger von nichts, was nicht gerade
 * auf dem Schirm ist; der Server stellt seit Stufe 4 nicht mehr je Konto zu.
 */

import { deriveBlindMailboxId, deriveGroupBlindMailboxId } from '../e2eeCrypto'
import { dmZiele } from './dmGeheimnis'
import { gruppenZiele } from './gruppenMailbox'

/**
 * Meldet jede bekannte Mailbox beim Strom an — nicht nur die offene.
 *
 * **Ohne das ist der Messenger nach Stufe 4 stumm.** Bis 09/2026 schlug der
 * Server bei jeder Gruppennachricht die Mitgliederliste nach und stellte an
 * jedes Konto einzeln zu; wer die Anwendung offen hatte, erfuhr davon, egal
 * welches Gespräch gerade auf dem Schirm war. Dieser Nachschlag ist weg — und
 * damit ist das Abo der **einzige** Weg, überhaupt von einer Nachricht zu
 * erfahren. Ein Abo nur für das offene Gespräch hiesse: alles andere kommt
 * erst beim nächsten Öffnen an, und die Meldung auf dem geschlossenen Tab nie.
 *
 * Nebenbei wandert dieselbe Liste an die Push-Zustellung — `abonniereMailbox`
 * meldet beide Empfänger aus einer Quelle.
 *
 * Nacheinander und nicht parallel: jeder Schritt liest die Ablage und rechnet
 * SHA-256, und ein Schwung von hundert gleichzeitig liesse den ersten
 * Bildaufbau stehen. Ein Fehlschlag hält die übrigen nicht auf — eine Gruppe,
 * deren Geheimnis klemmt, darf die anderen nicht mit stumm machen.
 */
export async function abonniereBekannteGespraeche(
  eigeneId: number,
  gruppen: readonly number[],
  gegenstellen: readonly number[],
): Promise<void> {
  for (const groupId of gruppen) {
    try {
      await gruppenZiele(groupId, await deriveGroupBlindMailboxId(groupId))
    } catch {
      /* Diese Gruppe nicht, die nächste schon. */
    }
  }
  if (!eigeneId) return
  for (const peerId of gegenstellen) {
    if (!peerId || peerId === eigeneId) continue
    try {
      // Ohne `erzeuge`: ein Abo ist kein Versand. Ein Chatgeheimnis entsteht
      // beim Schreiben, nicht beim Zuhören — sonst legte das blosse Öffnen
      // des Messengers für jeden Kontakt eines an und verteilte es.
      await dmZiele(eigeneId, peerId, await deriveBlindMailboxId(eigeneId, peerId))
    } catch {
      /* dito */
    }
  }
}
