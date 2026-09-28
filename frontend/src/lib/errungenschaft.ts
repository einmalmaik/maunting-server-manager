/**
 * Meldet eine Errungenschaft, die nur dieses Gerät feststellen kann —
 * Hotkeys gesetzt, Sprachnachricht verschickt, Globus angeflogen.
 *
 * Der Server nimmt nur die Kennungen aus `SELBST_GEMELDET` an und erfährt nur
 * „dieses Konto hat das einmal getan", nie mit wem oder was genau. Gemeldet
 * wird je Konto und Gerät höchstens einmal: gemerkt wird erst, was der
 * Server angenommen hat, damit eine gescheiterte Meldung beim nächsten Mal
 * wiederholt wird.
 */

import { angemeldetesKonto } from '@/lib/angemeldetesKonto'

// Die API kommt erst beim Melden: gemeldet wird auch aus `vaultStore` und
// `messengerSperre`, und `authStore` lädt den Tresor schon beim Start. Ein
// fester Import schlösse den Kreis über `api/client` zurück zu `authStore`.

function schluessel(konto: number): string {
  return `msm:errungenschaften:${konto}`
}

function gemeldet(konto: number): Set<string> {
  try {
    const roh = JSON.parse(localStorage.getItem(schluessel(konto)) || '[]')
    return new Set(Array.isArray(roh) ? roh.filter((k) => typeof k === 'string') : [])
  } catch {
    return new Set()
  }
}

function merke(konto: number, kennung: string): void {
  try {
    const bisher = gemeldet(konto)
    bisher.add(kennung)
    localStorage.setItem(schluessel(konto), JSON.stringify([...bisher]))
  } catch {
    // Ohne Ablage wird beim nächsten Mal noch einmal gemeldet. Der Server
    // schaltet jede Errungenschaft nur einmal frei.
  }
}

// Unterwegs oder in dieser Sitzung abgelehnt (Social aus, 400): nicht bei
// jedem Tastendruck neu fragen.
const offen = new Set<string>()

export function meldeErrungenschaft(kennung: string): void {
  const konto = angemeldetesKonto()
  if (!konto) return
  const marke = `${konto}:${kennung}`
  if (offen.has(marke) || gemeldet(konto).has(kennung)) return
  offen.add(marke)
  void import('@/api/social')
    .then(({ claimAchievement }) => claimAchievement(kennung))
    .then(() => {
      merke(konto, kennung)
      offen.delete(marke)
    })
    .catch(() => {})
}
