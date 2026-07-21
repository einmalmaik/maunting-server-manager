import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { FileEditorWorkspace } from './FileEditorWorkspace'
import type { EditorTab } from './fileWorkspaceTypes'

vi.mock('@uiw/react-codemirror', () => ({ default: () => <div data-testid="code-editor" /> }))

function tab(path: string): EditorTab {
  return {
    path,
    content: '',
    savedContent: '',
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
    fireEvent.keyDown(first, { key: 'ArrowRight' })
    expect(onActivate).toHaveBeenCalledWith('config/two.ini')
    await waitFor(() => expect(second).toHaveFocus())
  })
})
