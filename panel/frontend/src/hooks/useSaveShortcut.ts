import { useEffect } from 'react'
import type * as Monaco from 'monaco-editor'

type SaveShortcutOptions = {
  enabled?: boolean
  allowWhileTyping?: boolean
  onSave: () => void
}

export function isEditableTarget(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null
  if (!element) return false
  const tagName = element.tagName
  return element.isContentEditable || tagName === 'INPUT' || tagName === 'TEXTAREA' || tagName === 'SELECT'
}

function isMonacoTarget(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null
  return Boolean(element?.closest?.('.monaco-editor, .monaco-diff-editor'))
}

export function useSaveShortcut({
  enabled = true,
  allowWhileTyping = false,
  onSave,
}: SaveShortcutOptions): void {
  useEffect(() => {
    if (!enabled) return

    const handler = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 's') return
      if (isMonacoTarget(event.target)) return
      if (!allowWhileTyping && isEditableTarget(event.target)) return

      event.preventDefault()
      onSave()
    }

    window.addEventListener('keydown', handler, { capture: true })
    return () => window.removeEventListener('keydown', handler, { capture: true })
  }, [allowWhileTyping, enabled, onSave])
}

export function bindMonacoSaveShortcut(
  editor: Monaco.editor.IStandaloneCodeEditor | Monaco.editor.IStandaloneDiffEditor,
  monaco: typeof Monaco,
  onSave: () => void,
): Monaco.IDisposable {
  const targetEditor = typeof (editor as Monaco.editor.IStandaloneDiffEditor).getModifiedEditor === 'function'
    ? (editor as Monaco.editor.IStandaloneDiffEditor).getModifiedEditor()
    : editor as Monaco.editor.IStandaloneCodeEditor

  return targetEditor.addAction({
    id: 'panel-save',
    label: 'Save',
    keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS],
    run: () => onSave(),
  })
}
