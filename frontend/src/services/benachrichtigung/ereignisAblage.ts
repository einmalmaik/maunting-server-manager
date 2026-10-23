/**
 * Eine Meldung erscheint auf diesem Gerät **einmal** — auch über die Grenze
 * zwischen Fenster und Service Worker hinweg.
 *
 * `einmalig.ts` teilt Schlüssel zwischen Tabs desselben Ursprungs
 * (`localStorage`). Ein Service Worker hat kein `localStorage`; er und die
 * Seite treffen sich in der Cache API. Beide benutzen dieselbe Ablage und
 * dieselbe Kennung, deshalb reicht es, wenn einer sie beansprucht.
 *
 * Die Kennung kommt vom Server (`ereignis`, z. B. `msg:<mailbox>:<id>`) und
 * reist im Ereignis **und** im Push. Nur so ist „dieselbe Nachricht" auf beiden
 * Wegen erkennbar; eine im Client gebaute Kennung wäre eine zweite Meinung.
 *
 * Die Federung ist weich, wie bei `einmalig.ts`: zwei Zugriffe im selben
 * Moment können beide „neu" sehen. Das Fenster dafür ist ein Lesevorgang, und
 * die Dopplung, die übrig bleibt, ist eine Meldung statt einer bei jeder
 * Nachricht.
 */

const ABLAGE = 'msm-push-ereignisse'
const SCHLUESSEL = '/bereits-gezeigt.json'
const HOECHSTZAHL = 200

export async function ereignisIstNeu(kennung: string | null | undefined): Promise<boolean> {
  const sauber = (kennung || '').trim()
  if (!sauber) return true
  if (typeof caches === 'undefined') return true
  try {
    const ablage = await caches.open(ABLAGE)
    const antwort = await ablage.match(SCHLUESSEL)
    const liste: string[] = antwort ? await antwort.json() : []
    if (liste.includes(sauber)) return false
    liste.push(sauber)
    await ablage.put(
      SCHLUESSEL,
      new Response(JSON.stringify(liste.slice(-HOECHSTZAHL)), {
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    return true
  } catch {
    // Ohne Ablage lieber einmal zu viel als gar nicht.
    return true
  }
}

/** Nur für Tests und das Abmelden. */
export async function vergissEreignisse(): Promise<void> {
  if (typeof caches === 'undefined') return
  try {
    await caches.delete(ABLAGE)
  } catch {
    // Nichts zu löschen.
  }
}
