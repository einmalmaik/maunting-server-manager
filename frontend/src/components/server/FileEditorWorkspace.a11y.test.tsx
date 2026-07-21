import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { FileEditorWorkspace } from './FileEditorWorkspace'
import type { EditorTab } from './fileWorkspaceTypes'

vi.mock('@uiw/react-codemirror', () => ({
  default: ({ onCreateEditor }: { onCreateEditor: (view: { dispatch: ReturnType<typeof vi.fn>; focus: () => void }) => void }) => (
    <button
      type="button"
      data-testid="code-editor"
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
