import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, File as FileIcon, Folder, Server } from 'lucide-react'
import type { FileEntry, SearchResult } from './fileWorkspaceTypes'
import { formatBytes, joinPath, sortEntries } from './fileHelpers'

interface FileTreeProps {
  nodes: Record<string, FileEntry[]>
  expanded: Set<string>
  loadingPaths: Set<string>
  activePath: string | null
  searchResults: SearchResult[] | null
  searchTruncated: boolean
  emptyLabel: string
  searchEmptyLabel: string
  searchTruncatedLabel: string
  onToggle: (path: string) => void
  onOpenFile: (path: string) => void
  onContextMenu: (event: React.MouseEvent, entry: FileEntry, parent: string) => void
  onDragStart: (event: React.DragEvent, entry: FileEntry, parent: string) => void
  onDropFolder: (event: React.DragEvent, entry: FileEntry, parent: string) => void
}

const ROOT_KEY = '$root'

function parentKey(path: string): string {
  const index = path.lastIndexOf('/')
  return index < 0 ? ROOT_KEY : path.slice(0, index)
}

export function FileTree({
  nodes,
  expanded,
  loadingPaths,
  activePath,
  searchResults,
  searchTruncated,
  emptyLabel,
  searchEmptyLabel,
  searchTruncatedLabel,
  onToggle,
  onOpenFile,
  onContextMenu,
  onDragStart,
  onDropFolder,
}: FileTreeProps) {
  const itemRefs = useRef(new Map<string, HTMLElement>())
  const [focusedKey, setFocusedKey] = useState(activePath ?? ROOT_KEY)

  const visibleKeys = useMemo(() => {
    if (searchResults) return searchResults.map((result) => result.path)
    const keys = [ROOT_KEY]
    const collect = (parent: string) => {
      for (const entry of sortEntries(nodes[parent] ?? [])) {
        const path = joinPath(parent, entry.name)
        keys.push(path)
        if (entry.is_dir && expanded.has(path)) collect(path)
      }
    }
    collect('')
    return keys
  }, [expanded, nodes, searchResults])

  useEffect(() => {
    if (activePath && visibleKeys.includes(activePath)) setFocusedKey(activePath)
  }, [activePath, visibleKeys])

  useEffect(() => {
    if (!visibleKeys.includes(focusedKey)) setFocusedKey(visibleKeys[0] ?? ROOT_KEY)
  }, [focusedKey, visibleKeys])

  const focusKey = useCallback((key: string) => {
    setFocusedKey(key)
    window.requestAnimationFrame(() => itemRefs.current.get(key)?.focus())
  }, [])

  const moveFocus = useCallback((current: string, offset: number) => {
    const index = visibleKeys.indexOf(current)
    if (index < 0) return
    const nextIndex = Math.max(0, Math.min(visibleKeys.length - 1, index + offset))
    focusKey(visibleKeys[nextIndex])
  }, [focusKey, visibleKeys])

  const handleTreeKeyDown = useCallback((
    event: React.KeyboardEvent,
    key: string,
    entry?: FileEntry,
  ) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      moveFocus(key, 1)
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      moveFocus(key, -1)
      return
    }
    if (event.key === 'Home') {
      event.preventDefault()
      if (visibleKeys.length) focusKey(visibleKeys[0])
      return
    }
    if (event.key === 'End') {
      event.preventDefault()
      if (visibleKeys.length) focusKey(visibleKeys[visibleKeys.length - 1])
      return
    }
    if (event.key === 'ArrowRight') {
      event.preventDefault()
      if (key === ROOT_KEY) {
        moveFocus(key, 1)
      } else if (entry?.is_dir && !expanded.has(key)) {
        onToggle(key)
      } else if (entry?.is_dir) {
        moveFocus(key, 1)
      }
      return
    }
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      if (entry?.is_dir && expanded.has(key)) onToggle(key)
      else if (key !== ROOT_KEY) focusKey(parentKey(key))
      return
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      if (key === ROOT_KEY) onToggle('')
      else if (entry?.is_dir) onToggle(key)
      else onOpenFile(key)
    }
  }, [expanded, focusKey, moveFocus, onOpenFile, onToggle, visibleKeys])

  const registerItem = (key: string) => (element: HTMLElement | null) => {
    if (element) itemRefs.current.set(key, element)
    else itemRefs.current.delete(key)
  }

  const renderChildren = (parent: string, depth: number): React.ReactNode => {
    const entries = sortEntries(nodes[parent] ?? [])
    return entries.map((entry) => {
      const path = joinPath(parent, entry.name)
      const isExpanded = entry.is_dir && expanded.has(path)
      const isActive = activePath === path
      return (
        <div key={path}>
          <div
            ref={registerItem(path)}
            role="treeitem"
            aria-expanded={entry.is_dir ? isExpanded : undefined}
            aria-selected={isActive}
            tabIndex={focusedKey === path ? 0 : -1}
            draggable
            onFocus={() => setFocusedKey(path)}
            onDragStart={(event) => onDragStart(event, entry, parent)}
            onDragOver={(event) => {
              if (entry.is_dir) {
                event.preventDefault()
                event.dataTransfer.dropEffect = 'move'
              }
            }}
            onDrop={(event) => entry.is_dir && onDropFolder(event, entry, parent)}
            onContextMenu={(event) => onContextMenu(event, entry, parent)}
            onClick={() => (entry.is_dir ? onToggle(path) : onOpenFile(path))}
            onKeyDown={(event) => handleTreeKeyDown(event, path, entry)}
            className={`group flex min-h-8 cursor-pointer items-center gap-1.5 border-l-2 pr-2 text-xs transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-primary/60 ${
              isActive
                ? 'border-secondary bg-secondary/10 text-primary'
                : 'border-transparent text-on-surface-variant hover:bg-surface-container-highest/65 hover:text-on-surface'
            }`}
            style={{ paddingLeft: `${8 + depth * 14}px` }}
          >
            {entry.is_dir ? (
              <span className="flex h-4 w-4 shrink-0 items-center justify-center">
                {loadingPaths.has(path) ? (
                  <span className="h-3 w-3 animate-spin rounded-full border border-secondary border-t-transparent" />
                ) : isExpanded ? (
                  <ChevronDown className="h-3.5 w-3.5" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5" />
                )}
              </span>
            ) : (
              <span className="h-4 w-4 shrink-0" />
            )}
            {entry.is_dir ? <Folder className="h-4 w-4 shrink-0 text-secondary" /> : <FileIcon className="h-4 w-4 shrink-0 text-on-surface-variant" />}
            <span className="min-w-0 flex-1 truncate">{entry.name}</span>
            {!entry.is_dir && <span className="shrink-0 font-mono text-[10px] text-on-surface-variant/60 group-hover:text-on-surface-variant">{formatBytes(entry.size)}</span>}
          </div>
          {entry.is_dir && isExpanded && <div role="group">{renderChildren(path, depth + 1)}</div>}
        </div>
      )
    })
  }

  if (searchResults) {
    return (
      <div role="tree" aria-label="Search results" className="py-1">
        {searchTruncated && <p className="border-b border-outline-variant px-3 py-2 text-xs text-status-warning">{searchTruncatedLabel}</p>}
        {searchResults.length === 0 ? (
          <p className="px-4 py-10 text-center text-xs text-on-surface-variant">{searchEmptyLabel}</p>
        ) : searchResults.map((result) => (
          <button
            ref={registerItem(result.path)}
            key={result.path}
            type="button"
            role="treeitem"
            tabIndex={focusedKey === result.path ? 0 : -1}
            onFocus={() => setFocusedKey(result.path)}
            onClick={() => (result.is_dir ? onToggle(result.path) : onOpenFile(result.path))}
            onKeyDown={(event) => handleTreeKeyDown(event, result.path, { ...result, name: result.path, size: 0, modified: 0, mode: null, owner: null, group: null })}
            className="flex min-h-11 w-full items-center gap-2 px-3 py-2 text-left text-xs text-on-surface-variant hover:bg-surface-container-highest hover:text-on-surface focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-primary"
          >
            {result.is_dir ? <Folder className="h-4 w-4 text-secondary" /> : <FileIcon className="h-4 w-4" />}
            <span className="truncate font-mono">{result.path}</span>
          </button>
        ))}
      </div>
    )
  }

  return (
    <div role="tree" aria-label="Server files" className="py-1">
      <button
        ref={registerItem(ROOT_KEY)}
        type="button"
        role="treeitem"
        aria-expanded
        tabIndex={focusedKey === ROOT_KEY ? 0 : -1}
        onFocus={() => setFocusedKey(ROOT_KEY)}
        onClick={() => onToggle('')}
        onKeyDown={(event) => handleTreeKeyDown(event, ROOT_KEY)}
        className="flex min-h-9 w-full items-center gap-2 px-3 text-left text-xs font-semibold text-on-surface hover:bg-surface-container-highest focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-primary"
      >
        <ChevronDown className="h-3.5 w-3.5" />
        <Server className="h-4 w-4 text-secondary" />
        <span>Server-Dateien</span>
      </button>
      {(nodes['']?.length ?? 0) === 0 ? <p className="px-4 py-10 text-center text-xs text-on-surface-variant">{emptyLabel}</p> : renderChildren('', 1)}
    </div>
  )
}
