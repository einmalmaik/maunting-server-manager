/**
 * Textdateien im Tresor bearbeiten, mit dem Editor des Dateimanagers, im
 * ganzen Fenster.
 *
 * Gespeichert wird als neue Fassung über `dateiErsetzen`: neu verschlüsselt,
 * neue Blobs, die alte Fassung bleibt unter `frueher` und lässt sich in der
 * Seitenleiste „Versionen“ zurückholen. Der Editor wird erst geladen, wenn
 * jemand ihn öffnet (CodeMirror gehört nicht ins Startbündel).
 */

import { lazy, Suspense, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Download, FileText, History, LoaderCircle, X } from 'lucide-react'
import { Button, Dialog, DialogContent, Versionsliste } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { detectLineEnding, serializeLineEndings } from '@/components/server/fileHelpers'
import type { EditorTab } from '@/components/server/fileWorkspaceTypes'
import { useVaultStore, type VaultItem } from './vaultStore'
import { blobLesen } from './tresorDateien'
import { aufGeraetSpeichern } from './tresorAnzeige'
import { fassungenVon } from './tresorOrdner'

const FileEditorWorkspace = lazy(() => import('@/components/server/FileEditorWorkspace').then((m) => ({ default: m.FileEditorWorkspace })))

interface Props {
  item: VaultItem
  text: string
  /** Ort der Datei für die Kopfzeile des Editors, etwa „Stammverzeichnis / Verträge“. */
  ort?: string
  onFertig: (gespeichert: string | null) => void
}

export function TresorTexteditor({ item: anfang, text, ort, onFertig }: Props) {
  const { t } = useTranslation()
  const dateiErsetzen = useVaultStore((s) => s.dateiErsetzen)
  const fassungZurueckholen = useVaultStore((s) => s.fassungZurueckholen)
  const userKey = useVaultStore((s) => s.userKey)
  // Nach dem Speichern hat die Datei ein neues Original und neue frühere Fassungen.
  const item = useVaultStore((s) => s.items.find((i) => i.id === anfang.id)) ?? anfang
  const [versionenOffen, setVersionenOffen] = useState(false)
  const [holt, setHolt] = useState<string | null>(null)
  const [tab, setTab] = useState<EditorTab>(() => ({
    path: anfang.service,
    content: text,
    savedContent: text,
    revision: '',
    lineEnding: detectLineEnding(text),
    loading: false,
    saveState: 'clean',
    size: text.length,
    modified: (anfang.datei?.geaendert ?? anfang.updatedAt) / 1000,
    mode: null,
    owner: null,
    group: null,
  }))
  const ungespeichert = tab.content !== tab.savedContent

  const speichern = async () => {
    if (tab.saveState === 'clean' || tab.saveState === 'saving') return
    const inhalt = tab.content
    setTab((a) => ({ ...a, saveState: 'saving' }))
    try {
      const blob = new Blob([serializeLineEndings(inhalt, tab.lineEnding)], { type: item.datei?.typ || 'text/plain' })
      await dateiErsetzen(item.id, blob)
      setTab((a) => ({ ...a, savedContent: inhalt, saveState: a.content === inhalt ? 'clean' : 'dirty', size: blob.size }))
      toast.success(t('mss.vault.bearbeiten.gespeichert'))
    } catch (err) {
      setTab((a) => ({ ...a, saveState: 'error' }))
      toast.error(err instanceof Error ? err.message : t('mss.vault.bearbeiten.fehler'))
    }
  }

  const schliessen = async () => {
    if (ungespeichert && !(await confirm({ message: t('mss.vault.bearbeiten.verwerfen'), danger: true }))) return
    onFertig(tab.savedContent !== text ? tab.savedContent : null)
  }

  const aufGeraet = async () => {
    if (!item.datei || !userKey) return
    try {
      await aufGeraetSpeichern(item.datei.original, item.id, userKey, item.service, item.datei.typ)
    } catch {
      toast.error(t('mss.vault.dateien.speichernFehler'))
    }
  }

  const zurueckholen = async (originalId: string) => {
    if (ungespeichert && !(await confirm({ message: t('mss.vault.bearbeiten.verwerfen'), danger: true }))) return
    setHolt(originalId)
    try {
      await fassungZurueckholen(item.id, originalId)
      const neu = useVaultStore.getState().items.find((i) => i.id === item.id)?.datei
      if (!neu || !userKey) return
      const inhalt = await (await blobLesen(neu.original, item.id, userKey, neu.typ)).text()
      setTab((a) => ({ ...a, content: inhalt, savedContent: inhalt, saveState: 'clean', lineEnding: detectLineEnding(inhalt), size: inhalt.length }))
      toast.success(t('mss.vault.dateien.fassungZurueck'))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t('mss.vault.bearbeiten.fehler'))
    } finally {
      setHolt(null)
    }
  }

  return (
    <Dialog open onOpenChange={() => undefined}>
      <DialogContent className="h-[100dvh] max-w-none rounded-none border-0" overlayClassName="p-0" showCloseButton={false} data-testid="tresor-texteditor">
        <header className="flex min-h-12 items-center gap-2 border-b border-outline-variant bg-surface-container-low px-3 py-1.5">
          <FileText className="h-4 w-4 shrink-0 text-secondary" aria-hidden />
          <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-on-surface">{item.service}</h2>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-pressed={versionenOffen}
            aria-label={t('common.versionen.titel')}
            className={versionenOffen ? 'bg-primary/10 text-primary' : ''}
            onClick={() => setVersionenOffen((o) => !o)}
          >
            <History className="h-4 w-4" />
            <span className="ml-1.5 hidden sm:inline">{t('common.versionen.titel')}</span>
          </Button>
          <Button type="button" variant="ghost" size="sm" aria-label={t('mss.vault.dateien.speichern')} onClick={() => void aufGeraet()}>
            <Download className="h-4 w-4" />
            <span className="ml-1.5 hidden sm:inline">{t('mss.vault.dateien.speichern')}</span>
          </Button>
          <Button type="button" variant="ghost" size="sm" aria-label={t('common.close')} onClick={() => void schliessen()}>
            <X className="h-4 w-4" />
            <span className="ml-1.5 hidden sm:inline">{t('common.close')}</span>
          </Button>
        </header>
        <div className="relative flex min-h-0 flex-1">
          <Suspense
            fallback={
              <div className="flex flex-1 items-center justify-center">
                <LoaderCircle className="h-5 w-5 animate-spin text-on-surface-variant" />
              </div>
            }
          >
            <div className="flex min-h-0 min-w-0 flex-1">
              <FileEditorWorkspace
                tabs={[tab]}
                activePath={tab.path}
                canWrite
                vollbild
                ortLabel={ort ? `${ort} / ${item.service}` : item.service}
                tabListLabel={t('mss.vault.bearbeiten.titel', { name: item.service })}
                horizontalScrollHint={t('files.horizontalScrollHint')}
                onActivate={() => undefined}
                onChange={(_, content) => setTab((a) => ({ ...a, content, saveState: content === a.savedContent ? 'clean' : 'dirty' }))}
                onSave={() => void speichern()}
                onClose={() => void schliessen()}
                onReload={() => setTab((a) => ({ ...a, content: a.savedContent, saveState: 'clean' }))}
              />
            </div>
          </Suspense>
          {versionenOffen && (
            <aside
              aria-label={t('common.versionen.titel')}
              className="absolute inset-x-0 bottom-0 z-10 max-h-[60%] overflow-y-auto border-t border-outline-variant bg-surface-container-low p-3 shadow-panel-strong sm:static sm:max-h-none sm:w-72 sm:border-l sm:border-t-0 sm:shadow-none"
            >
              <h3 className="mb-1 text-xs font-semibold text-on-surface">{t('common.versionen.titel')}</h3>
              <p className="mb-3 text-label-sm text-on-surface-variant">{t('common.versionen.hinweis')}</p>
              <Versionsliste versionen={fassungenVon(item)} onWiederherstellen={(id) => void zurueckholen(id)} laeuft={holt} />
            </aside>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
