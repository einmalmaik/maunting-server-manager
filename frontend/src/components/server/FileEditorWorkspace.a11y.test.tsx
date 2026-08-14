import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'
import { FileEditorWorkspace } from './FileEditorWorkspace'
import type { EditorTab } from './fileWorkspaceTypes'

vi.mock('@uiw/react-codemirror', () => ({
  default: ({
    className,
    height,
    onCreateEditor,
  }: {
    className?: string
    height?: string
    onCreateEditor: (view: { dispatch: ReturnType<typeof vi.fn>; focus: () => void }) => void
  }) => (
    <button
      type="button"
      data-testid="code-editor"
      data-height={height}
      className={className}
      onClick={(event) => {
        const editorElement = event.currentTarget
        onCreateEditor({
          dispatch: vi.fn(),
          focus: () => editorElement.focus(),
        })
      }}
    />
  ),
}))

function tab(path: string, content = ''): EditorTab {
  return {
    path,
    content,
    savedContent: content,
    revision: 'revision',
    lineEnding: '\n',
    loading: false,
    saveState: 'clean',
    size: 0,
    modified: 0,
    mode: null,
    owner: null,
    group: null,
  }
}

describe('FileEditorWorkspace tabs', () => {
  // Die sichtbaren Texte kommen jetzt aus der Sprachdatei. Ohne feste Sprache
  // hängen die Erwartungen unten an der Spracherkennung der Testumgebung.
  beforeEach(async () => {
    await i18n.changeLanguage('de')
  })

  it('binds the CodeMirror wrapper and editor to the available height', () => {
    render(
      <FileEditorWorkspace
        tabs={[tab('config/server.ini', 'setting=value')]}
        activePath="config/server.ini"
        canWrite
        tabListLabel="Open files"
        horizontalScrollHint="Scroll horizontally"
        onActivate={vi.fn()}
        onChange={vi.fn()}
        onSave={vi.fn()}
        onClose={vi.fn()}
        onReload={vi.fn()}
      />,
    )

    expect(screen.getByTestId('code-editor')).toHaveClass('h-full')
    expect(screen.getByTestId('code-editor')).toHaveAttribute('data-height', '100%')
  })

  it('uses sibling native controls and supports roving arrow navigation', async () => {
    const onActivate = vi.fn()
    render(
      <FileEditorWorkspace
        tabs={[tab('config/one.ini'), tab('config/two.ini')]}
        activePath="config/one.ini"
        canWrite
        tabListLabel="Open files"
        horizontalScrollHint="Scroll horizontally"
        onActivate={onActivate}
        onChange={vi.fn()}
        onSave={vi.fn()}
        onClose={vi.fn()}
        onReload={vi.fn()}
      />,
    )

    const first = screen.getByRole('tab', { name: 'one.ini' })
    const second = screen.getByRole('tab', { name: 'two.ini' })
    const close = screen.getByRole('button', { name: 'one.ini schließen' })
    expect(first.contains(close)).toBe(false)
    expect(first).toHaveAttribute('tabindex', '0')
    expect(second).toHaveAttribute('tabindex', '-1')

    first.focus()
    const focusSpy = vi.spyOn(second, 'focus')
    fireEvent.keyDown(first, { key: 'ArrowRight' })
    expect(onActivate).toHaveBeenCalledWith('config/two.ini')
    await waitFor(() => expect(second).toHaveFocus())
    expect(focusSpy).toHaveBeenCalledWith({ preventScroll: true })
  })

  it('keeps focus in find and replacement inputs while selecting matches', async () => {
    render(
      <FileEditorWorkspace
        tabs={[tab('config/server.ini', 'Alpha=one\nAlpha=two')]}
        activePath="config/server.ini"
        canWrite
        tabListLabel="Open files"
        horizontalScrollHint="Scroll horizontally"
        onActivate={vi.fn()}
        onChange={vi.fn()}
        onSave={vi.fn()}
        onClose={vi.fn()}
        onReload={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByTestId('code-editor'))
    fireEvent.click(screen.getByRole('button', { name: 'Suchen und ersetzen' }))

    const findInput = await screen.findByPlaceholderText('Suchen…')
    await waitFor(() => expect(findInput).toHaveFocus())
    fireEvent.change(findInput, { target: { value: 'Alpha' } })
    expect(findInput).toHaveFocus()
    fireEvent.keyDown(findInput, { key: 'Enter' })
    expect(findInput).toHaveFocus()
    fireEvent.keyDown(findInput, { key: 'Enter', shiftKey: true })
    expect(findInput).toHaveFocus()

    const replacementInput = screen.getByPlaceholderText('Ersetzen durch…')
    replacementInput.focus()
    fireEvent.change(replacementInput, { target: { value: 'Beta' } })
    expect(replacementInput).toHaveFocus()
  })
})

/**
 * Der Editor war vollständig auf Deutsch verdrahtet, während der Dateibaum
 * daneben längst aus dem Katalog las. Wer die Oberfläche auf Englisch stellte,
 * bekam im selben Bild einen englischen Baum und einen deutschen Editor — bis
 * hin zum Warnhinweis über den Änderungskonflikt. Diese Tests halten fest, dass
 * die sichtbaren Texte aus der Sprachdatei kommen und nicht aus dem Quelltext.
 */
describe('FileEditorWorkspace — Sprachwahl', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en')
  })

  afterEach(async () => {
    await i18n.changeLanguage('de')
  })

  it('beschriftet Suchleiste und Speichern in der gewählten Sprache', () => {
    render(
      <FileEditorWorkspace
        tabs={[tab('config/server.ini', 'Alpha=one')]}
        activePath="config/server.ini"
        canWrite
        tabListLabel="Open files"
        horizontalScrollHint="Scroll horizontally"
        onActivate={vi.fn()}
        onChange={vi.fn()}
        onSave={vi.fn()}
        onClose={vi.fn()}
        onReload={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: 'Search and replace' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Close server.ini' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Speichern' })).toBeNull()
  })

  it('führt den Konflikthinweis in der gewählten Sprache', () => {
    render(
      <FileEditorWorkspace
        tabs={[{ ...tab('config/server.ini', 'Alpha=one'), saveState: 'conflict' }]}
        activePath="config/server.ini"
        canWrite
        tabListLabel="Open files"
        horizontalScrollHint="Scroll horizontally"
        onActivate={vi.fn()}
        onChange={vi.fn()}
        onSave={vi.fn()}
        onClose={vi.fn()}
        onReload={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: 'Reload server version' })).toBeInTheDocument()
    expect(screen.getByText(/changed outside this editor/i)).toBeInTheDocument()
  })

  it('zeigt den leeren Editor in der gewählten Sprache', () => {
    render(
      <FileEditorWorkspace
        tabs={[]}
        activePath={null}
        canWrite
        tabListLabel="Open files"
        horizontalScrollHint="Scroll horizontally"
        onActivate={vi.fn()}
        onChange={vi.fn()}
        onSave={vi.fn()}
        onClose={vi.fn()}
        onReload={vi.fn()}
      />,
    )

    expect(screen.getByText('No file open')).toBeInTheDocument()
    expect(screen.getByText('Open a file to edit')).toBeInTheDocument()
  })
})
