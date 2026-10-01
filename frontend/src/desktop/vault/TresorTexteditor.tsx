/**
 * Textdateien im Tresor bearbeiten, mit dem Editor des Dateimanagers.
 *
 * Gespeichert wird als neue Fassung über `dateiErsetzen`: neu verschlüsselt,
 * neue Blobs, die alte Fassung bleibt unter `frueher`. Der Editor wird erst
 * geladen, wenn jemand ihn öffnet (CodeMirror gehört nicht ins Startbündel).
 */

import { lazy, Suspense, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { LoaderCircle } from 'lucide-react'
import { Button } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { detectLineEnding, serializeLineEndings } from '@/components/server/fileHelpers'
import type { EditorTab } from '@/components/server/fileWorkspaceTypes'
import { useVaultStore, type VaultItem } from './vaultStore'

const FileEditorWorkspace = lazy(() => import('@/components/server/FileEditorWorkspace').then((m) => ({ default: m.FileEditorWorkspace })))

export function TresorTexteditor({ item, text, onFertig }: { item: VaultItem; text: string; onFertig: (gespeichert: string | null) => void }) {
  const { t } = useTranslation()
  const dateiErsetzen = useVaultStore((s) => s.dateiErsetzen)
  const [tab, setTab] = useState<EditorTab>(() => ({
    path: item.service,
    content: text,
    savedContent: text,
    revision: '',
    lineEnding: detectLineEnding(text),
    loading: false,
    saveState: 'clean',
    size: text.length,
    modified: (item.datei?.geaendert ?? item.updatedAt) / 1000,
    mode: null,
    owner: null,
    group: null,
  }))

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
    if (tab.content !== tab.savedContent && !(await confirm({ message: t('mss.vault.bearbeiten.verwerfen'), danger: true }))) return
    onFertig(tab.savedContent !== text ? tab.savedContent : null)
  }

  return (
    <div className="flex h-[65vh] flex-col gap-2 md:h-auto md:min-h-0 md:flex-1">
      <Suspense
        fallback={
          <div className="flex flex-1 items-center justify-center">
            <LoaderCircle className="h-5 w-5 animate-spin text-on-surface-variant" />
          </div>
        }
      >
        <div className="min-h-0 flex-1">
          <FileEditorWorkspace
            tabs={[tab]}
            activePath={tab.path}
            canWrite
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
      <div className="flex justify-end">
        <Button type="button" variant="ghost" size="sm" onClick={() => void schliessen()}>
          {t('common.close')}
        </Button>
      </div>
    </div>
  )
}
