/**
 * Die Funken dieses Geräts im Speicher — Akten, Freundschaften, Meilensteine.
 *
 * Gerechnet wird in `services/funkenService.ts`; hier liegt nur, was die
 * Oberfläche braucht, um es an drei Stellen (Chatliste, Chatkopf,
 * Freundesliste) gleich zu zeigen. Die Akten kommen versiegelt aus der
 * lokalen Messenger-Ablage und gehen dorthin zurück. Ist der Messenger
 * gesperrt, gibt es hier nichts — das ist richtig so.
 *
 * Ein Funke gibt es nur unter Freunden. `freunde` trägt dafür den Beginn der
 * Freundschaft: wer nicht darin steht, bekommt kein Abzeichen, und wer die
 * Freundschaft neu schließt, fängt bei null an (`falte` verwirft alles davor).
 */

import { create } from 'zustand'

import { claimAchievement } from '@/api/social'
import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import {
  erreichteMeilensteine,
  falte,
  leereAkte,
  leseAkte,
  nimmAuf as nimmInAkteAuf,
  ohneEreignis,
  verfalle,
  vergesseneAkte,
  type FunkenAkte,
  type FunkenEreignis,
  type FunkenKern,
} from '@/services/funkenService'
import { beimLeeren } from '@/services/klartextSpeicher'
import { istOffen } from '@/services/lokaleVersiegelung'
import { ladeFunkenAkten, speichereFunkenAkte } from '@/services/messengerLocalStore'

/** Welche Meilensteine dieses Konto schon gemeldet hat — nur die Zahl, kein Kontakt. */
function meilensteinSchluessel(konto: number): string {
  return `msm:funken-meilensteine:${konto}`
}

function gemeldeteMeilensteine(konto: number): Set<number> {
  try {
    const roh = JSON.parse(localStorage.getItem(meilensteinSchluessel(konto)) || '[]')
    return new Set(Array.isArray(roh) ? roh.filter((n) => Number.isSafeInteger(n)) : [])
  } catch {
    return new Set()
  }
}

function merkeMeilenstein(konto: number, stufe: number): void {
  try {
    const bisher = gemeldeteMeilensteine(konto)
    bisher.add(stufe)
    localStorage.setItem(meilensteinSchluessel(konto), JSON.stringify([...bisher]))
  } catch {
    // Ohne Ablage wird beim nächsten Mal noch einmal gemeldet. Der Server
    // schaltet jede Errungenschaft nur einmal frei.
  }
}

export interface FreundschaftsAngabe {
  userId: number
  /** `created_at` der Freundschaft, wie der Server sie liefert. */
  seit: string | number | null | undefined
}

interface FunkenState {
  /** Wessen Akten hier liegen. Ein anderes Konto, ein leerer Store. */
  konto: number | null
  akten: Record<number, FunkenAkte>
  /** Kontakt → Beginn der Freundschaft (ms). */
  freunde: Record<number, number>
  geladen: boolean
  lade: () => Promise<void>
  /** Nimmt Augenblicke und Wiederherstellungen eines Gesprächs auf. */
  nimmAuf: (partnerId: number, ereignisse: readonly FunkenEreignis[]) => Promise<void>
  /** Für einen Versand, der gescheitert ist. */
  nimmZurueck: (partnerId: number, kennung: string) => Promise<void>
  /** Freundschaft beendet oder blockiert: der Funke ist weg, unwiderruflich. */
  vergiss: (partnerId: number) => Promise<void>
  setzeFreunde: (liste: readonly FreundschaftsAngabe[]) => void
  /** Der Kern mit diesem Kontakt, verfallen bis `jetzt`. `null`: kein Freund. */
  kernVon: (partnerId: number, jetzt: number) => FunkenKern | null
}

/** Schreibt je Kontakt der Reihe nach, damit ein älterer Stand keinen neueren überholt. */
const schreibKette = new Map<number, Promise<void>>()

function schreibe(akte: FunkenAkte): Promise<void> {
  const vorige = schreibKette.get(akte.partnerId) ?? Promise.resolve()
  const naechste = vorige
    .catch(() => {})
    .then(() => speichereFunkenAkte(akte as unknown as { partnerId: number } & Record<string, unknown>))
    .catch(() => {
      // Gesperrt oder kein Speicher. Die Akte lebt im Speicher weiter, und der
      // nächste Augenblick schreibt sie erneut.
    })
  schreibKette.set(akte.partnerId, naechste)
  return naechste
}

function seitAlsZahl(seit: FreundschaftsAngabe['seit']): number {
  if (typeof seit === 'number') return Number.isFinite(seit) ? seit : 0
  if (!seit) return 0
  const hatZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(seit)
  const t = Date.parse(hatZone ? seit : `${seit}Z`)
  return Number.isFinite(t) ? t : 0
}

let ladeVersprechen: Promise<void> | null = null

export const useFunkenStore = create<FunkenState>((set, get) => {
  /** Hält den Store beim angemeldeten Konto; ein Wechsel leert ihn. */
  const pruefeKonto = (): number | null => {
    const konto = angemeldetesKonto()
    if (get().konto !== konto) {
      ladeVersprechen = null
      set({ konto, akten: {}, freunde: {}, geladen: false })
    }
    return konto
  }

  const meldeMeilensteine = (partnerId: number, akte: FunkenAkte) => {
    const konto = get().konto
    const seit = get().freunde[partnerId]
    // Ohne bekannte Freundschaft kein Deckel für fremde Angaben — also auch
    // keine Meldung.
    if (!konto || !seit) return
    const gemeldet = gemeldeteMeilensteine(konto)
    for (const stufe of erreichteMeilensteine(falte(akte, seit).rekord)) {
      if (gemeldet.has(stufe)) continue
      merkeMeilenstein(konto, stufe)
      void claimAchievement(`social_streak_${stufe}`).catch(() => {})
    }
  }

  return {
    konto: null,
    akten: {},
    freunde: {},
    geladen: false,

    lade: async () => {
      const konto = pruefeKonto()
      if (!konto || get().geladen) return
      // Gesperrt: nichts zu lesen. Beim nächsten Aufruf nach dem Entsperren.
      if (!istOffen()) return
      if (!ladeVersprechen) {
        ladeVersprechen = (async () => {
          try {
            const rohe = await ladeFunkenAkten()
            if (get().konto !== konto) return
            const geladen: Record<number, FunkenAkte> = {}
            for (const roh of rohe) {
              const akte = leseAkte(roh)
              if (akte) geladen[akte.partnerId] = akte
            }
            // Was vor dem Laden schon aufgenommen wurde (etwa bei gesperrtem
            // Messenger), gewinnt nicht gegen die Ablage, sondern kommt dazu.
            // Eine Vergessensgrenze zieht immer mit.
            const nachzutragen: FunkenAkte[] = []
            for (const [id, akte] of Object.entries(get().akten)) {
              const alt = geladen[Number(id)]
              const gemischt = alt
                ? nimmInAkteAuf(
                    { ...alt, vergessenBis: Math.max(alt.vergessenBis, akte.vergessenBis) },
                    akte.ereignisse,
                    Date.now(),
                  )
                : akte
              geladen[Number(id)] = gemischt
              nachzutragen.push(gemischt)
            }
            set({ akten: geladen, geladen: true })
            for (const akte of nachzutragen) void schreibe(akte)
          } catch {
            ladeVersprechen = null
          }
        })()
      }
      await ladeVersprechen
    },

    nimmAuf: async (partnerId, ereignisse) => {
      if (!Number.isSafeInteger(partnerId) || partnerId <= 0 || ereignisse.length === 0) return
      await get().lade()
      if (!get().konto) return
      const alt = get().akten[partnerId] ?? leereAkte(partnerId)
      const neu = nimmInAkteAuf(alt, ereignisse, Date.now(), get().freunde[partnerId] ?? 0)
      if (neu === alt) return
      set({ akten: { ...get().akten, [partnerId]: neu } })
      meldeMeilensteine(partnerId, neu)
      // Ungeladen wird nicht geschrieben: sonst ersetzte eine halbe Akte aus
      // dem Speicher die ganze aus der Ablage. `lade` trägt sie nach.
      if (get().geladen) await schreibe(neu)
    },

    nimmZurueck: async (partnerId, kennung) => {
      const alt = get().akten[partnerId]
      if (!alt) return
      const neu = ohneEreignis(alt, kennung)
      if (neu === alt) return
      set({ akten: { ...get().akten, [partnerId]: neu } })
      if (get().geladen) await schreibe(neu)
    },

    vergiss: async (partnerId) => {
      if (!Number.isSafeInteger(partnerId) || partnerId <= 0) return
      await get().lade()
      if (!get().konto) return
      const neu = vergesseneAkte(partnerId, Date.now())
      set({ akten: { ...get().akten, [partnerId]: neu } })
      if (get().geladen) await schreibe(neu)
    },

    setzeFreunde: (liste) => {
      pruefeKonto()
      const freunde: Record<number, number> = {}
      for (const { userId, seit } of liste) {
        const t = seitAlsZahl(seit)
        if (!Number.isSafeInteger(userId) || userId <= 0 || !t) continue
        // Steht eine Freundschaft zweimal im Bestand (beide Richtungen), gilt
        // die jüngere: sie ist die, die gerade besteht.
        freunde[userId] = Math.max(freunde[userId] ?? 0, t)
      }
      const bisher = get().freunde
      const gleich =
        Object.keys(bisher).length === Object.keys(freunde).length &&
        Object.entries(freunde).every(([id, t]) => bisher[Number(id)] === t)
      if (!gleich) set({ freunde })
    },

    kernVon: (partnerId, jetzt) => {
      const seit = get().freunde[partnerId]
      if (!seit) return null
      const akte = get().akten[partnerId]
      return verfalle(akte ? falte(akte, seit) : leereAkte(partnerId).basis, jetzt)
    },
  }
})

// Sperren und Abmelden werfen auch die Funken aus dem Speicher. Wer mit wem
// wie lange schreibt, gehört nicht unter den Sperrschirm.
beimLeeren.add(() => {
  ladeVersprechen = null
  useFunkenStore.setState({ konto: null, akten: {}, freunde: {}, geladen: false })
})
