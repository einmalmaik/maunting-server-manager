/**
 * Welcher Besitznachweis zu welcher Mailbox gehört — als schmales Blatt.
 *
 * Der Nachweis entsteht dort, wo das Gruppengeheimnis liegt
 * (`gruppenSchluessel.ts`), gebraucht wird er aber ganz woanders: in jedem
 * einzelnen Aufruf an die Mailbox, und die laufen über `api/social.ts`. Diese
 * Datei ist die Brücke dazwischen, und sie importiert nichts — ein Rückimport
 * aus der API-Schicht in die Krypto wäre ein Zyklus, und derselbe Umweg über
 * `await import(...)` mitten im Sendepfad ist an anderer Stelle schon einmal
 * unter gleichzeitigen Aufrufen zerbrochen.
 *
 * Gehalten wird nur im Arbeitsspeicher. Der Nachweis ist aus dem Geheimnis
 * jederzeit nachrechenbar; ihn zusätzlich auf die Platte zu legen hiesse, ein
 * Geheimnis zweimal zu lagern, damit man es einmal schneller hat.
 */

const nachweise = new Map<string, string>()

/** Hinterlegt den Nachweis für eine Mailbox. Ein leerer Wert nimmt ihn zurück. */
export function merkeMailboxNachweis(mailboxId: string, token: string | null): void {
  const kennung = (mailboxId || '').trim().toLowerCase()
  if (!kennung) return
  if (!token) {
    nachweise.delete(kennung)
    return
  }
  nachweise.set(kennung, token.trim().toLowerCase())
}

/** Der Nachweis für eine Mailbox, oder `null`, wenn dieses Gerät keinen kennt. */
export function mailboxNachweis(mailboxId: string | null | undefined): string | null {
  const kennung = (mailboxId || '').trim().toLowerCase()
  if (!kennung) return null
  return nachweise.get(kennung) ?? null
}

/**
 * Die Kopfzeile für einen Aufruf an diese Mailbox — leer, wenn es keinen gibt.
 *
 * Als Kopfzeile und nie als Abfrageparameter: was in der Adresse steht, steht
 * im Zugriffsprotokoll jedes Servers auf dem Weg.
 */
export function nachweisKopf(mailboxId: string | null | undefined): Record<string, string> {
  const token = mailboxNachweis(mailboxId)
  return token ? { 'X-Mailbox-Token': token } : {}
}

/** Beim Abmelden. Der nächste Mensch an diesem Gerät erbt keine Nachweise. */
export function leereMailboxNachweise(): void {
  nachweise.clear()
}
