/**
 * Miniaturen für die Galerie als Objekt-URLs.
 *
 * Kacheln fordern ihre Miniatur an, sobald sie in die Nähe des sichtbaren
 * Bereichs kommen. Die Anforderungen eines Augenblicks gehen gebündelt an
 * `miniaturenLesen`, also als eine Anfrage an den Server statt hundert.
 *
 * Eine URL bleibt, solange eine Kachel sie zeigt, und danach noch eine Weile
 * (`HALTEN`), damit Zurückscrollen nicht neu entschlüsselt. Beim Sperren
 * verfallen alle URLs über `ansichtenSchliessen`; diese Ablage merkt das am
 * neuen Schlüssel und fängt leer an.
 */

import { useEffect, useState } from 'react'
import { useVaultStore } from './vaultStore'
import { ansichtOeffnen, ansichtSchliessen, miniaturenLesen } from './tresorDateien'
import type { BlobKopf } from './tresorDatei'

const HALTEN = 400

interface Gehalten {
  url: string
  nutzer: number
  schluessel: CryptoKey
}

interface Anforderung {
  kopf: BlobKopf
  eintragId: string
  userKey: CryptoKey
  fertig: (url: string | null) => void
}

const gehalten = new Map<string, Gehalten>()
/** Nicht mehr angezeigte Miniaturen, älteste zuerst. */
const frei = new Set<string>()
const laufend = new Map<string, Promise<string | null>>()
let wartend: Anforderung[] = []
let geplant = false

function sitzungGilt(userKey: CryptoKey): boolean {
  return useVaultStore.getState().userKey === userKey
}

function aufraeumen() {
  for (const id of frei) {
    if (frei.size <= HALTEN) break
    const g = gehalten.get(id)
    if (g) ansichtSchliessen(g.url)
    gehalten.delete(id)
    frei.delete(id)
  }
}

async function abholen() {
  geplant = false
  const stapel = wartend
  wartend = []
  const userKey = stapel[0]?.userKey
  if (!userKey) return
  const passend = stapel.filter((a) => a.userKey === userKey)
  // Anforderungen einer älteren Sitzung gibt es nur nach dem Sperren: ohne Bild.
  for (const a of stapel) if (a.userKey !== userKey) a.fertig(null)
  let bilder = new Map<string, Uint8Array>()
  try {
    bilder = await miniaturenLesen(passend, userKey)
  } catch {
    // ohne Bilder weiter
  }
  for (const a of passend) {
    const bild = bilder.get(a.kopf.id)
    if (!bild || !sitzungGilt(userKey)) {
      a.fertig(null)
      continue
    }
    const vorhanden = gehalten.get(a.kopf.id)
    if (vorhanden?.schluessel === userKey) {
      a.fertig(vorhanden.url)
      continue
    }
    const url = ansichtOeffnen(new Blob([bild as BlobPart], { type: 'image/webp' }))
    gehalten.set(a.kopf.id, { url, nutzer: 0, schluessel: userKey })
    frei.add(a.kopf.id)
    a.fertig(url)
  }
  // Erst nachdem die Wartenden ihre URL übernommen haben (Mikrotasks), sonst
  // fiele bei großen Stapeln gerade Geliefertes gleich wieder heraus.
  setTimeout(aufraeumen, 0)
}

/**
 * Holt die Miniatur eines Blobs. Liefert `null`, wenn es keine gibt. Wer eine
 * URL bekommt, gibt sie mit `miniaturFreigeben` wieder frei.
 */
export function miniaturHolen(kopf: BlobKopf, eintragId: string, userKey: CryptoKey): Promise<string | null> {
  const da = gehalten.get(kopf.id)
  if (da && da.schluessel !== userKey) {
    // Neue Sitzung: die alten URLs sind beim Sperren schon verfallen.
    gehalten.clear()
    frei.clear()
  }
  let versprechen = laufend.get(kopf.id)
  if (!versprechen) {
    const aktuell = gehalten.get(kopf.id)
    versprechen = aktuell
      ? Promise.resolve(aktuell.url)
      : new Promise<string | null>((fertig) => {
          wartend.push({ kopf, eintragId, userKey, fertig })
          if (!geplant) {
            geplant = true
            setTimeout(() => void abholen(), 16)
          }
        })
    if (!aktuell) {
      laufend.set(kopf.id, versprechen)
      void versprechen.finally(() => laufend.delete(kopf.id))
    }
  }
  return versprechen.then((url) => {
    const g = gehalten.get(kopf.id)
    if (!url || !g || g.url !== url) return null
    g.nutzer++
    frei.delete(kopf.id)
    return url
  })
}

export function miniaturFreigeben(blobId: string): void {
  const g = gehalten.get(blobId)
  if (!g || g.nutzer === 0) return
  g.nutzer--
  if (g.nutzer === 0) {
    frei.add(blobId)
    aufraeumen()
  }
}

/** Die Miniatur-URL eines Blobs, solange `aktiv` gilt; sonst `null`. */
export function useMiniatur(kopf: BlobKopf | undefined, eintragId: string, aktiv: boolean): string | null {
  const userKey = useVaultStore((s) => s.userKey)
  const [url, setUrl] = useState<string | null>(null)
  const blobId = kopf && kopf.echt > 0 ? kopf.id : null
  useEffect(() => {
    if (!aktiv || !blobId || !kopf || !userKey) {
      setUrl(null)
      return
    }
    let offen = true
    let erhalten: string | null = null
    void miniaturHolen(kopf, eintragId, userKey).then((u) => {
      if (!u) return
      if (!offen) {
        miniaturFreigeben(blobId)
        return
      }
      erhalten = u
      setUrl(u)
    })
    return () => {
      offen = false
      if (erhalten) miniaturFreigeben(blobId)
    }
    // kopf ändert sich nur mit blobId
  }, [aktiv, blobId, eintragId, userKey])
  return url
}

/** Füllt den Offline-Cache mit allen Miniaturen, in Paketen und abbrechbar. */
export async function miniaturenVorladen(
  anfragen: { kopf: BlobKopf; eintragId: string }[],
  abgebrochen: () => boolean,
): Promise<void> {
  for (let i = 0; i < anfragen.length; i += 200) {
    if (abgebrochen() || (typeof navigator !== 'undefined' && navigator.onLine === false)) return
    await miniaturenLesen(anfragen.slice(i, i + 200), null)
  }
}
