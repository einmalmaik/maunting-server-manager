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
 *
 * Mit `suche` zeigt die Liste statt des Ordners alle Ordner und Dateien, deren
 * Name sie enthält, aus dem ganzen Tresor (ohne Papierkorb und Archiv), jeweils
 * mit ihrem Ort. Gesucht wird nur auf dem Gerät, im entschlüsselten Bestand.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
  SearchX,
  Trash2,
  Upload,
} from 'lucide-react'
import {
  Auswahlleiste,
  Blatteintrag,
  Blattmenue,
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
  useMehrfachauswahl,
  type ActionMenuItem,
  type AuswahlAktion,
} from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { prompt } from '@/stores/promptStore'
import { formatBytes } from '@/components/server/fileHelpers'
import { useLangdruck } from '@/hooks/useLangdruck'
import { ZipZuGross } from '@/lib/zipSchreiben'
import { cx } from '@/utils/classNames'
import { useVaultStore } from './vaultStore'
import { type VaultItem } from './vaultEintrag'
import { angeheftet, offlineAnheften, offlineLoesen, useTresorUploads, type UploadFortschritt } from './tresorDateien'
import { dateiAufsGeraet, mehrereAufGeraetSpeichern } from './tresorAnzeige'
import { dateienUnter, darfAlleVerschieben, darfVerschieben, obersteAuswahl, pfadVon, sichtbareEintraege, zielOrdner } from './tresorOrdner'
import { TresorOrdnerBaum } from './TresorOrdnerBaum'
import { fehlerText } from './tresorFehler'
import { inDenPapierkorb, insArchiv, type SammelStand } from './tresorSammel'
import { TresorDateiAnsicht, dateiIcon, oeffnetImEditor } from './TresorDateiAnsicht'
import { TresorSpeicherAnzeige, TresorUploadStand, useTresorSpeicher } from './TresorSpeicher'

/** Kennzeichnet beim Ziehen Einträge aus dem Tresor (im Unterschied zu Dateien vom Rechner). Wert: JSON-Liste der IDs. */
export const ZIEH_TYP = 'application/x-msm-tresor'

/** Spalten der Liste ab `lg`: Auswahl, Name, Größe, Geändert, Menü. Darunter stehen Größe und Datum unter dem Namen. */
const SPALTEN = 'lg:grid lg:grid-cols-[1.25rem_minmax(0,1fr)_6.5rem_10rem_2.5rem] lg:items-center lg:gap-3'

/** Knöpfe in Kopf und Zeilen: am Telefon 44 px Tippfläche. `min-*` schlägt die Höhe aus `size`. */
const TIPPFLAECHE = 'min-h-11 min-w-11 sm:min-h-8 sm:min-w-8'

/** Unter `md` steht das Aktionsmenü einer Zeile als Blatt am unteren Rand, nicht als Flyout am Knopf. */
const amTelefon = () => typeof window !== 'undefined' && !!window.matchMedia?.('(max-width: 767.98px)').matches

interface Props {
  /** Suchbegriff aus der Kopfleiste des Tresors. Nicht leer: Treffer aus allen Ordnern statt des geöffneten. */
  suche?: string
}

export function TresorDateiBereich({ suche = '' }: Props) {
  const { t, i18n } = useTranslation()
  const items = useVaultStore((s) => s.items)
  const userKey = useVaultStore((s) => s.userKey)
  const dateiHinzufuegen = useVaultStore((s) => s.dateiHinzufuegen)
  const ordnerAnlegen = useVaultStore((s) => s.ordnerAnlegen)
  const aendern = useVaultStore((s) => s.aendern)
  const uploads = useTresorUploads((s) => s.je)
  const [ordner, setOrdner] = useState<string | undefined>(undefined)
  /**
   * Der Suchbegriff, bei dem jemand einen Ordner geöffnet hat. Die Suche steht
   * in der Kopfleiste des Tresors und lässt sich hier nicht leeren; solange der
   * Begriff gleich bleibt, zeigt die Liste den geöffneten Ordner.
   */
  const [verlassenBei, setVerlassenBei] = useState<string | null>(null)
  // Speicher gibt nur eine Rolle. Ohne sie bleibt der Upload zu, der Server weist ihn ohnehin ab.
  const { speicher, ohneSpeicher } = useTresorSpeicher()
  const [ansicht, setAnsicht] = useState<VaultItem | null>(null)
  const [vorbereitung, setVorbereitung] = useState(0)
  const [verschiebenDialog, setVerschiebenDialog] = useState<{ items: VaultItem[]; ziel: string } | null>(null)
  const [offline, setOffline] = useState<Set<string>>(new Set())
  const [holt, setHolt] = useState<Record<string, number>>({})
  /** Offenes Aktionsmenü einer Zeile; `blatt` heißt: am Telefon über den Knopf geöffnet. */
  const [menue, setMenue] = useState<{ item: VaultItem; x: number; y: number; ausloeser: HTMLElement; blatt: boolean } | null>(null)
  const menueSchliessen = useCallback(() => setMenue(null), [])
  /** Eine laufende Sammelaktion (Papierkorb, Archiv, Offline, Zip), mit Fortschritt. Solange sie läuft, sind diese Aktionen gesperrt. */
  const [sammel, setSammel] = useState<SammelStand>(null)
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

  const sichtbareItems = useMemo(() => sichtbareEintraege(items), [items])
  const ordnerListe = useMemo(() => sichtbareItems.filter((i) => i.category === 'ordner'), [sichtbareItems])

  // Liegt der geöffnete Ordner nicht mehr da (gelöscht, in den Papierkorb), zurück nach oben.
  const aktuellerOrdner = ordner && ordnerListe.some((o) => o.id === ordner) ? ordner : undefined
  const pfad = useMemo(() => pfadVon(aktuellerOrdner, ordnerListe), [aktuellerOrdner, ordnerListe])
  /** Ort eines Ordners als Text, etwa „Stammverzeichnis / Verträge“. */
  const ortVon = (id: string | undefined) => [t('mss.vault.dateien.stamm'), ...pfadVon(id, ordnerListe).map((o) => o.service)].join(' / ')
  const ordnerName = (id: string | undefined) => (id ? ordnerListe.find((o) => o.id === id)?.service : undefined) ?? t('mss.vault.dateien.stamm')

  const suchwort = suche.trim().toLocaleLowerCase()
  // Ist die Suche leer, gilt ein später wieder getippter gleicher Begriff als neue Suche.
  if (suchwort === '' && verlassenBei !== null) setVerlassenBei(null)
  const sucht = suchwort !== '' && suchwort !== verlassenBei
  /** Öffnet einen Ordner (`undefined` = Stamm), auch aus der Suche heraus. */
  const ordnerOeffnen = (id: string | undefined) => {
    setOrdner(id)
    setVerlassenBei(suchwort || null)
  }

  const inhalt = useMemo(() => {
    // Beim Suchen der ganze Tresor; `sichtbareItems` lässt Papierkorb und Archiv samt Inhalt schon weg.
    const hier = sichtbareItems.filter(
      (i) => (i.category === 'ordner' || i.category === 'datei') && (sucht ? i.service.toLocaleLowerCase().includes(suchwort) : i.ordner === aktuellerOrdner),
    )
    const nachName = (a: VaultItem, b: VaultItem) => a.service.localeCompare(b.service)
    return [...hier.filter((i) => i.category === 'ordner').sort(nachName), ...hier.filter((i) => i.category === 'datei').sort(nachName)]
  }, [sichtbareItems, aktuellerOrdner, sucht, suchwort])

  // Mehrfachauswahl in der Reihenfolge der Liste.
  const wahl = useMehrfachauswahl(inhalt.map((i) => i.id))
  const { auswahl, leeren: auswahlLeeren } = wahl

  // Die Auswahl gilt für diesen Ordner bzw. diese Suche. Was verschwindet (verschoben, gelöscht), fällt heraus.
  useEffect(auswahlLeeren, [aktuellerOrdner, sucht, suchwort, auswahlLeeren])
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

  const anzahlUploads = Object.keys(uploads).length

  // ── Auswahl ───────────────────────────────────────────────────────────────

  const zeileKlick = (item: VaultItem, event: React.MouseEvent) => {
    if (!wahl.klick(item.id, event)) oeffnen(item)
  }

  // ── Einzelaktionen ────────────────────────────────────────────────────────

  const aufGeraet = (item: VaultItem) => dateiAufsGeraet(item, userKey)

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
        toast.error(fehlerText(err, t('mss.vault.dateien.hochladenFehler')))
      } finally {
        setVorbereitung((n) => n - 1)
      }
    }
  }

  const neuerOrdner = async () => {
    const name = await prompt({ message: t('mss.vault.dateien.ordnerName'), confirmText: t('mss.vault.dateien.ordnerAnlegen') })
    if (!name?.trim()) return
    try {
      await ordnerAnlegen(name.trim(), aktuellerOrdner)
    } catch (err) {
      toast.error(fehlerText(err, t('mss.vault.dateien.ordnerFehler')))
    }
  }

  const umbenennen = async (item: VaultItem) => {
    const name = await prompt({ message: t('mss.vault.dateien.neuerName'), defaultValue: item.service })
    if (!name?.trim() || name.trim() === item.service) return
    try {
      await aendern(item.id, { service: name.trim() })
    } catch (err) {
      toast.error(fehlerText(err, t('mss.vault.dateien.umbenennenFehler')))
    }
  }

  const oeffnen = (item: VaultItem) => {
    if (item.category === 'ordner') ordnerOeffnen(item.id)
    else if (item.datei) setAnsicht(item)
  }

  // ── Sammelaktionen (eine oder viele) ──────────────────────────────────────

  /**
   * Verschiebt, was nach `ziel` darf; was schon dort liegt, bleibt liegen. Ein
   * Fehler hält den Rest nicht auf; die Meldung nennt, wie viele nicht gingen.
   */
  const verschieben = async (liste: VaultItem[], ziel: string | undefined) => {
    const bewegt = liste.filter((i) => darfVerschieben(i, ziel, ordnerListe))
    if (bewegt.length === 0) return
    const geschafft: VaultItem[] = []
    for (const item of bewegt) {
      try {
        await aendern(item.id, { ordner: ziel })
        geschafft.push(item)
      } catch {
        // gezählt
      }
    }
    if (geschafft.length > 0) {
      toast.success(
        geschafft.length === 1
          ? t('mss.vault.dateien.verschoben', { name: geschafft[0].service, ordner: ordnerName(ziel) })
          : t('mss.vault.dateien.verschobenMehrere', { count: geschafft.length, ordner: ordnerName(ziel) }),
      )
    }
    if (geschafft.length < bewegt.length) toast.error(t('mss.vault.dateien.verschiebenFehler', { count: bewegt.length - geschafft.length }))
    auswahlLeeren()
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

  // Wie in der Galerie: ein Fehler hält den Rest nicht auf, die Meldung nennt die Zahl (tresorSammel).
  const archivieren = async (liste: VaultItem[]) => {
    if (liste.length === 0 || sammel) return
    await insArchiv(liste.map((i) => i.id), setSammel)
    auswahlLeeren()
  }

  const inPapierkorb = async (liste: VaultItem[]) => {
    if (liste.length === 0 || sammel) return
    await inDenPapierkorb(liste.map((i) => i.id), setSammel)
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
        disabled: leer || beschaeftigt,
        onSelect: () => void archivieren(liste),
      },
      {
        key: 'papierkorb',
        label: t('mss.vault.inPapierkorb'),
        kurz: t('mss.vault.dateien.kurz.papierkorb'),
        icon: <Trash2 className="h-5 w-5 md:h-4 md:w-4" />,
        destructive: true,
        disabled: leer || beschaeftigt,
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
            { key: 'oeffnen', label: t('mss.vault.dateien.oeffnen'), icon: <FolderOpen className="h-4 w-4" />, onSelect: () => ordnerOeffnen(item.id) },
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
      { key: 'auswaehlen', label: t('mss.vault.dateien.auswaehlen'), icon: <CheckSquare className="h-4 w-4" />, onSelect: () => wahl.langdruck(item.id) },
      {
        key: 'archiv',
        label: t('mss.vault.archivieren'),
        icon: <Archive className="h-4 w-4" />,
        disabled: sammel !== null,
        onSelect: () => void archivieren([item]),
      },
      {
        key: 'papierkorb',
        label: t('mss.vault.inPapierkorb'),
        icon: <Trash2 className="h-4 w-4" />,
        destructive: true,
        separatorBefore: true,
        disabled: sammel !== null,
        onSelect: () => void inPapierkorb([item]),
      },
    ]
  }

  // ── Darstellung ───────────────────────────────────────────────────────────

  const datum = new Intl.DateTimeFormat(i18n.language, { dateStyle: 'medium', timeStyle: 'short' })


  const listenTaste = (event: React.KeyboardEvent) => {
    // Strg+A und Escape.
    if (wahl.taste(event)) return
    if (event.key === 'Delete' && gewaehlt.length > 0) {
      event.preventDefault()
      void inPapierkorb(gewaehlt)
    }
  }

  const auswahlLabel = t('mss.vault.dateien.ausgewaehlt', { count: gewaehlt.length })

  return (
    <div className="flex min-h-0 flex-1">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-outline-variant/30 md:flex">
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          <TresorOrdnerBaum ordner={ordnerListe} aktuell={aktuellerOrdner} pfad={pfad} onWaehlen={ordnerOeffnen} ablage={{ kannAblegen, onAblegen: ablegen }} />
        </div>
        {speicher && (
          <div className="border-t border-outline-variant/30 p-3">
            <TresorSpeicherAnzeige speicher={speicher} />
          </div>
        )}
      </aside>

      <section className="relative flex min-w-0 flex-1 flex-col" {...flaecheAblage}>
        <header className="flex flex-wrap items-center gap-2 border-b border-outline-variant/30 px-3 py-2 sm:px-4">
          <Pfadleiste
            label={t('mss.vault.dateien.pfad')}
            // Auf dem Handy steht der Pfad in einer eigenen Zeile über den Knöpfen.
            className="basis-full text-xs [scrollbar-width:none] sm:basis-0"
            stamm={{ key: '', label: t('mss.vault.dateien.stamm'), icon: <HardDrive className="h-3.5 w-3.5" aria-hidden /> }}
            teile={pfad.map((o) => ({ key: o.id, label: o.service }))}
            onWaehlen={(key) => ordnerOeffnen(key || undefined)}
            hochLabel={t('mss.vault.dateien.hoch')}
            kannAblegen={(key, daten) => kannAblegen(key || undefined, daten)}
            onAblegen={(key, daten) => ablegen(key || undefined, daten)}
          />
          {!auswahl && (
            <div className="ml-auto flex items-center gap-1.5">
              {inhalt.length > 0 && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className={cx('md:hidden', TIPPFLAECHE)}
                  aria-label={t('mss.vault.dateien.auswaehlen')}
                  onClick={wahl.starten}
                >
                  <CheckSquare className="h-4 w-4" />
                </Button>
              )}
              <Button type="button" variant="ghost" size="sm" className={TIPPFLAECHE} onClick={() => void neuerOrdner()}>
                <FolderPlus className="mr-1.5 h-4 w-4" />
                {t('mss.vault.dateien.ordnerAnlegen')}
              </Button>
              <FileButton
                multiple
                size="sm"
                variant="primary"
                className={TIPPFLAECHE}
                disabled={ohneSpeicher}
                onFiles={(dateien) => void hochladen(dateien, aktuellerOrdner)}
              >
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
              alleLabel={gewaehlt.length < inhalt.length ? t('mss.vault.alle') : undefined}
              onAlle={wahl.alle}
            />
          </div>
        )}

        {speicher && !auswahl && (
          <div className="border-b border-outline-variant/30 px-3 py-2 md:hidden">
            <TresorSpeicherAnzeige speicher={speicher} />
          </div>
        )}

        {anzahlUploads > 0 && (
          <div className="border-b border-outline-variant/30 px-3 py-2 sm:px-4">
            <TresorUploadStand />
          </div>
        )}

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
          {inhalt.length === 0 && sucht ? (
            <div className="flex h-full min-h-64 flex-col items-center justify-center gap-3 px-6 text-center" role="status">
              <SearchX className="h-10 w-10 text-on-surface-variant/60" aria-hidden />
              <div>
                <p className="text-sm font-semibold text-on-surface">{t('mss.vault.dateien.sucheLeerTitel')}</p>
                <p className="mt-1 max-w-sm text-xs text-on-surface-variant">{t('mss.vault.dateien.sucheLeer', { suche: suche.trim() })}</p>
              </div>
            </div>
          ) : inhalt.length === 0 ? (
            <div className="flex h-full min-h-64 flex-col items-center justify-center gap-3 px-6 text-center">
              <FolderOpen className="h-10 w-10 text-on-surface-variant/60" aria-hidden />
              <div>
                <p className="text-sm font-semibold text-on-surface">{t('mss.vault.dateien.leerTitel')}</p>
                <p className="mt-1 max-w-sm text-xs text-on-surface-variant">
                  {ohneSpeicher ? (
                    t('mss.vault.dateien.keinSpeicher')
                  ) : (
                    <>
                      {t('mss.vault.dateien.leer')}
                      {/* Ziehen gibt es nur mit Maus; am Telefon stünde da etwas, das nicht geht. */}
                      <span className="hidden md:inline"> {t('mss.vault.dateien.leerZiehen')}</span>
                    </>
                  )}
                </p>
              </div>
              {!ohneSpeicher && (
                <FileButton multiple size="sm" variant="secondary" className={TIPPFLAECHE} onFiles={(dateien) => void hochladen(dateien, aktuellerOrdner)}>
                  <Upload className="mr-1.5 h-4 w-4" />
                  {t('mss.vault.dateien.hochladen')}
                </FileButton>
              )}
            </div>
          ) : (
            <>
              {sucht && (
                <p className="px-2 pb-1.5 text-label-sm text-on-surface-variant" role="status">
                  {t('mss.vault.dateien.trefferAnzahl', { count: inhalt.length })}
                </p>
              )}
              <div className={cx('hidden px-2 pb-1.5 text-label-sm text-on-surface-variant', SPALTEN)} aria-hidden>
                <span />
                <span className="pl-9">{t('mss.vault.dateien.spalteName')}</span>
                <span className="text-right">{t('mss.vault.dateien.spalteGroesse')}</span>
                <span>{t('mss.vault.dateien.geaendert')}</span>
                <span />
              </div>
              <ul
                aria-label={t(sucht ? 'mss.vault.dateien.treffer' : 'mss.vault.dateien.inhalt')}
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
                    ort={sucht ? ortVon(item.ordner) : undefined}
                    ziehen={zeileZiehen(item)}
                    ablage={item.category === 'ordner' ? ordnerAblage(item) : undefined}
                    onKlick={(event) => zeileKlick(item, event)}
                    onLangdruck={() => wahl.langdruck(item.id)}
                    onKaestchen={(schieben) => (schieben ? wahl.bereich(item.id) : wahl.umschalten(item.id))}
                    onMenue={(x, y, ausloeser, perKnopf) => setMenue({ item, x, y, ausloeser, blatt: perKnopf && amTelefon() })}
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
        ort={menue && !menue.blatt ? { x: menue.x, y: menue.y } : null}
        items={menue && !menue.blatt ? menueEintraege(menue.item) : []}
        label={t('mss.vault.dateien.aktionen')}
        ausloeser={menue?.ausloeser}
        onSchliessen={menueSchliessen}
      />

      {/* Am Telefon fährt das Menü von unten herein, in Daumenreichweite. */}
      <Blattmenue offen={!!menue?.blatt} onSchliessen={menueSchliessen} titel={menue ? t('mss.vault.dateien.aktionenFuer', { name: menue.item.service }) : ''}>
        {menue?.blatt && (
          <>
            <div className="truncate px-4 pb-1 pt-2 text-xs font-semibold text-on-surface-variant">{menue.item.service}</div>
            <div className="pb-2">
              {menueEintraege(menue.item).map((eintrag) => (
                <Blatteintrag
                  key={eintrag.key}
                  icon={eintrag.icon}
                  label={eintrag.label}
                  gefahr={eintrag.destructive}
                  disabled={eintrag.disabled}
                  onClick={() => {
                    menueSchliessen()
                    eintrag.onSelect()
                  }}
                />
              ))}
            </div>
          </>
        )}
      </Blattmenue>

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
          ort={ortVon(ansicht.ordner)}
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
  /** Ort des Eintrags, nur in Suchtreffern. */
  ort?: string
  ziehen: React.HTMLAttributes<HTMLLIElement> & { draggable: boolean }
  ablage: React.HTMLAttributes<HTMLLIElement> | undefined
  onKlick: (event: React.MouseEvent) => void
  onLangdruck: () => void
  /** Kästchen umgeschaltet; `true`, wenn dabei Umschalt gedrückt war. */
  onKaestchen: (schieben: boolean) => void
  /** `perKnopf`: über den Knopf „…“ geöffnet, nicht per Rechtsklick. */
  onMenue: (x: number, y: number, ausloeser: HTMLElement, perKnopf: boolean) => void
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
  ort,
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
      upload.fehler ? (
        <span className="text-label-sm text-status-destructive">{t(`mss.vault.dateien.${upload.fehler}`)}</span>
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
        onMenue(event.clientX, event.clientY, event.currentTarget, false)
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
        className="flex min-h-11 min-w-0 flex-1 items-center gap-3 rounded-md px-1 text-left sm:min-h-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
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
          {ort && <span className="block truncate text-label-sm text-on-surface-variant">{ort}</span>}
          {fortschritt && <span className="mt-1 block max-w-56">{fortschritt}</span>}
        </span>
      </button>
      <span className="hidden text-right font-mono text-label-sm text-on-surface-variant lg:block">{istOrdner ? '' : groesse ?? t('mss.vault.dateien.unlesbar')}</span>
      <span className="hidden truncate text-label-sm text-on-surface-variant lg:block">{geaendert}</span>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className={cx('min-h-11 min-w-11 shrink-0 opacity-70 group-hover:opacity-100 sm:min-h-9 sm:min-w-9', auswahlAktiv && 'invisible')}
        aria-label={t('mss.vault.dateien.aktionenFuer', { name: item.service })}
        aria-haspopup="menu"
        onClick={(event) => {
          const box = event.currentTarget.getBoundingClientRect()
          onMenue(box.right, box.bottom + 4, event.currentTarget, true)
        }}
      >
        <MoreHorizontal className="h-4 w-4" />
      </Button>
    </li>
  )
}
