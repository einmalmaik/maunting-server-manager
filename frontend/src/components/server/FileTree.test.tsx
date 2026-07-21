import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { FileTree } from './FileTree'
import type { FileEntry } from './fileWorkspaceTypes'

function entry(name: string, isDir: boolean): FileEntry {
  return { name, is_dir: isDir, size: 0, modified: 0, mode: null, owner: null, group: null }
}

describe('FileTree keyboard navigation', () => {
  it('uses one roving tab stop and supports arrows, Home and End', async () => {
    const onToggle = vi.fn()
    render(
      <FileTree
        nodes={{ '': [entry('config', true), entry('README.md', false)] }}
        expanded={new Set([''])}
        loadingPaths={new Set()}
        activePath={null}
        searchResults={null}
        searchTruncated={false}
        emptyLabel="Empty"
        searchEmptyLabel="No matches"
        searchTruncatedLabel="Truncated"
        onToggle={onToggle}
        onOpenFile={vi.fn()}
        onContextMenu={vi.fn()}
        onDragStart={vi.fn()}
        onDropFolder={vi.fn()}
      />,
    )

    const root = screen.getByRole('treeitem', { name: 'Server-Dateien' })
    const config = screen.getByRole('treeitem', { name: 'config' })
    const readme = screen.getByRole('treeitem', { name: /README\.md/ })
    expect(root).toHaveAttribute('tabindex', '0')
    expect(config).toHaveAttribute('tabindex', '-1')

    root.focus()
    fireEvent.keyDown(root, { key: 'ArrowDown' })
    await waitFor(() => expect(config).toHaveFocus())
    expect(config).toHaveAttribute('tabindex', '0')

    fireEvent.keyDown(config, { key: 'ArrowRight' })
    expect(onToggle).toHaveBeenCalledWith('config')

    fireEvent.keyDown(config, { key: 'End' })
    await waitFor(() => expect(readme).toHaveFocus())
    fireEvent.keyDown(readme, { key: 'Home' })
    await waitFor(() => expect(root).toHaveFocus())
  })
})
