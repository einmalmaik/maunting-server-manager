/**
 * Wie der Browser aussieht: wo die Einträge stehen, auf welcher Seite das
 * Panel aufgeht, was in welcher Reihenfolge zu sehen ist, welche Knöpfe da
 * sind, die Widgets samt Nachrichten und der Hintergrund der Startseite.
 */
import { useTranslation } from 'react-i18next'

import { FileButton, Input, Kurzinfo, MultiSelect, Slider } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import { bildZuWebp, FARB_HINTERGRUENDE, HINTERGRUND_OPTIONEN } from '../seite/hintergruende'
import { hintergrundStil } from '../seite/Startseite'


import { WIDGET_SYMBOLE, widgetsGeordnet } from '../seite/start/Widgets'
import {
  EIGENES_BILD_MAX_BYTES,
  KNOEPFE,
  NACHRICHTEN_THEMEN,
  useEinstellungenStore,
  type Hintergrund,
  type Leistenort,
  type NachrichtenThema,
  type TabAusrichtung,
  type Widget,
} from '../services/einstellungenStore'
import { Anordnung } from './Anordnung'
import { istAndroid } from '../services/plattform'
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

export function Design() {
  return (
    <>
      <Aufbau />
      <Startwidgets />
      <TabLeisteWahl />
      <Hintergrundwahl />
    </>
  )
}
