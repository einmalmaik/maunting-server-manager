/**
 * Wie der Browser aussieht: wo die Einträge stehen, auf welcher Seite das
 * Panel aufgeht, was in welcher Reihenfolge zu sehen ist, welche Knöpfe da
 * sind, die Widgets samt Nachrichten und der Hintergrund der Startseite.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '@/api/client'
import { Button, FileButton, Input, Kurzinfo, MultiSelect, Slider } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import { bildZuWebp, FARB_HINTERGRUENDE, HINTERGRUND_OPTIONEN } from '../seite/hintergruende'
import { hintergrundStil } from '../seite/Startseite'


import { WIDGET_SYMBOLE, widgetsGeordnet } from '../seite/start/Widgets'
import {
  EIGENES_BILD_MAX_BYTES,
  KNOEPFE,
  NACHRICHTEN_THEMEN,
  istWetterOrt,
  useEinstellungenStore,
  type Hintergrund,
  type Leistenort,
  type NachrichtenThema,
  type TabAusrichtung,
  type WetterOrt,
  type Widget,
} from '../services/einstellungenStore'
import { Anordnung } from './Anordnung'
import { istAndroid } from '../services/plattform'
import { wetterWidgetAnheften } from '../services/wetterWidget'
import { Ordnungsliste, verschoben } from './Ordnungsliste'
import { Abschnitt, Aktionszeile, Auswahlzeile, Schalterzeile } from './bausteine'

const ORTE: Leistenort[] = ['menue', 'links', 'rechts', 'oben']

function Aufbau() {
  const { t } = useTranslation()
  const leiste = useEinstellungenStore((s) => s.leiste)
  const panelSeite = useEinstellungenStore((s) => s.panelSeite)
  const ausgeblendet = useEinstellungenStore((s) => s.ausgeblendet)
  const setzen = useEinstellungenStore((s) => s.setzen)
  const umschalten = useEinstellungenStore((s) => s.umschalten)
  return (
    <>
      {/* Am Handy steht alles im Menü der Leiste unten, und das Panel füllt den Bildschirm. */}
      {!istAndroid() && (
        <Abschnitt titel={t('browser.einstellungen.aufbau')}>
          <Auswahlzeile<Leistenort>
            name={t('browser.einstellungen.leisteOrt')}
            wert={leiste}
            optionen={ORTE.map((o) => ({ value: o, label: t(`browser.einstellungen.ort.${o}`) }))}
            aendern={(v) => setzen({ leiste: v })}
          />
          <Auswahlzeile<'links' | 'rechts'>
            name={t('browser.einstellungen.panelSeite')}
            wert={panelSeite}
            optionen={[
              { value: 'rechts', label: t('browser.einstellungen.seite.rechts') },
              { value: 'links', label: t('browser.einstellungen.seite.links') },
            ]}
            aendern={(v) => setzen({ panelSeite: v })}
          />
        </Abschnitt>
      )}
      <Abschnitt titel={t('browser.einstellungen.eintraege')}>
        <Anordnung />
      </Abschnitt>
      <Abschnitt titel={t('browser.einstellungen.knoepfe')}>
        {KNOEPFE.filter((k) => k !== 'erweiterungen' || !istAndroid()).map((k) => (
          <Schalterzeile key={k} name={t(`browser.einstellungen.knopf.${k}`)} an={!ausgeblendet.includes(k)} aendern={() => umschalten(k)} />
        ))}
      </Abschnitt>
    </>
  )
}

function Startwidgets() {
  const { t } = useTranslation()
  const ordnung = widgetsGeordnet(useEinstellungenStore((s) => s.widgetOrdnung))
  const aus = useEinstellungenStore((s) => s.widgetsAus)
  const statistikZeigen = useEinstellungenStore((s) => s.statistikZeigen)
  const setzen = useEinstellungenStore((s) => s.setzen)
  return (
    <Abschnitt titel={t('browser.einstellungen.startseite')}>
      <p className="-mt-2 text-label-sm text-on-surface-variant">{t('browser.einstellungen.startseiteHinweis')}</p>
      <Ordnungsliste
        art="widgets"
        eintraege={ordnung.map((w) => ({ schluessel: w, name: t(`browser.start.widget.${w}`), symbol: WIDGET_SYMBOLE[w], an: !aus.includes(w) }))}
        verschieben={(von, nach) => setzen({ widgetOrdnung: verschoben(ordnung, von, nach) })}
        umschalten={(w) => setzen({ widgetsAus: aus.includes(w as Widget) ? aus.filter((a) => a !== w) : [...aus, w as Widget] })}
      />
      <Wetterwahl />
      {!aus.includes('nachrichten') && <Nachrichtenwahl />}
      <Schalterzeile
        name={t('browser.einstellungen.statistik')}
        hinweis={t('browser.einstellungen.statistikHinweis')}
        an={statistikZeigen}
        aendern={(an) => setzen({ statistikZeigen: an })}
      />
    </Abschnitt>
  )
}

/** Ort und Einheit fürs Wetter; die Suche geht über den eigenen Server. */
function Wetterwahl() {
  const { t } = useTranslation()
  const ort = useEinstellungenStore((s) => s.wetterOrt)
  const einheit = useEinstellungenStore((s) => s.wetterEinheit)
  const setzen = useEinstellungenStore((s) => s.setzen)
  const [suche, setSuche] = useState('')
  const [treffer, setTreffer] = useState<WetterOrt[]>([])
  const [laeuft, setLaeuft] = useState(false)
  const [fehler, setFehler] = useState(false)
  const [versuch, setVersuch] = useState(0)
  useEffect(() => {
    const q = suche.trim()
    if (q.length < 2) {
      setTreffer([])
      setLaeuft(false)
      setFehler(false)
      return
    }
    let weg = false
    setLaeuft(true)
    setFehler(false)
    const timer = window.setTimeout(() => {
      api<{ orte: WetterOrt[] }>(`/browser/wetter/orte?q=${encodeURIComponent(q)}`).then(
        (antwort) => {
          if (weg) return
          setTreffer(Array.isArray(antwort.orte) ? antwort.orte.filter(istWetterOrt) : [])
          setLaeuft(false)
        },
        () => {
          if (weg) return
          setTreffer([])
          setLaeuft(false)
          setFehler(true)
        },
      )
    }, 250)
    return () => {
      weg = true
      window.clearTimeout(timer)
    }
  }, [suche, versuch])
  const waehlen = (eintrag: WetterOrt) => {
    setzen({ wetterOrt: eintrag })
    setSuche('')
    setTreffer([])
    setFehler(false)
    setLaeuft(false)
  }
  return (
    <>
      <Aktionszeile name={t('browser.einstellungen.wetterOrt')} hinweis={t('browser.einstellungen.wetterHinweis')}>
        <Input
          aria-label={t('browser.einstellungen.wetterOrt')}
          placeholder={ort ? `${ort.name} (${ort.land})` : t('browser.einstellungen.wetterOrt')}
          value={suche}
          maxLength={80}
          onChange={(ereignis) => setSuche(ereignis.target.value)}
          onKeyDown={(ereignis) => {
            if (ereignis.key === 'Enter' && treffer.length > 0) {
              ereignis.preventDefault()
              waehlen(treffer[0])
            }
          }}
          className="w-56 max-w-full"
        />
      </Aktionszeile>
      {laeuft && (
        <p role="status" className="text-label-sm text-on-surface-variant">
          {t('browser.einstellungen.wetterSucheLaeuft')}
        </p>
      )}
      {fehler && (
        <p role="alert" className="flex items-center gap-2 text-label-sm text-status-error">
          {t('browser.einstellungen.wetterSucheFehler')}
          <button type="button" className="underline" onClick={() => setVersuch((n) => n + 1)}>
            {t('browser.einstellungen.wetterErneut')}
          </button>
        </p>
      )}
      {!laeuft && !fehler && suche.trim().length >= 2 && treffer.length === 0 && (
        <p className="text-label-sm text-on-surface-variant">{t('browser.einstellungen.wetterKeineTreffer')}</p>
      )}
      {treffer.length > 0 && (
        <ul className="flex w-full max-w-sm flex-col rounded-lg border border-outline-variant bg-surface-container-high p-1">
          {treffer.map((eintrag) => (
            <li key={`${eintrag.lat},${eintrag.lon}`}>
              <button
                type="button"
                className="flex w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-left text-body-sm hover:bg-surface-variant"
                onClick={() => waehlen(eintrag)}
              >
                <span>
                  {eintrag.name}
                  {eintrag.land ? `, ${eintrag.land}` : ''}
                </span>
                <span className="font-mono-sm text-on-surface-variant">{eintrag.lat.toFixed(1)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <Auswahlzeile<'celsius' | 'fahrenheit'>
        name={t('browser.einstellungen.wetterEinheit')}
        wert={einheit}
        optionen={[
          { value: 'celsius', label: t('browser.einstellungen.wetterEinheitC') },
          { value: 'fahrenheit', label: t('browser.einstellungen.wetterEinheitF') },
        ]}
        aendern={(wert) => setzen({ wetterEinheit: wert })}
      />
      {ort && (
        <p className="text-label-sm text-on-surface-variant">
          {ort.name}
          {ort.land ? `, ${ort.land}` : ''}
        </p>
      )}
      {istAndroid() && ort && (
        <Aktionszeile name={t('browser.einstellungen.wetterWidget')}>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() => {
              void wetterWidgetAnheften().then((ok) => {
                if (!ok) toast.error(t('browser.einstellungen.wetterWidgetFehler'))
              })
            }}
          >
            {t('browser.einstellungen.wetterWidgetAnheften')}
          </Button>
        </Aktionszeile>
      )}
    </>
  )
}

/** Themen und Schlagworte bleiben auf dem Gerät; der Server liefert allen dieselben Nachrichten. */
function Nachrichtenwahl() {
  const { t } = useTranslation()
  const themen = useEinstellungenStore((s) => s.nachrichtenThemen)
  const worte = useEinstellungenStore((s) => s.nachrichtenWorte)
  const setzen = useEinstellungenStore((s) => s.setzen)
  return (
    <>
      <Aktionszeile name={t('browser.einstellungen.nachrichten.themen')}>
        <MultiSelect
          aria-label={t('browser.einstellungen.nachrichten.themen')}
          placeholder={t('browser.einstellungen.nachrichten.keinThema')}
          options={NACHRICHTEN_THEMEN.map((thema) => ({ value: thema, label: t(`browser.einstellungen.nachrichten.thema.${thema}`) }))}
          values={themen}
          onChange={(v) => setzen({ nachrichtenThemen: v as NachrichtenThema[] })}
          className="w-56 max-w-full"
        />
      </Aktionszeile>
      <Aktionszeile name={t('browser.einstellungen.nachrichten.worte')} hinweis={t('browser.einstellungen.nachrichten.worteHinweis')}>
        <Input
          aria-label={t('browser.einstellungen.nachrichten.worte')}
          placeholder={t('browser.einstellungen.nachrichten.wortePlatzhalter')}
          value={worte}
          maxLength={200}
          onChange={(e) => setzen({ nachrichtenWorte: e.target.value })}
          className="w-56 max-w-full"
        />
      </Aktionszeile>
    </>
  )
}

function Hintergrundwahl() {
  const { t } = useTranslation()
  const hintergrund = useEinstellungenStore((s) => s.hintergrund)
  const hintergrundBlur = useEinstellungenStore((s) => s.hintergrundBlur)
  const eigenesBild = useEinstellungenStore((s) => s.eigenesBild)
  const setzen = useEinstellungenStore((s) => s.setzen)
  const auswahl: Hintergrund[] = [...HINTERGRUND_OPTIONEN, ...(eigenesBild ? (['eigen'] as const) : [])]

  const bildLaden = async (datei: File) => {
    if (!datei.type.startsWith('image/')) {
      toast.error(t('browser.einstellungen.bildFehler'))
      return
    }
    if (datei.size > EIGENES_BILD_MAX_BYTES) {
      toast.error(t('browser.einstellungen.bildZuGross'))
      return
    }
    try {
      const webp = await bildZuWebp(datei, 0.85)
      setzen({ eigenesBild: webp, hintergrund: 'eigen', ...(hintergrundBlur === 0 ? { hintergrundBlur: 30 } : {}) })
    } catch {
      toast.error(t('browser.einstellungen.bildFehler'))
    }
  }

  const hintergrundWaehlen = (h: Hintergrund) => {
    const istFarbe = h in FARB_HINTERGRUENDE
    if (istFarbe) {
      setzen({ hintergrund: h, hintergrundBlur: 0 })
    } else {
      setzen({ hintergrund: h, ...(hintergrundBlur === 0 ? { hintergrundBlur: 30 } : {}) })
    }
  }

  return (
    <Abschnitt titel={t('browser.einstellungen.hintergrund')}>
      <div role="radiogroup" aria-label={t('browser.einstellungen.hintergrund')} className="grid grid-cols-3 gap-3 sm:grid-cols-5">
        {auswahl.map((h) => (
          <Kurzinfo key={h} text={t(`browser.hintergrund.${h}`)}>
            <button
              type="button"
              role="radio"
              aria-checked={hintergrund === h}
              aria-label={t(`browser.hintergrund.${h}`)}
              onClick={() => hintergrundWaehlen(h)}
              style={hintergrundStil(h, eigenesBild)}
              className={`h-16 w-full rounded-lg border bg-surface transition-all ${
                hintergrund === h ? 'border-primary ring-2 ring-primary/40' : 'border-outline-variant hover:border-primary/50'
              }`}
            />
          </Kurzinfo>
        ))}
      </div>
      <p className="text-label-sm text-on-surface-variant -mt-1">
        {t('browser.einstellungen.kiBilderHinweis')}
      </p>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pt-1">
        <FileButton accept="image/*" onFile={bildLaden} size="sm" className="self-start">
          {t('browser.einstellungen.bildWaehlen')}
        </FileButton>
        <div className="w-full sm:w-64">
          <Slider
            label={t('browser.einstellungen.hintergrundBlur')}
            hint={`${hintergrundBlur} %`}
            value={hintergrundBlur}
            min={0}
            max={100}
            step={5}
            onValueChange={(v) => setzen({ hintergrundBlur: v })}
          />
        </div>
      </div>
    </Abschnitt>
  )
}


function TabLeisteWahl() {
  const { t } = useTranslation()
  const tabAusrichtung = useEinstellungenStore((s) => s.tabAusrichtung)
  const setzen = useEinstellungenStore((s) => s.setzen)

  if (istAndroid()) return null

  return (
    <Abschnitt titel={t('browser.einstellungen.tabLeiste')}>
      <Auswahlzeile<TabAusrichtung>
        name={t('browser.einstellungen.tabAusrichtung')}
        hinweis={t('browser.einstellungen.tabAusrichtungHinweis')}
        wert={tabAusrichtung}
        optionen={[
          { value: 'vertikal', label: t('browser.einstellungen.tabAusrichtungOption.vertikal') },
          { value: 'horizontal', label: t('browser.einstellungen.tabAusrichtungOption.horizontal') },
        ]}
        aendern={(v) => setzen({ tabAusrichtung: v })}
      />
    </Abschnitt>
  )
}

function Seitendarstellung() {
  const { t } = useTranslation()
  const seitenDunkel = useEinstellungenStore((s) => s.seitenDunkel)
  const setzen = useEinstellungenStore((s) => s.setzen)
  return (
    <Abschnitt titel={t('browser.einstellungen.darstellung')}>
      <Schalterzeile
        name={t('browser.einstellungen.seitenDunkel')}
        hinweis={t('browser.einstellungen.seitenDunkelHinweis')}
        an={seitenDunkel}
        aendern={(an) => setzen({ seitenDunkel: an })}
      />
    </Abschnitt>
  )
}

export function Design() {
  return (
    <>
      <Aufbau />
      <Seitendarstellung />
      <Startwidgets />
      <TabLeisteWahl />
      <Hintergrundwahl />
    </>
  )
}
