/**
 * Der Bereich „Dateien“ im Tresor: Ordner, Hochladen, Öffnen, Speichern.
 *
 * Name, Typ und Größe einer Datei stehen nur im verschlüsselten Eintrag; die
 * Liste hier kommt vollständig aus dem entsperrten Tresor. Geöffnet wird auf
 * dem Gerät, als Objekt-URL, die beim Schließen und beim Sperren verfällt.
 */

import React, { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Archive,
  ChevronRight,
  Download,
  File as DateiIcon,
  FileImage,
  FileText,
  FileVideo,
  Folder,
  FolderPlus,
  HardDrive,
  Pencil,
  Trash2,
  Upload,
} from 'lucide-react'
import { ActionMenu, Button, Dialog, DialogContent, DialogHeader, DialogTitle, FileButton, ProgressBar } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { prompt } from '@/stores/promptStore'
import { formatBytes } from '@/components/server/fileHelpers'
import { useVaultStore, type VaultItem } from './vaultStore'
import { ansichtOeffnen, ansichtSchliessen, blobLesen, useTresorUploads } from './tresorDateien'
import { speicherAbfragen, type TresorSpeicher } from './tresorBlobApi'
import { anzeigeArt, speichernUnter } from './tresorAnzeige'
import { TresorTexteditor } from './TresorTexteditor'

function dateiIcon(typ: string) {
  if (typ.startsWith('image/')) return FileImage
  if (typ.startsWith('video/')) return FileVideo
  if (typ.startsWith('text/') || typ === 'application/pdf') return FileText
  return DateiIcon
}

const TEXT_HOECHSTENS = 1024 * 1024

interface Geoeffnet {
  item: VaultItem
  url: string | null
  text: string | null
  anteil: number
  fehler: boolean
  bearbeiten?: boolean
}

export function TresorDateiBereich() {
  const { t } = useTranslation()
  const items = useVaultStore((s) => s.items)
  const userKey = useVaultStore((s) => s.userKey)
  const dateiHinzufuegen = useVaultStore((s) => s.dateiHinzufuegen)
  const ordnerAnlegen = useVaultStore((s) => s.ordnerAnlegen)
  const saveItem = useVaultStore((s) => s.saveItem)
  const trashItem = useVaultStore((s) => s.trashItem)
  const setArchived = useVaultStore((s) => s.setArchived)
  const uploads = useTresorUploads((s) => s.je)
  const [ordner, setOrdner] = useState<string | undefined>(undefined)
  const [speicher, setSpeicher] = useState<TresorSpeicher | null>(null)
  const [ziehen, setZiehen] = useState(false)
  const [geoeffnet, setGeoeffnet] = useState<Geoeffnet | null>(null)
  const [vorbereitung, setVorbereitung] = useState(0)

  const sichtbar = (i: VaultItem) => !i.trashedAt && !i.archivedAt
  const ordnerListe = useMemo(() => items.filter((i) => i.category === 'ordner'), [items])

  // Liegt der geöffnete Ordner nicht mehr da (gelöscht, in den Papierkorb), zurück nach oben.
  const aktuellerOrdner = ordner && ordnerListe.some((o) => o.id === ordner && sichtbar(o)) ? ordner : undefined

  const pfad = useMemo(() => {
    const kette: VaultItem[] = []
    let id = aktuellerOrdner
    while (id && kette.length < 50) {
      const o = ordnerListe.find((x) => x.id === id)
      if (!o) break
      kette.unshift(o)
      id = o.ordner
    }
    return kette
  }, [aktuellerOrdner, ordnerListe])

  const inhalt = useMemo(() => {
    const hier = items.filter((i) => (i.category === 'ordner' || i.category === 'datei') && sichtbar(i) && i.ordner === aktuellerOrdner)
    const nachName = (a: VaultItem, b: VaultItem) => a.service.localeCompare(b.service)
    return [...hier.filter((i) => i.category === 'ordner').sort(nachName), ...hier.filter((i) => i.category === 'datei').sort(nachName)]
  }, [items, aktuellerOrdner])

  const speicherLaden = () => {
    speicherAbfragen()
      .then(setSpeicher)
      .catch(() => setSpeicher(null))
  }
  useEffect(speicherLaden, [])
  const anzahlUploads = Object.keys(uploads).length
  useEffect(() => {
    if (anzahlUploads === 0) speicherLaden()
  }, [anzahlUploads])

  // Die Objekt-URL gehört genau zur offenen Ansicht.
  useEffect(() => {
    const url = geoeffnet?.url
    return () => {
      if (url) ansichtSchliessen(url)
    }
  }, [geoeffnet?.url])

  const hochladen = async (dateien: File[]) => {
    setVorbereitung((n) => n + dateien.length)
    for (const datei of dateien) {
      try {
        await dateiHinzufuegen(datei, aktuellerOrdner)
      } catch (err) {
        toast.error(err instanceof Error ? err.message : t('mss.vault.dateien.hochladenFehler'))
      } finally {
        setVorbereitung((n) => n - 1)
      }
    }
  }

  const neuerOrdner = async () => {
    const name = await prompt({ message: t('mss.vault.dateien.ordnerName'), confirmText: t('mss.vault.dateien.ordnerAnlegen') })
    if (!name?.trim()) return
    await ordnerAnlegen(name.trim(), aktuellerOrdner)
  }

  const umbenennen = async (item: VaultItem) => {
    const name = await prompt({ message: t('mss.vault.dateien.neuerName'), defaultValue: item.service })
    if (!name?.trim() || name.trim() === item.service) return
    await saveItem({ ...item, service: name.trim() })
  }

  const oeffnen = async (item: VaultItem) => {
    if (!item.datei || !userKey) return
    const art = anzeigeArt(item.datei.typ)
    setGeoeffnet({ item, url: null, text: null, anteil: 0, fehler: false })
    try {
      const blob = await blobLesen(item.datei.original, item.id, userKey, item.datei.typ, {
        fortschritt: (anteil) => setGeoeffnet((g) => (g?.item.id === item.id ? { ...g, anteil } : g)),
      })
      const text = art === 'text' && blob.size <= TEXT_HOECHSTENS ? await blob.text() : null
      // Während des Ladens gesperrt: nichts mehr anzeigen.
      if (useVaultStore.getState().userKey !== userKey) return
      const url = ansichtOeffnen(blob)
      setGeoeffnet((g) => {
        if (g?.item.id !== item.id) {
          ansichtSchliessen(url)
          return g
        }
        return { ...g, url, text }
      })
    } catch {
      setGeoeffnet((g) => (g?.item.id === item.id ? { ...g, fehler: true } : g))
    }
  }

  const auswahlZiehen = (e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes('Files')) return
    e.preventDefault()
    setZiehen(true)
  }

  const art = geoeffnet?.item.datei ? anzeigeArt(geoeffnet.item.datei.typ) : null

  return (
    <div
      className={`flex-1 overflow-y-auto px-4 py-3 space-y-3 ${ziehen ? 'bg-primary/5 outline-dashed outline-2 outline-primary/40 -outline-offset-4' : ''}`}
      onDragOver={auswahlZiehen}
      onDragLeave={() => setZiehen(false)}
      onDrop={(e) => {
        e.preventDefault()
        setZiehen(false)
        const dateien = Array.from(e.dataTransfer.files)
        if (dateien.length > 0) void hochladen(dateien)
      }}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <nav aria-label={t('mss.vault.dateien.pfad')} className="flex min-w-0 flex-wrap items-center gap-1 text-xs">
          <button type="button" onClick={() => setOrdner(undefined)} className="font-semibold text-on-surface hover:text-primary">
            {t('mss.vault.ansicht.dateien')}
          </button>
          {pfad.map((o) => (
            <React.Fragment key={o.id}>
              <ChevronRight className="h-3 w-3 text-on-surface-variant" />
              <button type="button" onClick={() => setOrdner(o.id)} className="truncate text-on-surface hover:text-primary">
                {o.service}
              </button>
            </React.Fragment>
          ))}
        </nav>
        <div className="flex items-center gap-1.5">
          <Button type="button" variant="ghost" size="sm" onClick={() => void neuerOrdner()}>
            <FolderPlus className="mr-1 h-3.5 w-3.5" />
            {t('mss.vault.dateien.ordnerAnlegen')}
          </Button>
          <FileButton multiple size="sm" variant="primary" onFiles={(dateien) => void hochladen(dateien)}>
            <Upload className="mr-1 h-3.5 w-3.5" />
            {t('mss.vault.dateien.hochladen')}
          </FileButton>
        </div>
      </div>

      {speicher && (
        <div className="flex items-center gap-2 rounded-xl border border-outline-variant/20 bg-surface-container-low px-3 py-2">
          <HardDrive className="h-4 w-4 shrink-0 text-on-surface-variant" />
          <ProgressBar
            value={speicher.quote > 0 ? (speicher.belegt / speicher.quote) * 100 : null}
            heat
            ariaLabel={t('mss.vault.dateien.speicher')}
            hint={t('mss.vault.dateien.speicherBelegt', { belegt: formatBytes(speicher.belegt), quote: formatBytes(speicher.quote) })}
          />
        </div>
      )}

      {vorbereitung > 0 && (
        <p className="text-label-sm text-on-surface-variant">{t('mss.vault.dateien.verschluesselt', { count: vorbereitung })}</p>
      )}

      {inhalt.length === 0 ? (
        <div className="flex flex-col items-center justify-center p-8 text-center text-xs text-on-surface-variant">
          <Upload className="mb-2 h-6 w-6 opacity-60" />
          {t('mss.vault.dateien.leer')}
        </div>
      ) : (
        <ul className="grid grid-cols-1 gap-1.5">
          {inhalt.map((item) => {
            const istOrdner = item.category === 'ordner'
            const Icon = istOrdner ? Folder : dateiIcon(item.datei?.typ ?? '')
            const upload = uploads[item.id]
            return (
              <li
                key={item.id}
                className="flex items-center gap-3 rounded-xl border border-outline-variant/20 bg-surface-container p-2.5 hover:bg-surface-container-high"
              >
                <button
                  type="button"
                  className="flex min-w-0 flex-1 items-center gap-3 text-left"
                  onClick={() => (istOrdner ? setOrdner(item.id) : void oeffnen(item))}
                  disabled={!istOrdner && !item.datei}
                >
                  <Icon className={`h-5 w-5 shrink-0 ${istOrdner ? 'text-primary' : 'text-on-surface-variant'}`} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-semibold text-on-surface">{item.service}</span>
                    <span className="block text-label-sm text-on-surface-variant">
                      {istOrdner
                        ? t('mss.vault.dateien.ordner')
                        : item.datei
                          ? `${formatBytes(item.datei.original.echt)} · ${new Date(item.createdAt).toLocaleDateString()}`
                          : t('mss.vault.dateien.unlesbar')}
                    </span>
                  </span>
                </button>
                {upload && (
                  <div className="w-28 shrink-0">
                    {upload.fehler === 'speicherVoll' ? (
                      <span className="text-label-sm text-status-destructive">{t('mss.vault.dateien.speicherVoll')}</span>
                    ) : (
                      <ProgressBar
                        value={upload.gesamt > 0 ? (upload.gesendet / upload.gesamt) * 100 : null}
                        ariaLabel={t('mss.vault.dateien.wirdHochgeladen')}
                      />
                    )}
                  </div>
                )}
                <ActionMenu
                  compact
                  align="end"
                  label={t('mss.vault.dateien.aktionen')}
                  items={[
                    { key: 'umbenennen', label: t('mss.vault.dateien.umbenennen'), icon: <Pencil className="h-3.5 w-3.5" />, onSelect: () => void umbenennen(item) },
                    { key: 'archiv', label: t('mss.vault.archivieren'), icon: <Archive className="h-3.5 w-3.5" />, onSelect: () => void setArchived(item.id, true) },
                    {
                      key: 'papierkorb',
                      label: t('mss.vault.inPapierkorb'),
                      icon: <Trash2 className="h-3.5 w-3.5" />,
                      destructive: true,
                      separatorBefore: true,
                      onSelect: () => void trashItem(item.id).then(() => toast.success(t('mss.vault.inPapierkorbGelegt'))),
                    },
                  ]}
                />
              </li>
            )
          })}
        </ul>
      )}

      {/* Beim Bearbeiten schließt nur der Editor selbst, damit nichts Ungespeichertes verloren geht. */}
      <Dialog open={!!geoeffnet} onOpenChange={(offen) => !offen && !geoeffnet?.bearbeiten && setGeoeffnet(null)}>
        {geoeffnet?.bearbeiten && geoeffnet.text !== null ? (
          <DialogContent className="max-w-5xl" showCloseButton={false}>
            <div className="p-3">
              <TresorTexteditor
                item={geoeffnet.item}
                text={geoeffnet.text}
                onFertig={(gespeichert) => {
                  if (gespeichert === null) {
                    setGeoeffnet((g) => (g ? { ...g, bearbeiten: false } : g))
                    return
                  }
                  // Die Ansicht zeigt ab jetzt die neue Fassung; die alte URL verfällt mit dem Wechsel.
                  const url = ansichtOeffnen(new Blob([gespeichert], { type: geoeffnet.item.datei?.typ || 'text/plain' }))
                  setGeoeffnet((g) => (g ? { ...g, bearbeiten: false, text: gespeichert, url } : g))
                }}
              />
            </div>
          </DialogContent>
        ) : geoeffnet && (
          <DialogContent className="max-w-4xl">
            <DialogHeader>
              <DialogTitle className="truncate">{geoeffnet.item.service}</DialogTitle>
            </DialogHeader>
            <div className="flex min-h-[12rem] items-center justify-center">
              {geoeffnet.fehler ? (
                <p className="text-xs text-status-destructive">{t('mss.vault.dateien.oeffnenFehler')}</p>
              ) : !geoeffnet.url ? (
                <div className="w-64">
                  <ProgressBar value={geoeffnet.anteil * 100} label={t('mss.vault.dateien.wirdEntschluesselt')} />
                </div>
              ) : art === 'bild' ? (
                <img src={geoeffnet.url} alt={geoeffnet.item.service} className="max-h-[70vh] max-w-full rounded-lg object-contain" />
              ) : art === 'video' ? (
                <video src={geoeffnet.url} controls className="max-h-[70vh] max-w-full rounded-lg" />
              ) : art === 'audio' ? (
                <audio src={geoeffnet.url} controls className="w-full" />
              ) : art === 'text' && geoeffnet.text !== null ? (
                <pre className="max-h-[70vh] w-full overflow-auto whitespace-pre-wrap rounded-lg bg-surface-container-low p-3 text-xs">{geoeffnet.text}</pre>
              ) : (
                <p className="text-xs text-on-surface-variant">{t('mss.vault.dateien.keineVorschau')}</p>
              )}
            </div>
            {geoeffnet.url && (
              <div className="flex justify-end gap-2">
                {art === 'text' && geoeffnet.text !== null && (
                  <Button type="button" variant="secondary" size="sm" onClick={() => setGeoeffnet((g) => (g ? { ...g, bearbeiten: true } : g))}>
                    <Pencil className="mr-1 h-3.5 w-3.5" />
                    {t('mss.vault.bearbeiten.knopf')}
                  </Button>
                )}
                <Button type="button" size="sm" onClick={() => speichernUnter(geoeffnet.url!, geoeffnet.item.service)}>
                  <Download className="mr-1 h-3.5 w-3.5" />
                  {t('mss.vault.dateien.speichern')}
                </Button>
              </div>
            )}
          </DialogContent>
        )}
      </Dialog>
    </div>
  )
}
