/**
 * Der Bereich „Dateien“ im Tresor: Ordner, Hochladen, Öffnen, Verschieben.
 *
 * Name, Typ und Größe einer Datei stehen nur im verschlüsselten Eintrag; die
 * Liste hier kommt vollständig aus dem entsperrten Tresor. Geöffnet wird auf
 * dem Gerät (`TresorDateiAnsicht`).
 *
 * Bedient wird wie im Explorer: Einträge lassen sich auf Ordner, auf einen
 * Teil der Pfadleiste oder in den Ordnerbaum ziehen; Dateien vom Rechner, die
 * auf einem Ordner landen, werden dorthin hochgeladen. Rechtsklick und der
 * Knopf am Ende jeder Zeile öffnen dasselbe Menü. Auf Touch-Geräten gibt es
 * kein Ziehen, dort verschiebt „Verschieben“ im Menü.
 */

import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Archive,
  Download,
  Folder,
  FolderInput,
  FolderOpen,
  FolderPlus,
  HardDrive,
  HardDriveDownload,
  MoreHorizontal,
  Pencil,
  Trash2,
  Upload,
} from 'lucide-react'
import {
  Button,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  Dropdown,
  FileButton,
  Kontextmenue,
  Pfadleiste,
  ProgressBar,
  type ActionMenuItem,
} from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { prompt } from '@/stores/promptStore'
import { formatBytes } from '@/components/server/fileHelpers'
import { cx } from '@/utils/classNames'
import { useVaultStore, type VaultItem } from './vaultStore'
import { angeheftet, offlineAnheften, offlineLoesen, useTresorUploads } from './tresorDateien'
import { speicherAbfragen, type TresorSpeicher } from './tresorBlobApi'
import { aufGeraetSpeichern } from './tresorAnzeige'
import { darfVerschieben, pfadVon, zielOrdner } from './tresorOrdner'
import { TresorOrdnerBaum } from './TresorOrdnerBaum'
import { TresorDateiAnsicht, dateiIcon, oeffnetImEditor } from './TresorDateiAnsicht'

/** Kennzeichnet beim Ziehen einen Eintrag aus dem Tresor (im Unterschied zu Dateien vom Rechner). */
export const ZIEH_TYP = 'application/x-msm-tresor'

/** Spalten der Liste ab `lg`: Name, Größe, Geändert, Menü. Darunter stehen Größe und Datum unter dem Namen. */
const SPALTEN = 'lg:grid lg:grid-cols-[minmax(0,1fr)_6.5rem_10rem_2.5rem] lg:items-center lg:gap-3'

export function TresorDateiBereich() {
  const { t, i18n } = useTranslation()
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
  // Speicher gibt nur eine Rolle. Ohne sie bleibt der Upload zu, der Server weist ihn ohnehin ab.
  const ohneSpeicher = speicher?.quote === 0
  const [ansicht, setAnsicht] = useState<VaultItem | null>(null)
  const [vorbereitung, setVorbereitung] = useState(0)
  const [verschiebenDialog, setVerschiebenDialog] = useState<{ item: VaultItem; ziel: string } | null>(null)
  const [offline, setOffline] = useState<Set<string>>(new Set())
  const [holt, setHolt] = useState<Record<string, number>>({})
  const [menue, setMenue] = useState<{ item: VaultItem; x: number; y: number; ausloeser: HTMLElement } | null>(null)
  /** Eintrag, der gerade gezogen wird; nur innerhalb dieses Fensters bekannt. */
  const [gezogen, setGezogen] = useState<string | null>(null)
  /** Ordnerzeile, über der gerade etwas schwebt. */
  const [zielZeile, setZielZeile] = useState<string | null>(null)
  // `dragenter`/`dragleave` feuern auch beim Wechsel zwischen Kindelementen;
  // erst wenn der Zähler auf null fällt, hat die Datei die Fläche verlassen.
  const ziehZaehler = useRef(0)
  const [rechnerDateiDarueber, setRechnerDateiDarueber] = useState(false)

  const sichtbar = (i: VaultItem) => !i.trashedAt && !i.archivedAt
  const ordnerListe = useMemo(() => items.filter((i) => i.category === 'ordner' && sichtbar(i)), [items])

  // Liegt der geöffnete Ordner nicht mehr da (gelöscht, in den Papierkorb), zurück nach oben.
  const aktuellerOrdner = ordner && ordnerListe.some((o) => o.id === ordner) ? ordner : undefined
  const pfad = useMemo(() => pfadVon(aktuellerOrdner, ordnerListe), [aktuellerOrdner, ordnerListe])
  const ort = [t('mss.vault.dateien.stamm'), ...pfad.map((o) => o.service)].join(' / ')
  const ordnerName = (id: string | undefined) => (id ? ordnerListe.find((o) => o.id === id)?.service : undefined) ?? t('mss.vault.dateien.stamm')

  const inhalt = useMemo(() => {
    const hier = items.filter((i) => (i.category === 'ordner' || i.category === 'datei') && sichtbar(i) && i.ordner === aktuellerOrdner)
    const nachName = (a: VaultItem, b: VaultItem) => a.service.localeCompare(b.service)
    return [...hier.filter((i) => i.category === 'ordner').sort(nachName), ...hier.filter((i) => i.category === 'datei').sort(nachName)]
  }, [items, aktuellerOrdner])

  // Welche Originale dieser Ebene angeheftet sind (nur dieses Gerät).
  const originale = inhalt.flatMap((i) => (i.datei ? [i.datei.original.id] : [])).join(',')
  const offlineLaden = () => {
    void angeheftet(originale ? originale.split(',') : []).then(setOffline).catch(() => setOffline(new Set()))
  }
  useEffect(offlineLaden, [originale])

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

  const aufGeraet = async (item: VaultItem) => {
    if (!item.datei || !userKey) return
    try {
      await aufGeraetSpeichern(item.datei.original, item.id, userKey, item.service, item.datei.typ)
    } catch {
      toast.error(t('mss.vault.dateien.speichernFehler'))
    }
  }

  const offlineUmschalten = async (item: VaultItem) => {
    if (!item.datei) return
    const kopf = item.datei.original
    if (offline.has(kopf.id)) {
      await offlineLoesen(kopf.id)
      offlineLaden()
      return
    }
    setHolt((h) => ({ ...h, [item.id]: 0 }))
    try {
      await offlineAnheften(kopf, (anteil) => setHolt((h) => ({ ...h, [item.id]: anteil })))
      toast.success(t('mss.vault.dateien.offlineFertig', { name: item.service }))
    } catch {
      toast.error(t('mss.vault.dateien.offlineFehler'))
    } finally {
      setHolt((h) => {
        const rest = { ...h }
        delete rest[item.id]
        return rest
      })
      offlineLaden()
    }
  }

  const hochladen = async (dateien: File[], ziel: string | undefined) => {
    if (ohneSpeicher || dateien.length === 0) return
    setVorbereitung((n) => n + dateien.length)
    for (const datei of dateien) {
      try {
        await dateiHinzufuegen(datei, ziel)
      } catch (err) {
        toast.error(err instanceof Error ? err.message : t('mss.vault.dateien.hochladenFehler'))
      } finally {
        setVorbereitung((n) => n - 1)
      }
    }
  }

  const verschieben = async (item: VaultItem, ziel: string | undefined) => {
    if (!darfVerschieben(item, ziel, ordnerListe)) return
    try {
      await saveItem({ ...item, ordner: ziel })
      toast.success(t('mss.vault.dateien.verschoben', { name: item.service, ordner: ordnerName(ziel) }))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
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

  const oeffnen = (item: VaultItem) => {
    if (item.category === 'ordner') setOrdner(item.id)
    else if (item.datei) setAnsicht(item)
  }

  // ── Ziehen und Ablegen ────────────────────────────────────────────────────

  /** Ob über dem Ordner `ziel` (`undefined` = Stamm) abgelegt werden darf. */
  const kannAblegen = (ziel: string | undefined, daten: DataTransfer) => {
    if (daten.types.includes(ZIEH_TYP)) {
      const item = gezogen ? items.find((i) => i.id === gezogen) : undefined
      return !!item && darfVerschieben(item, ziel, ordnerListe)
    }
    return daten.types.includes('Files') && !ohneSpeicher
  }

  const ablegen = (ziel: string | undefined, daten: DataTransfer) => {
    ziehZaehler.current = 0
    setRechnerDateiDarueber(false)
    setZielZeile(null)
    const id = daten.getData(ZIEH_TYP)
    if (id) {
      const item = items.find((i) => i.id === id)
      if (item) void verschieben(item, ziel)
      return
    }
    void hochladen(Array.from(daten.files), ziel)
  }

  const zeileZiehen = (item: VaultItem) => ({
    draggable: true,
    onDragStart: (event: React.DragEvent) => {
      event.dataTransfer.setData(ZIEH_TYP, item.id)
      event.dataTransfer.effectAllowed = 'move'
      setGezogen(item.id)
    },
    onDragEnd: () => {
      setGezogen(null)
      setZielZeile(null)
    },
  })

  const ordnerAblage = (o: VaultItem) => ({
    onDragOver: (event: React.DragEvent) => {
      if (!kannAblegen(o.id, event.dataTransfer)) return
      event.preventDefault()
      event.stopPropagation()
      setZielZeile(o.id)
    },
    onDragLeave: () => setZielZeile((z) => (z === o.id ? null : z)),
    onDrop: (event: React.DragEvent) => {
      if (!kannAblegen(o.id, event.dataTransfer)) return
      event.preventDefault()
      event.stopPropagation()
      ablegen(o.id, event.dataTransfer)
    },
  })

  // Die ganze Fläche nimmt Dateien vom Rechner für den geöffneten Ordner an.
  const flaecheAblage = {
    onDragEnter: (event: React.DragEvent) => {
      if (!event.dataTransfer.types.includes('Files') || event.dataTransfer.types.includes(ZIEH_TYP)) return
      ziehZaehler.current += 1
      setRechnerDateiDarueber(true)
    },
    onDragLeave: (event: React.DragEvent) => {
      if (!event.dataTransfer.types.includes('Files') || event.dataTransfer.types.includes(ZIEH_TYP)) return
      ziehZaehler.current = Math.max(0, ziehZaehler.current - 1)
      if (ziehZaehler.current === 0) setRechnerDateiDarueber(false)
    },
    onDragOver: (event: React.DragEvent) => {
      if (kannAblegen(aktuellerOrdner, event.dataTransfer) && !event.dataTransfer.types.includes(ZIEH_TYP)) event.preventDefault()
    },
    onDrop: (event: React.DragEvent) => {
      event.preventDefault()
      if (event.dataTransfer.types.includes(ZIEH_TYP)) return
      ablegen(aktuellerOrdner, event.dataTransfer)
    },
  }

  // ── Menü ──────────────────────────────────────────────────────────────────

  const menueEintraege = (item: VaultItem): ActionMenuItem[] => [
    ...(item.category === 'ordner'
      ? [{ key: 'oeffnen', label: t('mss.vault.dateien.oeffnen'), icon: <FolderOpen className="h-4 w-4" />, onSelect: () => setOrdner(item.id) }]
      : []),
    ...(item.datei
      ? [
          { key: 'speichern', label: t('mss.vault.dateien.speichern'), icon: <Download className="h-4 w-4" />, onSelect: () => void aufGeraet(item) },
          {
            key: 'offline',
            label: t(offline.has(item.datei.original.id) ? 'mss.vault.dateien.nurOnline' : 'mss.vault.dateien.offlineMachen'),
            icon: <HardDriveDownload className="h-4 w-4" />,
            disabled: holt[item.id] !== undefined,
            onSelect: () => void offlineUmschalten(item),
          },
        ]
      : []),
    { key: 'umbenennen', label: t('mss.vault.dateien.umbenennen'), icon: <Pencil className="h-4 w-4" />, separatorBefore: true, onSelect: () => void umbenennen(item) },
    {
      key: 'verschieben',
      label: t('mss.vault.dateien.verschieben'),
      icon: <FolderInput className="h-4 w-4" />,
      onSelect: () => setVerschiebenDialog({ item, ziel: item.ordner ?? '' }),
    },
    { key: 'archiv', label: t('mss.vault.archivieren'), icon: <Archive className="h-4 w-4" />, onSelect: () => void setArchived(item.id, true) },
    {
      key: 'papierkorb',
      label: t('mss.vault.inPapierkorb'),
      icon: <Trash2 className="h-4 w-4" />,
      destructive: true,
      separatorBefore: true,
      onSelect: () => void trashItem(item.id).then(() => toast.success(t('mss.vault.inPapierkorbGelegt'))),
    },
  ]

  // ── Darstellung ───────────────────────────────────────────────────────────

  const datum = new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium', timeStyle: 'short' })

  const speicherAnzeige = speicher && (
    <div className="flex items-start gap-2">
      <HardDrive className="mt-0.5 h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden />
      {ohneSpeicher ? (
        <p className="text-label-sm text-on-surface-variant">{t('mss.vault.dateien.keinSpeicher')}</p>
      ) : (
        <div className="min-w-0 flex-1">
          <ProgressBar
            value={(speicher.belegt / speicher.quote) * 100}
            heat
            ariaLabel={t('mss.vault.dateien.speicher')}
            hint={t('mss.vault.dateien.speicherBelegt', { belegt: formatBytes(speicher.belegt), quote: formatBytes(speicher.quote) })}
          />
        </div>
      )}
    </div>
  )

  const zeile = (item: VaultItem) => {
    const istOrdner = item.category === 'ordner'
    const Icon = istOrdner ? Folder : dateiIcon(item.datei?.typ ?? '')
    const upload = uploads[item.id]
    const istOffline = !!item.datei && offline.has(item.datei.original.id)
    const groesse = item.datei ? formatBytes(item.datei.original.echt) : null
    const geaendert = datum.format(item.datei?.geaendert ?? item.updatedAt)
    const fortschritt = holt[item.id] !== undefined ? (
      <ProgressBar value={holt[item.id] * 100} ariaLabel={t('mss.vault.dateien.offlineLaedt')} />
    ) : upload ? (
      upload.fehler === 'speicherVoll' ? (
        <span className="text-label-sm text-status-destructive">{t('mss.vault.dateien.speicherVoll')}</span>
      ) : (
        <ProgressBar value={upload.gesamt > 0 ? (upload.gesendet / upload.gesamt) * 100 : null} ariaLabel={t('mss.vault.dateien.wirdHochgeladen')} />
      )
    ) : null
    return (
      <li
        key={item.id}
        {...zeileZiehen(item)}
        {...(istOrdner ? ordnerAblage(item) : {})}
        onContextMenu={(event) => {
          event.preventDefault()
          setMenue({ item, x: event.clientX, y: event.clientY, ausloeser: event.currentTarget })
        }}
        className={cx(
          'group flex items-center gap-2 border-b border-outline-variant/40 px-2 py-1.5 last:border-b-0',
          SPALTEN,
          zielZeile === item.id ? 'bg-primary/10 ring-1 ring-inset ring-primary/50' : 'hover:bg-surface-container-high/70',
          gezogen === item.id && 'opacity-50',
        )}
      >
        <button
          type="button"
          className="flex min-h-10 min-w-0 flex-1 items-center gap-3 rounded-md px-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
          onClick={() => oeffnen(item)}
          disabled={!istOrdner && !item.datei}
        >
          <Icon className={cx('h-5 w-5 shrink-0', istOrdner ? 'text-primary' : 'text-on-surface-variant')} aria-hidden />
          <span className="min-w-0 flex-1">
            <span className="flex min-w-0 items-center gap-1.5">
              <span className="truncate text-sm text-on-surface">{item.service}</span>
              {istOffline && (
                <HardDriveDownload className="h-3.5 w-3.5 shrink-0 text-secondary" role="img" aria-label={t('mss.vault.dateien.offlineVerfuegbar')} />
              )}
            </span>
            {/* Auf schmalen Bildschirmen stehen Größe und Datum unter dem Namen. */}
            <span className="block truncate text-label-sm text-on-surface-variant lg:hidden">
              {istOrdner ? t('mss.vault.dateien.ordner') : item.datei ? `${groesse}, ${geaendert}` : t('mss.vault.dateien.unlesbar')}
            </span>
            {fortschritt && <span className="mt-1 block max-w-56">{fortschritt}</span>}
          </span>
        </button>
        <span className="hidden text-right font-mono text-label-sm text-on-surface-variant lg:block">
          {istOrdner ? '' : groesse ?? t('mss.vault.dateien.unlesbar')}
        </span>
        <span className="hidden truncate text-label-sm text-on-surface-variant lg:block">{geaendert}</span>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-9 w-9 shrink-0 opacity-70 group-hover:opacity-100"
          aria-label={t('mss.vault.dateien.aktionenFuer', { name: item.service })}
          aria-haspopup="menu"
          onClick={(event) => {
            const box = event.currentTarget.getBoundingClientRect()
            setMenue({ item, x: box.right, y: box.bottom + 4, ausloeser: event.currentTarget })
          }}
        >
          <MoreHorizontal className="h-4 w-4" />
        </Button>
      </li>
    )
  }

  return (
    <div className="flex min-h-0 flex-1">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-outline-variant/30 md:flex">
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          <TresorOrdnerBaum ordner={ordnerListe} aktuell={aktuellerOrdner} pfad={pfad} onWaehlen={setOrdner} ablage={{ kannAblegen, onAblegen: ablegen }} />
        </div>
        {speicherAnzeige && <div className="border-t border-outline-variant/30 p-3">{speicherAnzeige}</div>}
      </aside>

      <section className="relative flex min-w-0 flex-1 flex-col" {...flaecheAblage}>
        <header className="flex flex-wrap items-center gap-2 border-b border-outline-variant/30 px-3 py-2 sm:px-4">
          <Pfadleiste
            label={t('mss.vault.dateien.pfad')}
            // Auf dem Handy steht der Pfad in einer eigenen Zeile über den Knöpfen.
            className="basis-full text-xs [scrollbar-width:none] sm:basis-0"
            stamm={{ key: '', label: t('mss.vault.dateien.stamm'), icon: <HardDrive className="h-3.5 w-3.5" aria-hidden /> }}
            teile={pfad.map((o) => ({ key: o.id, label: o.service }))}
            onWaehlen={(key) => setOrdner(key || undefined)}
            hochLabel={t('mss.vault.dateien.hoch')}
            kannAblegen={(key, daten) => kannAblegen(key || undefined, daten)}
            onAblegen={(key, daten) => ablegen(key || undefined, daten)}
          />
          <div className="ml-auto flex items-center gap-1.5">
            <Button type="button" variant="ghost" size="sm" onClick={() => void neuerOrdner()}>
              <FolderPlus className="mr-1.5 h-4 w-4" />
              {t('mss.vault.dateien.ordnerAnlegen')}
            </Button>
            <FileButton multiple size="sm" variant="primary" disabled={ohneSpeicher} onFiles={(dateien) => void hochladen(dateien, aktuellerOrdner)}>
              <Upload className="mr-1.5 h-4 w-4" />
              {t('mss.vault.dateien.hochladen')}
            </FileButton>
          </div>
        </header>

        {speicherAnzeige && <div className="border-b border-outline-variant/30 px-3 py-2 md:hidden">{speicherAnzeige}</div>}

        {vorbereitung > 0 && (
          <p className="border-b border-outline-variant/30 px-4 py-1.5 text-label-sm text-on-surface-variant" role="status">
            {t('mss.vault.dateien.verschluesselt', { count: vorbereitung })}
          </p>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2 sm:px-3">
          {inhalt.length === 0 ? (
            <div className="flex h-full min-h-64 flex-col items-center justify-center gap-3 px-6 text-center">
              <FolderOpen className="h-10 w-10 text-on-surface-variant/60" aria-hidden />
              <div>
                <p className="text-sm font-semibold text-on-surface">{t('mss.vault.dateien.leerTitel')}</p>
                <p className="mt-1 max-w-sm text-xs text-on-surface-variant">
                  {ohneSpeicher ? t('mss.vault.dateien.keinSpeicher') : t('mss.vault.dateien.leer')}
                </p>
              </div>
              {!ohneSpeicher && (
                <FileButton multiple size="sm" variant="secondary" onFiles={(dateien) => void hochladen(dateien, aktuellerOrdner)}>
                  <Upload className="mr-1.5 h-4 w-4" />
                  {t('mss.vault.dateien.hochladen')}
                </FileButton>
              )}
            </div>
          ) : (
            <>
              <div className={cx('hidden px-2 pb-1.5 text-label-sm text-on-surface-variant', SPALTEN)} aria-hidden>
                <span className="pl-9">{t('mss.vault.dateien.spalteName')}</span>
                <span className="text-right">{t('mss.vault.dateien.spalteGroesse')}</span>
                <span>{t('mss.vault.dateien.geaendert')}</span>
                <span />
              </div>
              <ul aria-label={t('mss.vault.dateien.inhalt')} className="overflow-hidden rounded-xl border border-outline-variant/40 bg-surface-container-low/60">
                {inhalt.map(zeile)}
              </ul>
            </>
          )}
        </div>

        {rechnerDateiDarueber && !ohneSpeicher && (
          <div className="pointer-events-none absolute inset-2 flex items-end justify-center rounded-2xl border-2 border-dashed border-primary/60 bg-primary/5 pb-8">
            <p className="flex items-center gap-2 rounded-full bg-surface-container-highest px-4 py-2 text-sm text-on-surface shadow-panel">
              <Upload className="h-4 w-4 text-primary" aria-hidden />
              {t('mss.vault.dateien.ablegenHochladen', { ordner: ordnerName(zielZeile ?? aktuellerOrdner) })}
            </p>
          </div>
        )}
      </section>

      <Kontextmenue
        ort={menue ? { x: menue.x, y: menue.y } : null}
        items={menue ? menueEintraege(menue.item) : []}
        label={t('mss.vault.dateien.aktionen')}
        ausloeser={menue?.ausloeser}
        onSchliessen={() => setMenue(null)}
      />

      <Dialog open={!!verschiebenDialog} onOpenChange={(offen) => !offen && setVerschiebenDialog(null)}>
        {verschiebenDialog && (
          <DialogContent>
            <DialogHeader>
              <DialogTitle className="truncate">{t('mss.vault.dateien.verschiebenTitel', { name: verschiebenDialog.item.service })}</DialogTitle>
            </DialogHeader>
            <div className="p-6">
              <Dropdown
                value={verschiebenDialog.ziel}
                onChange={(ziel) => setVerschiebenDialog((v) => (v ? { ...v, ziel } : v))}
                options={[{ value: '', label: t('mss.vault.dateien.obersteEbene') }, ...zielOrdner(verschiebenDialog.item, ordnerListe)]}
                searchable
                aria-label={t('mss.vault.dateien.zielOrdner')}
              />
            </div>
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={() => setVerschiebenDialog(null)}>
                {t('common.cancel')}
              </Button>
              <Button
                type="button"
                disabled={!darfVerschieben(verschiebenDialog.item, verschiebenDialog.ziel || undefined, ordnerListe)}
                onClick={() => {
                  const { item, ziel } = verschiebenDialog
                  setVerschiebenDialog(null)
                  void verschieben(item, ziel || undefined)
                }}
              >
                {t('mss.vault.dateien.verschieben')}
              </Button>
            </DialogFooter>
          </DialogContent>
        )}
      </Dialog>

      {ansicht && (
        <TresorDateiAnsicht
          item={ansicht}
          ort={ort}
          folge={inhalt.filter((i) => i.datei && !oeffnetImEditor(i))}
          onWechseln={setAnsicht}
          onSchliessen={() => setAnsicht(null)}
        />
      )}
    </div>
  )
}
