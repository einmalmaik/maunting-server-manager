/**
 * Einstellungen des Browsers. Was Rust braucht (Schild, Download-Ordner,
 * Vergessen beim Schließen), geht über `useGeraetKonfig`; der Rest liegt in
 * `einstellungenStore`.
 */
import { useEffect, useState, type ReactNode } from 'react'
import { open as ordnerWaehlen } from '@tauri-apps/plugin-dialog'
import { X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button, confirm, Dropdown, FileButton, Input, Kurzinfo, Switch } from '@/Singra/UI'
import { abmelden } from '@/desktop/auth'
import { toast } from '@/stores/toastStore'

import { MarkenSymbol } from '../marken'
import { HINTERGRUENDE, hintergrundStil } from '../seite/Startseite'
import { EIGENES_BILD_MAX_BYTES, useEinstellungenStore, type Hintergrund, type Modul } from '../services/einstellungenStore'
import { useGeraetKonfig } from '../services/geraetKonfig'
import { nativ } from '../services/nativ'
import { searxngBasis, SUCHMASCHINEN, type SuchmaschinenId } from '../services/searchEngines'
import { istGekoppelt, useSitzung } from '../services/sitzung'
import { useVerlaufStore } from '../services/verlaufStore'

function Abschnitt({ titel, children }: { titel: string; children: ReactNode }) {
  return (
    <section className="border-b border-outline-variant px-4 py-4 last:border-b-0">
      <h3 className="mb-3 text-label-md text-on-surface-variant">{titel}</h3>
      <div className="flex flex-col gap-3">{children}</div>
    </section>
  )
}

function Schalterzeile({ id, text, hinweis, checked, onChange }: { id: string; text: string; hinweis?: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div>
        <p id={id} className="text-body-sm text-on-surface">{text}</p>
        {hinweis && <p className="mt-0.5 text-label-sm text-on-surface-variant">{hinweis}</p>}
      </div>
      <Switch aria-labelledby={id} checked={checked} onCheckedChange={onChange} />
    </div>
  )
}

function Suche() {
  const { t } = useTranslation()
  const { suchmaschine, searxngUrl, setzen } = useEinstellungenStore()
  const [adresse, setAdresse] = useState(searxngUrl ?? '')
  const gueltig = !adresse.trim() || searxngBasis(adresse) !== null
  return (
    <Abschnitt titel={t('browser.einstellungen.suche')}>
      <Dropdown
        aria-label={t('browser.start.suchmaschine')}
        value={suchmaschine}
        onChange={(v) => setzen({ suchmaschine: v as SuchmaschinenId })}
        options={SUCHMASCHINEN.map((s) => ({ value: s.id, label: s.name, icon: <MarkenSymbol marke={s.marke} /> }))}
      />
      {suchmaschine === 'searxng' && (
        <Input
          label={t('browser.einstellungen.searxngAdresse')}
          placeholder="https://search.example.org"
          value={adresse}
          error={gueltig ? undefined : t('browser.einstellungen.searxngUngueltig')}
          onChange={(e) => setAdresse(e.target.value)}
          onBlur={() => gueltig && setzen({ searxngUrl: searxngBasis(adresse) })}
        />
      )}
    </Abschnitt>
  )
}

function Schutz() {
  const { t } = useTranslation()
  const konfig = useGeraetKonfig((s) => s.konfig)
  const aendern = useGeraetKonfig((s) => s.aendern)
  const [stand, setStand] = useState<{ name: string; alter_sekunden: number | null }[]>([])
  const vergessen = useVerlaufStore((s) => s.verlaufLeeren)

  useEffect(() => {
    void nativ.schildStand().then((s) => s && setStand(s.listen)).catch(() => null)
  }, [])

  const aendernMitMeldung = (felder: Parameters<typeof aendern>[0]) =>
    aendern(felder).catch((e) => toast.error(e instanceof Error ? e.message : String(e)))

  const datenLoeschen = async () => {
    const ja = await confirm({
      title: t('browser.einstellungen.datenLoeschenTitel'),
      message: t('browser.einstellungen.datenLoeschenText'),
      confirmText: t('browser.einstellungen.datenLoeschen'),
      danger: true,
    })
    if (!ja) return
    try {
      await nativ.seitendatenLoeschen()
      vergessen()
      toast.success(t('browser.einstellungen.datenGeloescht'))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <Abschnitt titel={t('browser.einstellungen.schutz')}>
      <Schalterzeile
        id="msb-e-schild"
        text={t('browser.einstellungen.schild')}
        hinweis={t('browser.einstellungen.schildHinweis')}
        checked={konfig?.schild_aktiv ?? true}
        onChange={(v) => void aendernMitMeldung({ schild_aktiv: v })}
      />
      {stand.length > 0 && (
        <ul className="text-label-sm text-on-surface-variant">
          {stand.map((l) => (
            <li key={l.name}>
              {l.alter_sekunden === null
                ? t('browser.einstellungen.listeEingebaut', { name: l.name })
                : t('browser.einstellungen.listeStand', { name: l.name, stunden: Math.round(l.alter_sekunden / 3600) })}
            </li>
          ))}
        </ul>
      )}
      {(konfig?.schild_ausnahmen.length ?? 0) > 0 && (
        <div>
          <p className="mb-1 text-body-sm text-on-surface">{t('browser.einstellungen.ausnahmen')}</p>
          <ul className="flex flex-col gap-1">
            {konfig!.schild_ausnahmen.map((host) => (
              <li key={host} className="flex items-center justify-between rounded-md bg-surface-container px-2 py-1 text-body-sm">
                <span className="truncate">{host}</span>
                <Kurzinfo text={t('browser.einstellungen.ausnahmeEntfernen')} seite="ende">
                  <button
                    type="button"
                    onClick={() => void aendernMitMeldung({ schild_ausnahmen: konfig!.schild_ausnahmen.filter((h) => h !== host) })}
                    aria-label={t('browser.einstellungen.ausnahmeEntfernenName', { host })}
                    className="flex h-6 w-6 items-center justify-center rounded text-on-surface-variant hover:text-status-destructive"
                  >
                    <X className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                </Kurzinfo>
              </li>
            ))}
          </ul>
        </div>
      )}
      <Schalterzeile
        id="msb-e-vergessen"
        text={t('browser.einstellungen.vergessen')}
        hinweis={t('browser.einstellungen.vergessenHinweis')}
        checked={konfig?.vergessen_beim_schliessen ?? false}
        onChange={(v) => void aendernMitMeldung({ vergessen_beim_schliessen: v })}
      />
      <Button variant="secondary" size="sm" className="self-start" onClick={() => void datenLoeschen()}>
        {t('browser.einstellungen.datenLoeschen')}
      </Button>
    </Abschnitt>
  )
}

function Downloads() {
  const { t } = useTranslation()
  const konfig = useGeraetKonfig((s) => s.konfig)
  const aendern = useGeraetKonfig((s) => s.aendern)
  const [ordner, setOrdner] = useState<string | null>(null)

  useEffect(() => {
    void nativ.downloadOrdner().then(setOrdner).catch(() => null)
  }, [konfig?.download_ordner])

  const waehlen = async () => {
    const gewaehlt = await ordnerWaehlen({ directory: true, multiple: false }).catch(() => null)
    if (typeof gewaehlt === 'string') await aendern({ download_ordner: gewaehlt }).catch((e) => toast.error(String(e)))
  }

  return (
    <Abschnitt titel={t('browser.einstellungen.downloads')}>
      <p className="break-all rounded-md bg-surface-container px-2 py-1.5 font-mono text-mono-sm text-on-surface">{ordner ?? '…'}</p>
      <div className="flex gap-2">
        <Button variant="secondary" size="sm" onClick={() => void waehlen()}>
          {t('browser.einstellungen.ordnerWaehlen')}
        </Button>
        {konfig?.download_ordner && (
          <Button variant="ghost" size="sm" onClick={() => void aendern({ download_ordner: null })}>
            {t('browser.einstellungen.ordnerStandard')}
          </Button>
        )}
      </div>
    </Abschnitt>
  )
}

function Darstellung() {
  const { t } = useTranslation()
  const { hintergrund, eigenesBild, ausgeblendet, setzen, modulUmschalten } = useEinstellungenStore()
  const auswahl: Hintergrund[] = [...(Object.keys(HINTERGRUENDE) as Hintergrund[]), ...(eigenesBild ? (['eigen'] as const) : [])]
  const module: Modul[] = ['singra', 'messenger', 'notizen', 'kalender', 'tresor']

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
    <Abschnitt titel={t('browser.einstellungen.darstellung')}>
      <div role="radiogroup" aria-label={t('browser.einstellungen.hintergrund')} className="grid grid-cols-5 gap-2">
        {auswahl.map((h) => (
          <Kurzinfo key={h} text={t(`browser.hintergrund.${h}`)}>
            <button
              type="button"
              role="radio"
              aria-checked={hintergrund === h}
              aria-label={t(`browser.hintergrund.${h}`)}
              onClick={() => setzen({ hintergrund: h })}
              style={hintergrundStil(h, eigenesBild)}
              className={`h-10 w-full rounded-md border bg-surface ${hintergrund === h ? 'border-primary ring-2 ring-primary/40' : 'border-outline-variant'}`}
            />
          </Kurzinfo>
        ))}
      </div>
      <FileButton accept="image/*" onFile={bildLaden} size="sm" className="self-start">
        {t('browser.einstellungen.bildWaehlen')}
      </FileButton>
      <p className="mt-2 text-body-sm text-on-surface">{t('browser.einstellungen.leiste')}</p>
      {module.map((m) => (
        <Schalterzeile
          key={m}
          id={`msb-e-modul-${m}`}
          text={t(`browser.leiste.${m}`)}
          checked={!ausgeblendet.includes(m)}
          onChange={() => modulUmschalten(m)}
        />
      ))}
    </Abschnitt>
  )
}

function Kopplung() {
  const { t } = useTranslation()
  const stand = useSitzung((s) => s.stand)
  const adresse = useSitzung((s) => s.adresse)
  if (!istGekoppelt(stand)) return null
  const trennen = async () => {
    const ja = await confirm({
      title: t('browser.einstellungen.trennenTitel'),
      message: t('browser.einstellungen.trennenText'),
      confirmText: t('browser.einstellungen.trennen'),
      danger: true,
    })
    if (ja) await abmelden()
  }
  return (
    <Abschnitt titel={t('browser.einstellungen.kopplung')}>
      <p className="text-body-sm text-on-surface">
        {stand === 'an' ? t('browser.einstellungen.gekoppeltMit', { adresse }) : t('browser.einstellungen.gekoppeltOffline', { adresse })}
      </p>
      <Button variant="secondary" size="sm" className="self-start" onClick={() => void trennen()}>
        {t('browser.einstellungen.trennen')}
      </Button>
    </Abschnitt>
  )
}

export function EinstellungenPanel() {
  return (
    <div className="h-full overflow-y-auto">
      <Suche />
      <Schutz />
      <Downloads />
      <Darstellung />
      <Kopplung />
    </div>
  )
}
