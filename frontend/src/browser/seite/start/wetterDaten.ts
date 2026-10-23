/**
 * Wetter für Startseite und Widget. Die Werte kommen ausschließlich über MSM
 * (`/browser/wetter`); MSM liest sie aus dem eigenen Sidecar. Der Browser
 * spricht nie direkt mit einem Wetteranbieter.
 *
 * Der letzte Stand liegt lokal im Spiegel, damit die Startseite ohne Netz
 * nicht leer wird. Nach 30 Minuten wird höchstens einmal neu gefragt.
 */
import { useEffect, useState } from 'react'

import { api } from '@/api/client'

import { useEinstellungenStore, type WetterOrt } from '../../services/einstellungenStore'
import { useSitzung } from '../../services/sitzung'

const SPIEGEL = 'msb:wetter'
const AUFFRISCHEN_MS = 30 * 60_000

export interface WetterJetzt {
  temp: number
  lage: string
  wind: number
  feuchte: number
  regen: number
  wolken: number
  zeit: string
}

export interface WetterStunde {
  stunde: number
  zeit: string
  temp: number
  lage: string
}

export interface WetterTag {
  datum: string
  min: number
  max: number
  lage: string
}

export interface WetterWarnung {
  titel: string
  text: string
  stufe: string
  gruppe?: string
  beginn?: string
  ende?: string
}

export interface WetterStand {
  jetzt: WetterJetzt
  stunden: WetterStunde[]
  tage: WetterTag[]
  warnungen?: WetterWarnung[]
  warnquelle?: string
  quelle: string
  stand: string
}

export type WetterLage = { stand: WetterStand | null; fehler: 'nichtEingerichtet' | 'fehler' | 'ungekoppelt' | null }

const geholt = new Map<string, number>()

function schluessel(ort: WetterOrt): string {
  return `${ort.lat.toFixed(2)},${ort.lon.toFixed(2)}`
}

function istStand(roh: unknown): roh is WetterStand {
  if (!roh || typeof roh !== 'object') return false
  const w = roh as Partial<WetterStand>
  return (
    !!w.jetzt &&
    typeof w.jetzt.temp === 'number' &&
    typeof w.jetzt.lage === 'string' &&
    Array.isArray(w.stunden) &&
    Array.isArray(w.tage)
  )
}

function spiegelLesen(ort: WetterOrt): WetterStand | null {
  try {
    const roh = JSON.parse(window.localStorage.getItem(SPIEGEL) || 'null') as { ort?: string; stand?: unknown } | null
    if (roh && roh.ort === schluessel(ort) && istStand(roh.stand)) return roh.stand
  } catch {
    /* kein oder kaputter Spiegel */
  }
  return null
}

function spiegelSchreiben(ort: WetterOrt, stand: WetterStand): void {
  try {
    window.localStorage.setItem(SPIEGEL, JSON.stringify({ ort: schluessel(ort), stand }))
  } catch {
    /* Speicher voll oder gesperrt */
  }
}

export function useWetter(): WetterLage {
  const ort = useEinstellungenStore((s) => s.wetterOrt)
  const sitzung = useSitzung((s) => s.stand)
  const [lage, setLage] = useState<WetterLage>(() => ({ stand: ort ? spiegelLesen(ort) : null, fehler: null }))

  useEffect(() => {
    if (!ort) {
      setLage({ stand: null, fehler: null })
      return
    }
    const gespiegelt = spiegelLesen(ort)
    if (sitzung !== 'an') {
      setLage({ stand: gespiegelt, fehler: 'ungekoppelt' })
      return
    }
    const key = schluessel(ort)
    if (Date.now() - (geholt.get(key) ?? 0) < AUFFRISCHEN_MS) {
      setLage({ stand: gespiegelt, fehler: null })
      return
    }
    geholt.set(key, Date.now())
    let weg = false
    api<unknown>('/browser/wetter', {
      method: 'POST',
      body: JSON.stringify({ lat: ort.lat, lon: ort.lon }),
    }).then(
      (roh) => {
        if (!istStand(roh)) return
        spiegelSchreiben(ort, roh)
        if (!weg) setLage({ stand: roh, fehler: null })
      },
      (fehler: unknown) => {
        geholt.delete(key)
        const code = (fehler as { code?: string | null })?.code
        if (!weg) {
          setLage({
            stand: gespiegelt,
            fehler:
              code === 'BROWSER_WETTER_NICHT_EINGERICHTET' || code === 'BROWSER_WETTER_NICHT_ERREICHBAR'
                ? 'nichtEingerichtet'
                : 'fehler',
          })
        }
      },
    )
    return () => {
      weg = true
    }
  }, [ort?.lat, ort?.lon, sitzung])

  return lage
}
