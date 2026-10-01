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
 * Knopf am Ende jeder Zeile öffnen dasselbe Menü.
 *
 * Mehrfachauswahl: am Rechner mit Strg/Cmd- und Umschalt-Klick oder den
 * Kästchen, am Telefon mit langem Drücken. Solange etwas gewählt ist, schaltet
 * ein Klick um, statt zu öffnen; gezogen werden alle gewählten zusammen. Die
 * Auswahl gilt für den geöffneten Ordner und leert sich beim Wechsel.
 */

import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Archive,
  CheckSquare,
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
  Auswahlleiste,
  Button,
  Checkbox,
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
  type AuswahlAktion,
} from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { prompt } from '@/stores/promptStore'
import { formatBytes } from '@/components/server/fileHelpers'
import { useLangdruck } from '@/hooks/useLangdruck'
import { useZurueckSchliesst } from '@/hooks/useZurueckSchliesst'
import { ZipZuGross } from '@/lib/zipSchreiben'
import { cx } from '@/utils/classNames'
import { useVaultStore, type VaultItem } from './vaultStore'
import { angeheftet, offlineAnheften, offlineLoesen, useTresorUploads, type UploadFortschritt } from './tresorDateien'
import { speicherAbfragen, type TresorSpeicher } from './tresorBlobApi'
import { aufGeraetSpeichern, mehrereAufGeraetSpeichern } from './tresorAnzeige'
import { dateienUnter, darfAlleVerschieben, darfVerschieben, obersteAuswahl, pfadVon, zielOrdner } from './tresorOrdner'
import { TresorOrdnerBaum } from './TresorOrdnerBaum'
import { TresorDateiAnsicht, dateiIcon, oeffnetImEditor } from './TresorDateiAnsicht'

/** Kennzeichnet beim Ziehen Einträge aus dem Tresor (im Unterschied zu Dateien vom Rechner). Wert: JSON-Liste der IDs. */
export const ZIEH_TYP = 'application/x-msm-tresor'

/** Spalten der Liste ab `lg`: Auswahl, Name, Größe, Geändert, Menü. Darunter stehen Größe und Datum unter dem Namen. */
const SPALTEN = 'lg:grid lg:grid-cols-[1.25rem_minmax(0,1fr)_6.5rem_10rem_2.5rem] lg:items-center lg:gap-3'

/** So lange nach einem langen Druck gilt der folgende Klick als dessen Ende, nicht als neuer Tipp. */
const KLICK_NACH_LANGDRUCK_MS = 700

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
  const [verschiebenDialog, setVerschiebenDialog] = useState<{ items: VaultItem[]; ziel: string } | null>(null)
  const [offline, setOffline] = useState<Set<string>>(new Set())
  const [holt, setHolt] = useState<Record<string, number>>({})
  const [menue, setMenue] = useState<{ item: VaultItem; x: number; y: number; ausloeser: HTMLElement } | null>(null)
  /** Gewählte Einträge; `null` heißt: keine Auswahl. */
  const [auswahl, setAuswahl] = useState<Set<string> | null>(null)
  // Am Handy beendet Zurück die Auswahl, nicht die App.
  useZurueckSchliesst(auswahl !== null, () => {
    setAuswahl(null)
    setAnker(null)
  })
  /** Ausgangspunkt für Umschalt-Klick. */
  const [anker, setAnker] = useState<string | null>(null)
  /** Eine laufende Sammelaktion (Offline, Zip), mit Fortschritt. */
  const [sammel, setSammel] = useState<{ text: string; anteil: number | null } | null>(null)
  /** Ob alle Dateien der Auswahl bzw. des Menü-Ordners schon offline liegen. */
  const [alleAngeheftet, setAlleAngeheftet] = useState(false)
  /** Einträge, die gerade gezogen werden; nur innerhalb dieses Fensters bekannt. */
  const [gezogen, setGezogen] = useState<string[]>([])
  /** Ordnerzeile, über der gerade etwas schwebt. */
  const [zielZeile, setZielZeile] = useState<string | null>(null)
  // `dragenter`/`dragleave` feuern auch beim Wechsel zwischen Kindelementen;
  // erst wenn der Zähler auf null fällt, hat die Datei die Fläche verlassen.
  const ziehZaehler = useRef(0)
  const [rechnerDateiDarueber, setRechnerDateiDarueber] = useState(false)
  const letzterLangdruck = useRef(0)

  const sichtbar = (i: VaultItem) => !i.trashedAt && !i.archivedAt
  const sichtbareItems = useMemo(() => items.filter(sichtbar), [items])
  const ordnerListe = useMemo(() => sichtbareItems.filter((i) => i.category === 'ordner'), [sichtbareItems])

  // Liegt der geöffnete Ordner nicht mehr da (gelöscht, in den Papierkorb), zurück nach oben.
  const aktuellerOrdner = ordner && ordnerListe.some((o) => o.id === ordner) ? ordner : undefined
  const pfad = useMemo(() => pfadVon(aktuellerOrdner, ordnerListe), [aktuellerOrdner, ordnerListe])
  const ort = [t('mss.vault.dateien.stamm'), ...pfad.map((o) => o.service)].join(' / ')
  const ordnerName = (id: string | undefined) => (id ? ordnerListe.find((o) => o.id === id)?.service : undefined) ?? t('mss.vault.dateien.stamm')

  const inhalt = useMemo(() => {
    const hier = sichtbareItems.filter((i) => (i.category === 'ordner' || i.category === 'datei') && i.ordner === aktuellerOrdner)
    const nachName = (a: VaultItem, b: VaultItem) => a.service.localeCompare(b.service)
    return [...hier.filter((i) => i.category === 'ordner').sort(nachName), ...hier.filter((i) => i.category === 'datei').sort(nachName)]
  }, [sichtbareItems, aktuellerOrdner])

  // Die Auswahl gilt für diesen Ordner. Was verschwindet (verschoben, gelöscht), fällt heraus.
  useEffect(() => {
    setAuswahl(null)
    setAnker(null)
  }, [aktuellerOrdner])
  const gewaehlt = useMemo(() => (auswahl ? obersteAuswahl(auswahl, inhalt) : []), [auswahl, inhalt])
  const gewaehltIds = gewaehlt.map((i) => i.id).join(',')

  // Welche Originale dieser Ebene angeheftet sind (nur dieses Gerät).
  const originale = inhalt.flatMap((i) => (i.datei ? [i.datei.original.id] : [])).join(',')
  const offlineLaden = () => {
    void angeheftet(originale ? originale.split(',') : []).then(setOffline).catch(() => setOffline(new Set()))
  }
  useEffect(offlineLaden, [originale])

  // Für die Beschriftung „Offline verfügbar machen“ bzw. „Nur online behalten“.
  const menueOrdner = menue?.item.category === 'ordner' ? menue.item.id : ''
  useEffect(() => {
    const liste = menueOrdner ? sichtbareItems.filter((i) => i.id === menueOrdner) : gewaehlt
    const ids = dateienUnter(liste, sichtbareItems).map((d) => d.item.datei!.original.id)
    if (ids.length === 0) return setAlleAngeheftet(false)
    void angeheftet(ids)
      .then((da) => setAlleAngeheftet(ids.every((id) => da.has(id))))
      .catch(() => setAlleAngeheftet(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gewaehltIds, menueOrdner, offline])

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

  // ── Auswahl ───────────────────────────────────────────────────────────────

  const auswahlLeeren = () => {
    setAuswahl(null)
    setAnker(null)
  }

  const umschalten = (item: VaultItem) => {
    setAuswahl((a) => {
      const neu = new Set(a ?? [])
      if (neu.has(item.id)) neu.delete(item.id)
      else neu.add(item.id)
      return neu.size > 0 ? neu : null
    })
    setAnker(item.id)
  }

  /** Umschalt-Klick: alles zwischen Anker und `item` in der Reihenfolge der Liste. */
  const bereich = (item: VaultItem) => {
    const von = inhalt.findIndex((i) => i.id === anker)
    const bis = inhalt.findIndex((i) => i.id === item.id)
    if (von < 0 || bis < 0) return umschalten(item)
    const [a, b] = von < bis ? [von, bis] : [bis, von]
    setAuswahl(new Set(inhalt.slice(a, b + 1).map((i) => i.id)))
  }

  const zeileKlick = (item: VaultItem, event: React.MouseEvent) => {
    if (Date.now() - letzterLangdruck.current < KLICK_NACH_LANGDRUCK_MS) return
    if (event.shiftKey && anker) bereich(item)
    else if (event.ctrlKey || event.metaKey || auswahl) umschalten(item)
    else oeffnen(item)
  }

  const langdruck = (item: VaultItem) => {
    letzterLangdruck.current = Date.now()
    setAuswahl((a) => new Set([...(a ?? []), item.id]))
    setAnker(item.id)
  }

  // ── Einzelaktionen ────────────────────────────────────────────────────────

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

  // ── Sammelaktionen (eine oder viele) ──────────────────────────────────────

  /** Verschiebt, was nach `ziel` darf; was schon dort liegt, bleibt liegen. */
  const verschieben = async (liste: VaultItem[], ziel: string | undefined) => {
    const bewegt = liste.filter((i) => darfVerschieben(i, ziel, ordnerListe))
    if (bewegt.length === 0) return
    try {
      for (const item of bewegt) await saveItem({ ...item, ordner: ziel })
      toast.success(
        bewegt.length === 1
          ? t('mss.vault.dateien.verschoben', { name: bewegt[0].service, ordner: ordnerName(ziel) })
          : t('mss.vault.dateien.verschobenMehrere', { count: bewegt.length, ordner: ordnerName(ziel) }),
      )
      auswahlLeeren()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  /** Heftet alle Dateien in und unter `liste` an, oder löst sie, wenn schon alle angeheftet sind. */
  const offlineSammel = async (liste: VaultItem[]) => {
    const dateien = dateienUnter(liste, sichtbareItems)
    if (dateien.length === 0) return
    const da = await angeheftet(dateien.map((d) => d.item.datei!.original.id))
    if (dateien.every((d) => da.has(d.item.datei!.original.id))) {
      for (const d of dateien) await offlineLoesen(d.item.datei!.original.id)
      toast.success(t('mss.vault.dateien.nurOnlineMehrere', { count: dateien.length }))
    } else {
      const fehlen = dateien.filter((d) => !da.has(d.item.datei!.original.id))
      const gesamt = fehlen.reduce((s, d) => s + d.item.datei!.original.echt, 0) || 1
      let erledigt = 0
      let fehler = 0
      const text = t('mss.vault.dateien.offlineSammelLaedt', { count: fehlen.length })
      setSammel({ text, anteil: 0 })
      for (const d of fehlen) {
        const groesse = d.item.datei!.original.echt
        try {
          await offlineAnheften(d.item.datei!.original, (anteil) => setSammel({ text, anteil: (erledigt + anteil * groesse) / gesamt }))
        } catch {
          fehler += 1
        }
        erledigt += groesse
      }
      setSammel(null)
      if (fehler === 0) toast.success(t('mss.vault.dateien.offlineFertigMehrere', { count: fehlen.length }))
      else toast.error(t('mss.vault.dateien.offlineTeilweise', { count: fehler, gesamt: fehlen.length }))
    }
    offlineLaden()
    auswahlLeeren()
  }

  /** Eine Datei wie bisher, sonst ein Zip mit Ordnern. */
  const speichernSammel = async (liste: VaultItem[]) => {
    if (!userKey) return
    if (liste.length === 1 && liste[0].category === 'datei') return aufGeraet(liste[0])
    const dateien = dateienUnter(liste, sichtbareItems)
    if (dateien.length === 0) return toast.error(t('mss.vault.dateien.keineDateien'))
    const name = `${liste.length === 1 ? liste[0].service : aktuellerOrdner ? ordnerName(aktuellerOrdner) : t('mss.vault.dateien.zipStamm')}.zip`
    setSammel({ text: t('mss.vault.dateien.zipLaeuft', { count: dateien.length }), anteil: null })
    try {
      await mehrereAufGeraetSpeichern(
        dateien.map((d) => ({ kopf: d.item.datei!.original, eintragId: d.item.id, pfad: d.pfad, geaendert: d.item.datei!.geaendert ?? d.item.updatedAt })),
        userKey,
        name,
      )
      auswahlLeeren()
    } catch (err) {
      toast.error(
        err instanceof ZipZuGross
          ? t(err.grund === 'dateien' ? 'mss.vault.dateien.zipZuVieleDateien' : 'mss.vault.dateien.zipZuGross')
          : t('mss.vault.dateien.speichernFehler'),
      )
    } finally {
      setSammel(null)
    }
  }

  const archivieren = async (liste: VaultItem[]) => {
    for (const item of liste) await setArchived(item.id, true)
    if (liste.length > 1) toast.success(t('mss.vault.dateien.archiviertMehrere', { count: liste.length }))
    auswahlLeeren()
  }

  const inPapierkorb = async (liste: VaultItem[]) => {
    for (const item of liste) await trashItem(item.id)
    toast.success(liste.length === 1 ? t('mss.vault.inPapierkorbGelegt') : t('mss.vault.dateien.papierkorbMehrere', { count: liste.length }))
    auswahlLeeren()
  }

  const sammelAktionen = (liste: VaultItem[]): AuswahlAktion[] => {
    const leer = liste.length === 0
    const beschaeftigt = sammel !== null
    return [
      {
        key: 'verschieben',
        label: t('mss.vault.dateien.verschieben'),
        icon: <FolderInput className="h-5 w-5 md:h-4 md:w-4" />,
        disabled: leer,
        onSelect: () => setVerschiebenDialog({ items: liste, ziel: aktuellerOrdner ?? '' }),
      },
      {
        key: 'offline',
        label: t(alleAngeheftet ? 'mss.vault.dateien.nurOnline' : 'mss.vault.dateien.offlineMachen'),
        kurz: t(alleAngeheftet ? 'mss.vault.dateien.kurz.nurOnline' : 'mss.vault.dateien.kurz.offline'),
        icon: <HardDriveDownload className="h-5 w-5 md:h-4 md:w-4" />,
        disabled: leer || beschaeftigt,
        onSelect: () => void offlineSammel(liste),
      },
      {
        key: 'speichern',
        label: t('mss.vault.dateien.speichern'),
        kurz: t('mss.vault.dateien.kurz.speichern'),
        icon: <Download className="h-5 w-5 md:h-4 md:w-4" />,
        disabled: leer || beschaeftigt,
        onSelect: () => void speichernSammel(liste),
      },
      {
        key: 'archiv',
        label: t('mss.vault.archivieren'),
        icon: <Archive className="h-5 w-5 md:h-4 md:w-4" />,
        disabled: leer,
        onSelect: () => void archivieren(liste),
      },
      {
        key: 'papierkorb',
        label: t('mss.vault.inPapierkorb'),
        kurz: t('mss.vault.dateien.kurz.papierkorb'),
        icon: <Trash2 className="h-5 w-5 md:h-4 md:w-4" />,
        destructive: true,
        disabled: leer,
        onSelect: () => void inPapierkorb(liste),
      },
    ]
  }

  // ── Ziehen und Ablegen ────────────────────────────────────────────────────

  const gezogeneItems = (ids: string[]) => ids.flatMap((id) => sichtbareItems.filter((i) => i.id === id))

  /** Ob über dem Ordner `ziel` (`undefined` = Stamm) abgelegt werden darf. */
  const kannAblegen = (ziel: string | undefined, daten: DataTransfer) => {
    if (daten.types.includes(ZIEH_TYP)) return gezogen.length > 0 && darfAlleVerschieben(gezogeneItems(gezogen), ziel, ordnerListe)
    return daten.types.includes('Files') && !ohneSpeicher
  }

  const ablegen = (ziel: string | undefined, daten: DataTransfer) => {
    ziehZaehler.current = 0
    setRechnerDateiDarueber(false)
    setZielZeile(null)
    const roh = daten.getData(ZIEH_TYP)
    if (roh) {
      let ids: unknown
      try {
        ids = JSON.parse(roh)
      } catch {
        return
      }
      if (Array.isArray(ids)) void verschieben(gezogeneItems(ids.filter((id): id is string => typeof id === 'string')), ziel)
      return
    }
    void hochladen(Array.from(daten.files), ziel)
  }

  const zeileZiehen = (item: VaultItem) => ({
    draggable: true,
    onDragStart: (event: React.DragEvent) => {
      // Ein gewählter Eintrag nimmt die ganze Auswahl mit, ein anderer nur sich selbst.
      const ids = auswahl?.has(item.id) ? gewaehlt.map((i) => i.id) : [item.id]
      event.dataTransfer.setData(ZIEH_TYP, JSON.stringify(ids))
      event.dataTransfer.effectAllowed = 'move'
      if (ids.length > 1 && event.dataTransfer.setDragImage) {
        const bild = document.createElement('div')
        bild.textContent = t('mss.vault.dateien.eintraege', { count: ids.length })
        bild.className = 'fixed -left-[999px] top-0 rounded-full bg-primary px-3 py-1 text-sm font-semibold text-on-primary'
        document.body.appendChild(bild)
        event.dataTransfer.setDragImage(bild, 12, 12)
        setTimeout(() => bild.remove(), 0)
      }
      setGezogen(ids)
    },
    onDragEnd: () => {
      setGezogen([])
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

  const menueEintraege = (item: VaultItem): ActionMenuItem[] => {
    // Rechtsklick in eine Auswahl aus mehreren: das Menü gilt für alle.
    if (auswahl?.has(item.id) && gewaehlt.length > 1) {
      return sammelAktionen(gewaehlt).map((a) => ({ ...a, separatorBefore: a.key === 'papierkorb' }))
    }
    return [
      ...(item.category === 'ordner'
        ? [
            { key: 'oeffnen', label: t('mss.vault.dateien.oeffnen'), icon: <FolderOpen className="h-4 w-4" />, onSelect: () => setOrdner(item.id) },
            {
              key: 'offline',
              label: t(alleAngeheftet ? 'mss.vault.dateien.nurOnline' : 'mss.vault.dateien.offlineMachen'),
              icon: <HardDriveDownload className="h-4 w-4" />,
              disabled: sammel !== null,
              onSelect: () => void offlineSammel([item]),
            },
            { key: 'speichern', label: t('mss.vault.dateien.alsZip'), icon: <Download className="h-4 w-4" />, disabled: sammel !== null, onSelect: () => void speichernSammel([item]) },
          ]
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
        onSelect: () => setVerschiebenDialog({ items: [item], ziel: item.ordner ?? '' }),
      },
      { key: 'auswaehlen', label: t('mss.vault.dateien.auswaehlen'), icon: <CheckSquare className="h-4 w-4" />, onSelect: () => langdruck(item) },
      { key: 'archiv', label: t('mss.vault.archivieren'), icon: <Archive className="h-4 w-4" />, onSelect: () => void archivieren([item]) },
      {
        key: 'papierkorb',
        label: t('mss.vault.inPapierkorb'),
        icon: <Trash2 className="h-4 w-4" />,
        destructive: true,
        separatorBefore: true,
        onSelect: () => void inPapierkorb([item]),
      },
    ]
  }

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

  const listenTaste = (event: React.KeyboardEvent) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
      event.preventDefault()
      setAuswahl(new Set(inhalt.map((i) => i.id)))
    } else if (event.key === 'Escape' && auswahl) {
      event.preventDefault()
      auswahlLeeren()
    } else if (event.key === 'Delete' && gewaehlt.length > 0) {
      event.preventDefault()
      void inPapierkorb(gewaehlt)
    }
  }

  const auswahlLabel = t('mss.vault.dateien.ausgewaehlt', { count: gewaehlt.length })

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
          {!auswahl && (
            <div className="ml-auto flex items-center gap-1.5">
              {inhalt.length > 0 && (
                <Button type="button" variant="ghost" size="sm" className="md:hidden" aria-label={t('mss.vault.dateien.auswaehlen')} onClick={() => setAuswahl(new Set())}>
                  <CheckSquare className="h-4 w-4" />
                </Button>
              )}
              <Button type="button" variant="ghost" size="sm" onClick={() => void neuerOrdner()}>
                <FolderPlus className="mr-1.5 h-4 w-4" />
                {t('mss.vault.dateien.ordnerAnlegen')}
              </Button>
              <FileButton multiple size="sm" variant="primary" disabled={ohneSpeicher} onFiles={(dateien) => void hochladen(dateien, aktuellerOrdner)}>
                <Upload className="mr-1.5 h-4 w-4" />
                {t('mss.vault.dateien.hochladen')}
              </FileButton>
            </div>
          )}
        </header>

        {auswahl && (
          <div className="flex items-center border-b border-outline-variant/30 bg-primary/5 px-3 py-1.5 sm:px-4">
            <Auswahlleiste
              variante="kopf"
              anzahlLabel={auswahlLabel}
              aktionen={sammelAktionen(gewaehlt)}
              abbrechenLabel={t('mss.vault.dateien.auswahlBeenden')}
              onAbbrechen={auswahlLeeren}
              alleLabel={gewaehlt.length < inhalt.length ? t('mss.vault.dateien.alleAuswaehlen') : undefined}
              onAlle={() => setAuswahl(new Set(inhalt.map((i) => i.id)))}
            />
          </div>
        )}

        {speicherAnzeige && !auswahl && <div className="border-b border-outline-variant/30 px-3 py-2 md:hidden">{speicherAnzeige}</div>}

        {vorbereitung > 0 && (
          <p className="border-b border-outline-variant/30 px-4 py-1.5 text-label-sm text-on-surface-variant" role="status">
            {t('mss.vault.dateien.verschluesselt', { count: vorbereitung })}
          </p>
        )}
        {sammel && (
          <div className="border-b border-outline-variant/30 px-4 py-2" role="status">
            <ProgressBar value={sammel.anteil === null ? null : sammel.anteil * 100} label={sammel.text} />
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2 sm:px-3" onKeyDown={listenTaste}>
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
                <span />
                <span className="pl-9">{t('mss.vault.dateien.spalteName')}</span>
                <span className="text-right">{t('mss.vault.dateien.spalteGroesse')}</span>
                <span>{t('mss.vault.dateien.geaendert')}</span>
                <span />
              </div>
              <ul
                aria-label={t('mss.vault.dateien.inhalt')}
                className="overflow-hidden rounded-xl border border-outline-variant/40 bg-surface-container-low/60"
              >
                {inhalt.map((item) => (
                  <TresorDateiZeile
                    key={item.id}
                    item={item}
                    ausgewaehlt={!!auswahl?.has(item.id)}
                    auswahlAktiv={auswahl !== null}
                    offline={!!item.datei && offline.has(item.datei.original.id)}
                    holt={holt[item.id]}
                    upload={uploads[item.id]}
                    ziel={zielZeile === item.id}
                    gezogen={gezogen.includes(item.id)}
                    geaendert={datum.format(item.datei?.geaendert ?? item.updatedAt)}
                    ziehen={zeileZiehen(item)}
                    ablage={item.category === 'ordner' ? ordnerAblage(item) : undefined}
                    onKlick={(event) => zeileKlick(item, event)}
                    onLangdruck={() => langdruck(item)}
                    onKaestchen={(schieben) => (schieben && anker ? bereich(item) : umschalten(item))}
                    onMenue={(x, y, ausloeser) => setMenue({ item, x, y, ausloeser })}
                  />
                ))}
              </ul>
            </>
          )}
        </div>

        {auswahl && (
          <Auswahlleiste
            variante="fuss"
            anzahlLabel={auswahlLabel}
            aktionen={sammelAktionen(gewaehlt)}
            abbrechenLabel={t('mss.vault.dateien.auswahlBeenden')}
            onAbbrechen={auswahlLeeren}
          />
        )}

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
              <DialogTitle className="truncate">
                {verschiebenDialog.items.length === 1
                  ? t('mss.vault.dateien.verschiebenTitel', { name: verschiebenDialog.items[0].service })
                  : t('mss.vault.dateien.verschiebenTitelMehrere', { count: verschiebenDialog.items.length })}
              </DialogTitle>
            </DialogHeader>
            <div className="p-6">
              <Dropdown
                value={verschiebenDialog.ziel}
                onChange={(ziel) => setVerschiebenDialog((v) => (v ? { ...v, ziel } : v))}
                options={[{ value: '', label: t('mss.vault.dateien.obersteEbene') }, ...zielOrdner(verschiebenDialog.items, ordnerListe)]}
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
                disabled={!darfAlleVerschieben(verschiebenDialog.items, verschiebenDialog.ziel || undefined, ordnerListe)}
                onClick={() => {
                  const { items: liste, ziel } = verschiebenDialog
                  setVerschiebenDialog(null)
                  void verschieben(liste, ziel || undefined)
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

interface ZeileProps {
  item: VaultItem
  ausgewaehlt: boolean
  auswahlAktiv: boolean
  offline: boolean
  holt: number | undefined
  upload: UploadFortschritt | undefined
  /** Etwas schwebt über diesem Ordner. */
  ziel: boolean
  gezogen: boolean
  geaendert: string
  ziehen: React.HTMLAttributes<HTMLLIElement> & { draggable: boolean }
  ablage: React.HTMLAttributes<HTMLLIElement> | undefined
  onKlick: (event: React.MouseEvent) => void
  onLangdruck: () => void
  /** Kästchen umgeschaltet; `true`, wenn dabei Umschalt gedrückt war. */
  onKaestchen: (schieben: boolean) => void
  onMenue: (x: number, y: number, ausloeser: HTMLElement) => void
}

/** Eine Zeile der Liste. Eigene Komponente, weil das lange Drücken ein Hook ist. */
function TresorDateiZeile({
  item,
  ausgewaehlt,
  auswahlAktiv,
  offline,
  holt,
  upload,
  ziel,
  gezogen,
  geaendert,
  ziehen,
  ablage,
  onKlick,
  onLangdruck,
  onKaestchen,
  onMenue,
}: ZeileProps) {
  const { t } = useTranslation()
  const lang = useLangdruck(onLangdruck)
  const schieben = useRef(false)
  // Android startet beim langen Drücken ein HTML-Ziehen, das nie endet. Am
  // Finger gehört der lange Druck der Auswahl; verschoben wird über das Menü.
  const amFinger = useRef(false)
  const istOrdner = item.category === 'ordner'
  const Icon = istOrdner ? Folder : dateiIcon(item.datei?.typ ?? '', item.service)
  const groesse = item.datei ? formatBytes(item.datei.original.echt) : null
  const fortschritt =
    holt !== undefined ? (
      <ProgressBar value={holt * 100} ariaLabel={t('mss.vault.dateien.offlineLaedt')} />
    ) : upload ? (
      upload.fehler === 'speicherVoll' ? (
        <span className="text-label-sm text-status-destructive">{t('mss.vault.dateien.speicherVoll')}</span>
      ) : (
        <ProgressBar value={upload.gesamt > 0 ? (upload.gesendet / upload.gesamt) * 100 : null} ariaLabel={t('mss.vault.dateien.wirdHochgeladen')} />
      )
    ) : null
  return (
    <li
      {...ziehen}
      {...ablage}
      onDragStart={(event) => {
        if (amFinger.current) {
          event.preventDefault()
          return
        }
        ziehen.onDragStart?.(event)
      }}
      onPointerDown={(event) => {
        amFinger.current = event.pointerType === 'touch'
        lang.onPointerDown(event)
      }}
      onPointerMove={lang.onPointerMove}
      onPointerUp={lang.onPointerUp}
      onPointerCancel={lang.onPointerCancel}
      onContextMenu={(event) => {
        // Am Finger gehört der lange Druck der Auswahl, nicht dem Menü.
        lang.onContextMenu(event)
        if (event.defaultPrevented) return
        event.preventDefault()
        onMenue(event.clientX, event.clientY, event.currentTarget)
      }}
      className={cx(
        'group flex items-center gap-2 border-b border-outline-variant/40 px-2 py-1.5 last:border-b-0',
        SPALTEN,
        ziel ? 'bg-primary/10 ring-1 ring-inset ring-primary/50' : ausgewaehlt ? 'bg-primary/10' : 'hover:bg-surface-container-high/70',
        gezogen && 'opacity-50',
      )}
    >
      <span className={cx('flex shrink-0 items-center justify-center', auswahlAktiv ? 'w-6' : 'hidden lg:flex')}>
        <Checkbox
          checked={ausgewaehlt}
          aria-label={t('mss.vault.dateien.auswaehlenName', { name: item.service })}
          className={cx(!auswahlAktiv && 'opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100')}
          onClick={(event) => {
            schieben.current = event.shiftKey
            event.stopPropagation()
          }}
          onCheckedChange={() => onKaestchen(schieben.current)}
        />
      </span>
      <button
        type="button"
        className="flex min-h-10 min-w-0 flex-1 items-center gap-3 rounded-md px-1 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
        onClick={onKlick}
        disabled={!istOrdner && !item.datei}
      >
        <Icon className={cx('h-5 w-5 shrink-0', istOrdner ? 'text-primary' : 'text-on-surface-variant')} aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="truncate text-sm text-on-surface">{item.service}</span>
            {offline && <HardDriveDownload className="h-3.5 w-3.5 shrink-0 text-secondary" role="img" aria-label={t('mss.vault.dateien.offlineVerfuegbar')} />}
          </span>
          {/* Auf schmalen Bildschirmen stehen Größe und Datum unter dem Namen. */}
          <span className="block truncate text-label-sm text-on-surface-variant lg:hidden">
            {istOrdner ? t('mss.vault.dateien.ordner') : item.datei ? `${groesse}, ${geaendert}` : t('mss.vault.dateien.unlesbar')}
          </span>
          {fortschritt && <span className="mt-1 block max-w-56">{fortschritt}</span>}
        </span>
      </button>
      <span className="hidden text-right font-mono text-label-sm text-on-surface-variant lg:block">{istOrdner ? '' : groesse ?? t('mss.vault.dateien.unlesbar')}</span>
      <span className="hidden truncate text-label-sm text-on-surface-variant lg:block">{geaendert}</span>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className={cx('h-9 w-9 shrink-0 opacity-70 group-hover:opacity-100', auswahlAktiv && 'invisible')}
        aria-label={t('mss.vault.dateien.aktionenFuer', { name: item.service })}
        aria-haspopup="menu"
        onClick={(event) => {
          const box = event.currentTarget.getBoundingClientRect()
          onMenue(box.right, box.bottom + 4, event.currentTarget)
        }}
      >
        <MoreHorizontal className="h-4 w-4" />
      </Button>
    </li>
  )
}
