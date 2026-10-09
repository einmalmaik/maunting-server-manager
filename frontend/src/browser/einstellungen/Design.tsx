/**
 * Wie der Browser aussieht: wo die Einträge stehen, auf welcher Seite das
 * Panel aufgeht, was in welcher Reihenfolge zu sehen ist, welche Knöpfe da
 * sind, die Widgets samt Nachrichten und der Hintergrund der Startseite.
 */
import { useTranslation } from 'react-i18next'

import { FileButton, Input, Kurzinfo, MultiSelect } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import { HINTERGRUENDE, hintergrundStil } from '../seite/Startseite'
import { WIDGET_SYMBOLE, widgetsGeordnet } from '../seite/start/Widgets'
import {
  EIGENES_BILD_MAX_BYTES,
  KNOEPFE,
  NACHRICHTEN_THEMEN,
  useEinstellungenStore,
  type Hintergrund,
  type Leistenort,
  type NachrichtenThema,
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
        {KNOEPFE.map((k) => (
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
  const eigenesBild = useEinstellungenStore((s) => s.eigenesBild)
  const setzen = useEinstellungenStore((s) => s.setzen)
  const auswahl: Hintergrund[] = [...(Object.keys(HINTERGRUENDE) as Hintergrund[]), ...(eigenesBild ? (['eigen'] as const) : [])]

  const bildLaden = (datei: File) => {
    if (!datei.type.startsWith('image/') || datei.size > EIGENES_BILD_MAX_BYTES) {
      toast.error(t('browser.einstellungen.bildZuGross'))
      return
    }
    const leser = new FileReader()
    leser.onload = () => typeof leser.result === 'string' && setzen({ eigenesBild: leser.result, hintergrund: 'eigen' })
    leser.readAsDataURL(datei)
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
              onClick={() => setzen({ hintergrund: h })}
              style={hintergrundStil(h, eigenesBild)}
              className={`h-16 w-full rounded-lg border bg-surface ${hintergrund === h ? 'border-primary ring-2 ring-primary/40' : 'border-outline-variant'}`}
            />
          </Kurzinfo>
        ))}
      </div>
      <FileButton accept="image/*" onFile={bildLaden} size="sm" className="self-start">
        {t('browser.einstellungen.bildWaehlen')}
      </FileButton>
    </Abschnitt>
  )
}

export function Design() {
  return (
    <>
      <Aufbau />
      <Startwidgets />
      <Hintergrundwahl />
    </>
  )
}
