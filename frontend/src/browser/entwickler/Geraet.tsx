/**
 * Gerät: Bildschirm, Medien, Prozessor, Standort und Zeitzone der Seite
 * nachstellen (`Emulation`). Alles fällt beim Schließen der Werkzeuge weg
 * (`ausschalten`); neu laden zeigt die Seite, wie sie auf dem Gerät startet.
 */
import { RotateCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button, Checkbox, Dropdown, NumberStepper } from '@/Singra/UI'

import { rufen } from './protokoll'
import { OHNE_EMULATION, useAnsicht, useSeitenflaeche, type Emulation } from './werkzeuge'

const UA = {
  iphone: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
  ipad: 'Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
  android: 'Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36',
}

export const GERAETE: Record<string, { breite: number; hoehe: number; faktor: number; mobil: boolean; ua?: string }> = {
  iphoneSe: { breite: 375, hoehe: 667, faktor: 2, mobil: true, ua: UA.iphone },
  iphone15: { breite: 393, hoehe: 852, faktor: 3, mobil: true, ua: UA.iphone },
  pixel7: { breite: 412, hoehe: 915, faktor: 2.625, mobil: true, ua: UA.android },
  galaxyS20: { breite: 360, hoehe: 800, faktor: 3, mobil: true, ua: UA.android },
  ipadAir: { breite: 820, hoehe: 1180, faktor: 2, mobil: true, ua: UA.ipad },
  laptop: { breite: 1280, hoehe: 800, faktor: 1, mobil: false },
}

const ORTE: Record<string, { latitude: number; longitude: number }> = {
  berlin: { latitude: 52.52, longitude: 13.405 },
  london: { latitude: 51.5074, longitude: -0.1278 },
  newYork: { latitude: 40.7128, longitude: -74.006 },
  tokio: { latitude: 35.6762, longitude: 139.6503 },
}

const ZONEN = ['', 'Europe/Berlin', 'Europe/London', 'America/New_York', 'America/Los_Angeles', 'Asia/Tokyo', 'UTC']

const leise = (p: Promise<unknown>) => p.catch(() => null)

function bildschirmVon(e: Emulation) {
  return e.geraet === 'eigen' ? { breite: e.breite, hoehe: e.hoehe, faktor: e.faktor, mobil: e.mobil } : GERAETE[e.geraet]
}

/**
 * Wie weit ein Gerät verkleinert wird, damit es ganz in die Fläche passt.
 * Ohne das schnitt die Webview ab, was über die Fläche ragte, samt Text.
 */
export function einpassen(geraet: { breite: number; hoehe: number }, flaeche: { breite: number; hoehe: number }): number {
  if (flaeche.breite <= 0 || flaeche.hoehe <= 0) return 1
  return Math.min(1, flaeche.breite / geraet.breite, flaeche.hoehe / geraet.hoehe)
}

/** Nur der Bildschirm; läuft auch, wenn sich die Fläche ändert. */
export async function bildschirm(tab: string, e: Emulation): Promise<void> {
  const g = bildschirmVon(e)
  if (!g) return
  const scale = einpassen(g, useSeitenflaeche.getState())
  await leise(rufen(tab, 'Emulation.setDeviceMetricsOverride', { width: g.breite, height: g.hoehe, deviceScaleFactor: g.faktor, mobile: g.mobil, scale }))
}

/** Spielt den Stand vollständig in die Seite; was aus ist, wird zurückgesetzt. */
export async function anwenden(tab: string, e: Emulation): Promise<void> {
  const g = bildschirmVon(e)
  if (g) {
    await bildschirm(tab, e)
    // `maxTouchPoints: 0` lehnt das Protokoll ab; ausgeschaltet geht ohne.
    await leise(rufen(tab, 'Emulation.setTouchEmulationEnabled', g.mobil ? { enabled: true, maxTouchPoints: 5 } : { enabled: false }))
  } else {
    await leise(rufen(tab, 'Emulation.clearDeviceMetricsOverride'))
    await leise(rufen(tab, 'Emulation.setTouchEmulationEnabled', { enabled: false }))
  }
  await leise(rufen(tab, 'Emulation.setUserAgentOverride', { userAgent: GERAETE[e.geraet]?.ua ?? '' }))
  const merkmale = [
    { name: 'prefers-color-scheme', value: e.schema },
    { name: 'prefers-reduced-motion', value: e.bewegung },
  ]
  await leise(rufen(tab, 'Emulation.setEmulatedMedia', { media: e.medien, features: merkmale }))
  await leise(rufen(tab, 'Emulation.setCPUThrottlingRate', { rate: e.cpu }))
  const ort = ORTE[e.ort]
  await leise(ort ? rufen(tab, 'Emulation.setGeolocationOverride', { ...ort, accuracy: 50 }) : rufen(tab, 'Emulation.clearGeolocationOverride'))
  await leise(rufen(tab, 'Emulation.setTimezoneOverride', { timezoneId: e.zeitzone }))
}

function Feld({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[8rem_1fr] items-center gap-2 py-1 text-label-sm">
      <span className="text-on-surface-variant">{label}</span>
      <div className="min-w-0">{children}</div>
    </div>
  )
}

export function Geraet({ tab }: { tab: string }) {
  const { t } = useTranslation()
  const e = useAnsicht((s) => s.emulation[tab]) ?? OHNE_EMULATION
  const setzen = (teil: Partial<Emulation>) => {
    const neu = { ...e, ...teil }
    useAnsicht.getState().setEmulation(tab, neu)
    void anwenden(tab, neu)
  }
  const auswahl = (label: string, wert: string, werte: string[], schluessel: string, aendern: (v: string) => void) => (
    <Dropdown
      aria-label={label}
      value={wert}
      onChange={aendern}
      options={werte.map((v) => ({ value: v, label: t(`browser.entwickler.geraet.${schluessel}.${v || 'aus'}`) }))}
      buttonClassName="h-7 text-label-sm"
    />
  )

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
      <h3 className="pb-1 text-label-sm font-semibold text-on-surface">{t('browser.entwickler.geraet.bildschirm')}</h3>
      <Feld label={t('browser.entwickler.geraet.geraet')}>
        {auswahl(t('browser.entwickler.geraet.geraet'), e.geraet, ['aus', ...Object.keys(GERAETE), 'eigen'], 'geraete', (v) => {
          const g = GERAETE[v]
          setzen(g ? { geraet: v, breite: g.breite, hoehe: g.hoehe, faktor: g.faktor, mobil: g.mobil } : { geraet: v })
        })}
      </Feld>
      {e.geraet === 'eigen' && (
        <>
          <Feld label={t('browser.entwickler.geraet.breite')}>
            <NumberStepper aria-label={t('browser.entwickler.geraet.breite')} value={e.breite} min={200} max={4000} step={10} size="sm" onValueChange={(v) => Number.isFinite(Number(v)) && v !== '' && setzen({ breite: Number(v) })} />
          </Feld>
          <Feld label={t('browser.entwickler.geraet.hoehe')}>
            <NumberStepper aria-label={t('browser.entwickler.geraet.hoehe')} value={e.hoehe} min={200} max={4000} step={10} size="sm" onValueChange={(v) => Number.isFinite(Number(v)) && v !== '' && setzen({ hoehe: Number(v) })} />
          </Feld>
          <Feld label={t('browser.entwickler.geraet.faktor')}>
            <NumberStepper aria-label={t('browser.entwickler.geraet.faktor')} value={e.faktor} min={1} max={4} step={0.25} size="sm" onValueChange={(v) => Number.isFinite(Number(v)) && v !== '' && setzen({ faktor: Number(v) })} />
          </Feld>
          <label className="flex items-center gap-2 py-1 text-label-sm text-on-surface">
            <Checkbox checked={e.mobil} onCheckedChange={(an) => setzen({ mobil: an })} />
            {t('browser.entwickler.geraet.mobil')}
          </label>
        </>
      )}
      <h3 className="pb-1 pt-3 text-label-sm font-semibold text-on-surface">{t('browser.entwickler.geraet.darstellung')}</h3>
      <Feld label={t('browser.entwickler.geraet.medienTitel')}>{auswahl(t('browser.entwickler.geraet.medienTitel'), e.medien, ['', 'screen', 'print'], 'medien', (v) => setzen({ medien: v }))}</Feld>
      <Feld label={t('browser.entwickler.geraet.schemaTitel')}>{auswahl(t('browser.entwickler.geraet.schemaTitel'), e.schema, ['', 'light', 'dark'], 'schema', (v) => setzen({ schema: v }))}</Feld>
      <Feld label={t('browser.entwickler.geraet.bewegungTitel')}>{auswahl(t('browser.entwickler.geraet.bewegungTitel'), e.bewegung, ['', 'reduce'], 'bewegung', (v) => setzen({ bewegung: v }))}</Feld>
      <h3 className="pb-1 pt-3 text-label-sm font-semibold text-on-surface">{t('browser.entwickler.geraet.umgebung')}</h3>
      <Feld label={t('browser.entwickler.geraet.cpuTitel')}>{auswahl(t('browser.entwickler.geraet.cpuTitel'), String(e.cpu), ['1', '4', '6'], 'cpu', (v) => setzen({ cpu: Number(v) }))}</Feld>
      <Feld label={t('browser.entwickler.geraet.ortTitel')}>{auswahl(t('browser.entwickler.geraet.ortTitel'), e.ort, ['', ...Object.keys(ORTE)], 'orte', (v) => setzen({ ort: v }))}</Feld>
      <Feld label={t('browser.entwickler.geraet.zoneTitel')}>
        <Dropdown
          aria-label={t('browser.entwickler.geraet.zoneTitel')}
          value={e.zeitzone}
          onChange={(v) => setzen({ zeitzone: v })}
          options={ZONEN.map((z) => ({ value: z, label: z || t('browser.entwickler.geraet.zoneAus') }))}
          buttonClassName="h-7 text-label-sm"
        />
      </Feld>
      <div className="flex gap-2 pt-3">
        <Button size="sm" variant="secondary" onClick={() => void rufen(tab, 'Page.reload').catch(() => null)}>
          <RotateCw className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
          {t('browser.entwickler.neuLaden')}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setzen(OHNE_EMULATION)}>
          {t('browser.entwickler.geraet.zuruecksetzen')}
        </Button>
      </div>
      <p className="pt-2 text-label-sm text-on-surface-variant">{t('browser.entwickler.geraet.hinweis')}</p>
    </div>
  )
}
