/**
 * Eine Tresor-Datei ansehen: in der Lichtbox der Design-DNA, wie Fotos in der
 * Galerie. Die Aktionen stehen oben in der Kopfleiste, Details und frühere
 * Fassungen in der Infoleiste. Textdateien öffnen gleich im Editor.
 *
 * Entschlüsselt wird auf dem Gerät; die Objekt-URL verfällt beim Wechsel, beim
 * Schließen und beim Sperren.
 */

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Crop, Download, File as DateiIcon, FileAudio, FileImage, FileText, FileVideo, Trash2 } from 'lucide-react'
import { Button, Lichtbox, ProgressBar, Versionsliste } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { formatBytes } from '@/components/server/fileHelpers'
import { useVaultStore, type VaultItem } from './vaultStore'
import { ansichtOeffnen, ansichtSchliessen, blobLesen } from './tresorDateien'
import { anzeigeArt, aufGeraetSpeichern } from './tresorAnzeige'
import { fassungenVon } from './tresorOrdner'
import { BEARBEITBAR, TresorBildeditor } from './TresorBildeditor'
import { TresorTexteditor } from './TresorTexteditor'

/** Bis zu dieser Größe öffnet eine Textdatei im Editor, größere werden gespeichert. */
export const TEXT_HOECHSTENS = 1024 * 1024

export function dateiIcon(typ: string) {
  if (typ.startsWith('image/')) return FileImage
  if (typ.startsWith('video/')) return FileVideo
  if (typ.startsWith('audio/')) return FileAudio
  if (typ.startsWith('text/') || typ === 'application/pdf') return FileText
  return DateiIcon
}

/** Ob eine Datei direkt im Editor statt in der Lichtbox aufgeht. */
export function oeffnetImEditor(item: VaultItem): boolean {
  return !!item.datei && anzeigeArt(item.datei.typ) === 'text' && item.datei.original.echt <= TEXT_HOECHSTENS
}

interface Anzeige {
  id: string
  url: string | null
  text: string | null
  anteil: number | null
  fehler: boolean
}

interface Props {
  item: VaultItem
  /** Ort der Datei, etwa „Stammverzeichnis / Verträge“. */
  ort: string
  /** Dateien, durch die man blättern kann (ohne Textdateien, die im Editor aufgehen). */
  folge: VaultItem[]
  onWechseln: (item: VaultItem) => void
  onSchliessen: () => void
}

const KNOPF = 'text-white/85 hover:bg-white/10 hover:text-white'

export function TresorDateiAnsicht({ item: anfang, ort, folge, onWechseln, onSchliessen }: Props) {
  const { t, i18n } = useTranslation()
  const userKey = useVaultStore((s) => s.userKey)
  const trashItem = useVaultStore((s) => s.trashItem)
  const fassungZurueckholen = useVaultStore((s) => s.fassungZurueckholen)
  // Live aus dem Store: nach Bearbeiten oder Zurückholen hat die Datei ein neues Original.
  const item = useVaultStore((s) => s.items.find((i) => i.id === anfang.id)) ?? anfang
  const datei = item.datei
  const art = datei ? anzeigeArt(datei.typ) : null
  const [anzeige, setAnzeige] = useState<Anzeige>({ id: item.id, url: null, text: null, anteil: 0, fehler: false })
  const [bildBearbeiten, setBildBearbeiten] = useState(false)
  const [holt, setHolt] = useState<string | null>(null)

  // Ein neues Original (nach Bearbeiten oder Zurückholen) lädt neu. Im
  // Texteditor nicht: er hält den Text selbst, ein Neuladen würfe ihn samt
  // Rückgängig-Verlauf weg.
  const ladeSchluessel = art === 'text' ? item.id : `${item.id}:${datei?.original.id}`
  useEffect(() => {
    if (!datei || !userKey) return
    const abbruch = new AbortController()
    const id = item.id
    setAnzeige({ id, url: null, text: null, anteil: 0, fehler: false })
    const mitInhalt = art !== null && (art !== 'text' || datei.original.echt <= TEXT_HOECHSTENS)
    if (!mitInhalt) {
      setAnzeige({ id, url: null, text: null, anteil: null, fehler: false })
      return () => abbruch.abort()
    }
    let url: string | null = null
    void (async () => {
      try {
        const blob = await blobLesen(datei.original, id, userKey, datei.typ, {
          zuletzt: true,
          signal: abbruch.signal,
          fortschritt: (anteil) => setAnzeige((a) => (a.id === id ? { ...a, anteil } : a)),
        })
        // Während des Ladens gesperrt oder weitergeblättert: nichts mehr anzeigen.
        if (abbruch.signal.aborted || useVaultStore.getState().userKey !== userKey) return
        const text = art === 'text' ? await blob.text() : null
        url = text === null ? ansichtOeffnen(blob) : null
        setAnzeige({ id, url, text, anteil: null, fehler: false })
      } catch {
        if (!abbruch.signal.aborted) setAnzeige((a) => (a.id === id ? { ...a, anteil: null, fehler: true } : a))
      }
    })()
    return () => {
      abbruch.abort()
      if (url) ansichtSchliessen(url)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ladeSchluessel, userKey])

  if (!datei) return null

  const aufGeraet = async () => {
    if (!userKey) return
    try {
      await aufGeraetSpeichern(datei.original, item.id, userKey, item.service, datei.typ)
    } catch {
      toast.error(t('mss.vault.dateien.speichernFehler'))
    }
  }

  const zurueckholen = async (id: string) => {
    setHolt(id)
    try {
      await fassungZurueckholen(item.id, id)
      toast.success(t('mss.vault.dateien.fassungZurueck'))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('mss.vault.bearbeiten.fehler'))
    } finally {
      setHolt(null)
    }
  }

  // Textdateien haben keine eigene Ansicht: sobald der Text da ist, geht der Editor auf.
  if (anzeige.id === item.id && anzeige.text !== null) {
    return <TresorTexteditor item={item} text={anzeige.text} ort={ort} onFertig={onSchliessen} />
  }
  if (bildBearbeiten && anzeige.url) {
    return <TresorBildeditor item={item} vorschauUrl={anzeige.url} onFertig={() => setBildBearbeiten(false)} />
  }

  const datum = (ms: number) => new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium', timeStyle: 'short' }).format(ms)
  const index = folge.findIndex((f) => f.id === item.id)
  const Icon = dateiIcon(datei.typ)
  const geladen = anzeige.id === item.id

  let inhalt: React.ReactNode
  if (geladen && anzeige.fehler) {
    inhalt = <p className="text-sm text-white/70">{t('mss.vault.dateien.oeffnenFehler')}</p>
  } else if (geladen && anzeige.url && art === 'bild') {
    inhalt = <img src={anzeige.url} alt={item.service} draggable={false} className="max-h-full max-w-full object-contain" />
  } else if (geladen && anzeige.url && art === 'video') {
    inhalt = <video src={anzeige.url} controls className="max-h-full max-w-full" />
  } else if (geladen && anzeige.anteil !== null) {
    inhalt = (
      <div className="w-64 rounded-lg bg-black/60 p-3">
        <ProgressBar value={anzeige.anteil * 100} label={t('mss.vault.dateien.wirdEntschluesselt')} />
      </div>
    )
  } else {
    // Ohne Vorschau (oder Audio): eine ruhige Karte mit dem, was man tun kann.
    inhalt = (
      <div className="flex w-full max-w-sm flex-col items-center gap-4 rounded-2xl border border-white/10 bg-white/[0.04] px-6 py-8 text-center">
        <Icon className="h-14 w-14 text-white/60" aria-hidden />
        <div className="min-w-0 max-w-full">
          <p className="break-words font-semibold text-white">{item.service}</p>
          <p className="mt-0.5 text-label-sm text-white/55">{formatBytes(datei.original.echt)}</p>
        </div>
        {art === 'audio' && anzeige.url ? (
          <audio src={anzeige.url} controls className="w-full" />
        ) : (
          <>
            <p className="text-sm text-white/70">{t('mss.vault.dateien.keineVorschau')}</p>
            <Button type="button" onClick={() => void aufGeraet()}>
              <Download className="mr-1.5 h-4 w-4" />
              {t('mss.vault.dateien.speichern')}
            </Button>
          </>
        )}
      </div>
    )
  }

  const info = (
    <div className="space-y-6">
      <dl className="space-y-3">
        {[
          [t('mss.vault.fotos.info.name'), item.service],
          [t('mss.vault.dateien.ort'), ort],
          [t('mss.vault.fotos.info.groesse'), formatBytes(datei.original.echt)],
          [t('mss.vault.dateien.geaendert'), datum(datei.geaendert ?? item.updatedAt)],
          [t('mss.vault.fotos.info.hinzugefuegt'), datum(item.createdAt)],
        ].map(([name, wert]) => (
          <div key={name}>
            <dt className="text-label-sm text-white/55">{name}</dt>
            <dd className="break-words text-white/90">{wert}</dd>
          </div>
        ))}
      </dl>
      <section aria-label={t('common.versionen.titel')}>
        <h3 className="mb-1 text-sm font-semibold text-white">{t('common.versionen.titel')}</h3>
        {fassungenVon(item).length > 0 && <p className="mb-3 text-label-sm text-white/55">{t('common.versionen.hinweis')}</p>}
        <Versionsliste versionen={fassungenVon(item)} onWiederherstellen={(id) => void zurueckholen(id)} laeuft={holt} aufDunkel />
      </section>
    </div>
  )

  return (
    <Lichtbox
      kennung={item.id}
      titel={item.service}
      untertitel={formatBytes(datei.original.echt)}
      position={index >= 0 && folge.length > 1 ? { index, anzahl: folge.length } : undefined}
      onSchliessen={onSchliessen}
      onVor={index >= 0 && index < folge.length - 1 ? () => onWechseln(folge[index + 1]) : undefined}
      onZurueck={index > 0 ? () => onWechseln(folge[index - 1]) : undefined}
      zoombar={art === 'bild'}
      info={info}
      aktionen={
        <>
          {art === 'bild' && BEARBEITBAR.includes(datei.typ) && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className={KNOPF}
              aria-label={t('mss.vault.bearbeiten.knopf')}
              disabled={!anzeige.url}
              onClick={() => setBildBearbeiten(true)}
            >
              <Crop className="h-4 w-4" />
              <span className="ml-1.5 hidden sm:inline">{t('mss.vault.bearbeiten.knopf')}</span>
            </Button>
          )}
          <Button type="button" variant="ghost" size="sm" className={KNOPF} aria-label={t('mss.vault.dateien.speichern')} onClick={() => void aufGeraet()}>
            <Download className="h-4 w-4" />
            <span className="ml-1.5 hidden sm:inline">{t('mss.vault.dateien.speichern')}</span>
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className={KNOPF}
            aria-label={t('mss.vault.inPapierkorb')}
            onClick={() =>
              void trashItem(item.id).then(() => {
                toast.success(t('mss.vault.inPapierkorbGelegt'))
                onSchliessen()
              })
            }
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </>
      }
    >
      {inhalt}
    </Lichtbox>
  )
}
