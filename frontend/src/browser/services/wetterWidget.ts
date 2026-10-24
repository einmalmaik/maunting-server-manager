/**
 * Homescreen-Wetter-Widget (Android). Die App stellt beim Start und nach jeder
 * Ortsänderung ein eigenes, eng begrenztes Token aus und übergibt es der
 * nativen Seite; dort liegt es im Keystore. Der Worker aktualisiert auch bei
 * geschlossener App ausschließlich über MSM.
 */
import { useEffect, useRef } from 'react'

import { api } from '@/api/client'

import { useEinstellungenStore } from './einstellungenStore'
import { nativ } from './nativ'
import { istAndroid } from './plattform'
import { istGekoppelt, useSitzung } from './sitzung'

async function einrichten(): Promise<boolean> {
  if (!istAndroid()) return false
  const { wetterOrt, wetterEinheit } = useEinstellungenStore.getState()
  if (!wetterOrt) return false
  const antwort = await api<{ token: string }>('/browser/wetter/widget-token', { method: 'POST' })
  await nativ.widgetWetterSetzen({
    ort: wetterOrt.name,
    land: wetterOrt.land,
    lat: wetterOrt.lat,
    lon: wetterOrt.lon,
    einheit: wetterEinheit,
    token: antwort.token,
  })
  return true
}

/** Stellt ein frisches Token aus und bittet den Startbildschirm um das Widget. */
export async function wetterWidgetAnheften(): Promise<boolean> {
  if (!(await einrichten())) return false
  return Boolean(await nativ.widgetWetterAnheften())
}

export function useWetterWidget() {
  const ort = useEinstellungenStore((s) => s.wetterOrt)
  const einheit = useEinstellungenStore((s) => s.wetterEinheit)
  const stand = useSitzung((s) => s.stand)
  const zuletzt = useRef('')
  useEffect(() => {
    if (!istAndroid() || !ort || !istGekoppelt(stand)) return
    const schluessel = `${ort.lat},${ort.lon},${einheit}`
    if (zuletzt.current === schluessel) return
    zuletzt.current = schluessel
    void einrichten().catch(() => {
      zuletzt.current = ''
    })
  }, [ort?.lat, ort?.lon, einheit, stand])
}
