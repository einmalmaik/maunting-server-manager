/**
 * Die Kopplung des Browsers mit dem MSM-Panel.
 *
 * Ohne Kopplung ist der Browser ein Browser: Tabs, Suche, Schild, Lesezeichen.
 * Gekoppelt kommen Singra, Messenger, Notizen, Kalender und der Tresor dazu,
 * mit denselben Seiten und derselben Sitzungstechnik wie MSS
 * (`desktop/transport.ts`, `desktop/auth.ts`).
 *
 * `stand` sagt, was gerade gilt:
 * - `aus`: nie gekoppelt oder abgemeldet; die Module zeigen die Kopplung.
 * - `pruefen`: beim Start, bis die stille Anmeldung eine Antwort hat.
 * - `an`: angemeldet.
 * - `offline`: gekoppelt, aber das Panel antwortet nicht. Tresor, Notizen und
 *   Kalender arbeiten mit ihrem lokalen Spiegel weiter; alle 20 s wird erneut
 *   gefragt.
 * - `abgelehnt`: das Panel hat die Sitzung verweigert (Gerät entzogen). Nicht
 *   sofort vergessen: auch zwei gleichzeitige Rotationen sehen so aus. Der
 *   Nutzer koppelt neu oder meldet sich ab.
 */
import { useEffect } from 'react'
import { create } from 'zustand'

import { api, isNetworkOrOfflineError } from '@/api/client'
import { ABGEMELDET } from '@/desktop/auth'
import { stillAnmeldenDetail } from '@/desktop/transport'
import { useAuthStore } from '@/stores/authStore'
import { usePublicSettingsStore } from '@/stores/publicSettingsStore'

import { konfig } from './nativ'

export type SitzungsStand = 'aus' | 'pruefen' | 'an' | 'offline' | 'abgelehnt'

interface SitzungZustand {
  stand: SitzungsStand
  /** Die Panel-Adresse, sobald eine eingetragen ist. */
  adresse: string | null
  setzen: (stand: SitzungsStand) => void
  adresseSetzen: (adresse: string | null) => void
}

export const useSitzung = create<SitzungZustand>()((set) => ({
  stand: 'pruefen',
  adresse: null,
  setzen: (stand) => set({ stand }),
  adresseSetzen: (adresse) => set({ adresse }),
}))

/** Gekoppelt, auch wenn das Panel gerade nicht antwortet. */
export function istGekoppelt(stand: SitzungsStand): boolean {
  return stand === 'an' || stand === 'offline'
}

const HERZSCHLAG_MS = 45_000
const NACHFRAGEN_MS = 20_000

async function anmeldenMitFrist(): Promise<SitzungsStand> {
  const ergebnis = await stillAnmeldenDetail(5000)
  if (ergebnis.status === 'offline') return 'offline'
  if (ergebnis.status === 'abgelehnt') return ergebnis.grund === 'kein_token' ? 'aus' : 'abgelehnt'
  try {
    await Promise.race([
      useAuthStore.getState().checkAuth(),
      new Promise((_, ablehnen) => setTimeout(() => ablehnen(new Error('timeout')), 5000)),
    ])
    void usePublicSettingsStore.getState().refresh()
    return 'an'
  } catch {
    return 'offline'
  }
}

/** Einmal in der Wurzel: Start, Herzschlag, Wiederanlauf, Abmelden. */
export function useSitzungsLauf(): void {
  const stand = useSitzung((s) => s.stand)

  useEffect(() => {
    let aktiv = true
    void (async () => {
      const k = await konfig.laden().catch(() => null)
      if (!aktiv) return
      useSitzung.getState().adresseSetzen(k?.backend_url ?? null)
      if (!k?.backend_url || !k.eingerichtet) {
        useSitzung.getState().setzen('aus')
        return
      }
      const neu = await anmeldenMitFrist()
      if (aktiv) useSitzung.getState().setzen(neu)
    })()
    return () => {
      aktiv = false
    }
  }, [])

  useEffect(() => {
    const abgemeldet = () => useSitzung.getState().setzen('aus')
    window.addEventListener(ABGEMELDET, abgemeldet)
    return () => window.removeEventListener(ABGEMELDET, abgemeldet)
  }, [])

  // Lebenszeichen des Geräts; scheitert es am Netz, gilt der Browser als offline.
  useEffect(() => {
    if (stand !== 'an') return
    // Scheitert ein Herzschlag erst nach dem Abmelden, gilt er nicht mehr:
    // er schrieb sonst „offline“, und der Browser galt wieder als gekoppelt (bis 09.10.2026).
    let aktiv = true
    const takt = setInterval(() => {
      api('/auth/devices/heartbeat', { method: 'POST' }).catch((fehler) => {
        if (aktiv && isNetworkOrOfflineError(fehler)) useSitzung.getState().setzen('offline')
      })
    }, HERZSCHLAG_MS)
    return () => {
      aktiv = false
      clearInterval(takt)
    }
  }, [stand])

  // Aus dem Offline-Zustand selbst herausfinden: `online` allein meldet nur
  // einen Netzadapter, nicht das Panel (siehe DesktopApp).
  useEffect(() => {
    if (stand !== 'offline') return
    let aktiv = true
    const nachfragen = async () => {
      const neu = await anmeldenMitFrist()
      if (aktiv && neu !== 'offline') useSitzung.getState().setzen(neu)
    }
    const takt = setInterval(() => void nachfragen(), NACHFRAGEN_MS)
    window.addEventListener('online', nachfragen)
    return () => {
      aktiv = false
      clearInterval(takt)
      window.removeEventListener('online', nachfragen)
    }
  }, [stand])
}
