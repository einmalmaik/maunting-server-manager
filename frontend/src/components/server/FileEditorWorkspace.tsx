import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import CodeMirror from '@uiw/react-codemirror'
import { json } from '@codemirror/lang-json'
import { yaml } from '@codemirror/lang-yaml'
import { xml } from '@codemirror/lang-xml'
import { markdown } from '@codemirror/lang-markdown'
import { StreamLanguage, type LanguageSupport } from '@codemirror/language'
import { EditorView, keymap, type ViewUpdate } from '@codemirror/view'
import { css } from '@codemirror/legacy-modes/mode/css'
import { cpp, csharp, java } from '@codemirror/legacy-modes/mode/clike'
import { dockerFile } from '@codemirror/legacy-modes/mode/dockerfile'
import { go } from '@codemirror/legacy-modes/mode/go'
import { javascript, typescript } from '@codemirror/legacy-modes/mode/javascript'
import { lua } from '@codemirror/legacy-modes/mode/lua'
import { properties } from '@codemirror/legacy-modes/mode/properties'
import { python } from '@codemirror/legacy-modes/mode/python'
import { rust } from '@codemirror/legacy-modes/mode/rust'
import { shell } from '@codemirror/legacy-modes/mode/shell'
import { sqlite } from '@codemirror/legacy-modes/mode/sql'
import { toml } from '@codemirror/legacy-modes/mode/toml'
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronUp,
  FileCode2,
  LoaderCircle,
  Replace,
  RotateCcw,
  Save,
  Search,
  X,
} from 'lucide-react'
import { detectLanguage, detectIndentation, fileName } from './fileHelpers'
import type { EditorTab } from './fileWorkspaceTypes'

interface FileEditorWorkspaceProps {
  tabs: EditorTab[]
  activePath: string | null
  canWrite: boolean
  tabListLabel: string
  horizontalScrollHint: string
  onActivate: (path: string) => void
  onChange: (path: string, content: string) => void
  onSave: (path: string) => void
  onClose: (path: string) => void
  onReload: (path: string) => void
}

function languageExtension(path: string): LanguageSupport | ReturnType<typeof StreamLanguage.define> | null {
  switch (detectLanguage(path)) {
    case 'json': return json()
    case 'yaml': return yaml()
    case 'xml': return xml()
    case 'markdown': return markdown()
    case 'ini':
    case 'properties': return StreamLanguage.define(properties)
    case 'javascript': return StreamLanguage.define(javascript)
    case 'typescript': return StreamLanguage.define(typescript)
    case 'python': return StreamLanguage.define(python)
    case 'shell': return StreamLanguage.define(shell)
    case 'sql': return StreamLanguage.define(sqlite)
    case 'css': return StreamLanguage.define(css)
    case 'cpp': return StreamLanguage.define(cpp)
    case 'java': return StreamLanguage.define(java)
    case 'csharp': return StreamLanguage.define(csharp)
    case 'go': return StreamLanguage.define(go)
    case 'rust': return StreamLanguage.define(rust)
    case 'lua': return StreamLanguage.define(lua)
    case 'toml': return StreamLanguage.define(toml)
    case 'dockerfile': return StreamLanguage.define(dockerFile)
    default: return null
  }
}

const EDITOR_THEME = EditorView.theme({
  '&': { backgroundColor: '#071013', color: '#e7f4f7', fontSize: '13px', height: '100%' },
  '.cm-content': { caretColor: '#67e8f9', fontFamily: 'JetBrains Mono, monospace' },
  '.cm-gutters': { backgroundColor: '#071013', borderRight: '1px solid #203038', color: '#5b737a' },
  '.cm-scroller': { overflowX: 'auto', lineHeight: '1.55' },
  '.cm-activeLine, .cm-activeLineGutter': { backgroundColor: 'rgba(103, 232, 249, 0.045)' },
  '&.cm-focused': { outline: 'none' },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': { backgroundColor: 'rgba(103, 232, 249, 0.22)' },
})

const EDITOR_BASIC_SETUP = {
  lineNumbers: true,
  highlightActiveLine: true,
  foldGutter: true,
  tabSize: 2,
  defaultKeymap: true,
  searchKeymap: false,
} as const

export function matchPositions(content: string, query: string, caseSensitive: boolean): Array<{ from: number; to: number }> {
  if (!query) return []
  const haystack = caseSensitive ? content : content.toLocaleLowerCase()
  const needle = caseSensitive ? query : query.toLocaleLowerCase()
  const matches: Array<{ from: number; to: number }> = []
  let position = 0
  while (position <= haystack.length - needle.length) {
    const found = haystack.indexOf(needle, position)
    if (found < 0) break
    matches.push({ from: found, to: found + needle.length })
    position = found + Math.max(1, needle.length)
  }
  return matches
}

export function FileEditorWorkspace({
  tabs,
  activePath,
  canWrite,
  tabListLabel,
  horizontalScrollHint,
  onActivate,
  onChange,
  onSave,
  onClose,
  onReload,
}: FileEditorWorkspaceProps) {
  const activeTab = tabs.find((tab) => tab.path === activePath) ?? null
  const editorRef = useRef<EditorView | null>(null)
  const tabRefs = useRef(new Map<string, HTMLButtonElement>())
  const findInputRef = useRef<HTMLInputElement>(null)
  const activePathRef = useRef(activeTab?.path ?? null)
  const onChangeRef = useRef(onChange)
  const onSaveRef = useRef(onSave)
  activePathRef.current = activeTab?.path ?? null
  onChangeRef.current = onChange
  onSaveRef.current = onSave
  const [searchOpen, setSearchOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [replacement, setReplacement] = useState('')
  const [caseSensitive, setCaseSensitive] = useState(false)
  const [activeMatch, setActiveMatch] = useState(0)
  const [cursor, setCursor] = useState({ line: 1, column: 1, selected: 0 })

  useEffect(() => {
    setQuery('')
    setReplacement('')
    setActiveMatch(0)
    setSearchOpen(false)
  }, [activePath])

  useEffect(() => {
    if (searchOpen) window.setTimeout(() => findInputRef.current?.focus({ preventScroll: true }), 0)
  }, [searchOpen])

  const matches = useMemo(
    () => matchPositions(activeTab?.content ?? '', query, caseSensitive),
    [activeTab?.content, caseSensitive, query],
  )

  const selectMatch = (index: number) => {
    if (!matches.length || !editorRef.current) return
    const normalized = (index + matches.length) % matches.length
    const match = matches[normalized]
    setActiveMatch(normalized)
    editorRef.current.dispatch({
      selection: { anchor: match.from, head: match.to },
      effects: EditorView.scrollIntoView(match.from, { y: 'center' }),
    })
  }

  useEffect(() => {
    if (query && matches.length) selectMatch(Math.min(activeMatch, matches.length - 1))
    // Selection should only follow query/content changes, not every editor transaction.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, caseSensitive])

  const replaceCurrent = () => {
    if (!canWrite || !matches.length || !editorRef.current) return
    const match = matches[Math.min(activeMatch, matches.length - 1)]
    editorRef.current.dispatch({ changes: { from: match.from, to: match.to, insert: replacement } })
  }

  const replaceAll = () => {
    if (!canWrite || !activeTab || !matches.length || !editorRef.current) return
    editorRef.current.dispatch({
      changes: matches.map((match) => ({ from: match.from, to: match.to, insert: replacement })),
    })
  }

  const extensions = useMemo(() => {
    if (!activeTab) return []
    const language = languageExtension(activeTab.path)
    return [
      ...(language ? [language] : []),
      keymap.of([
        {
          key: 'Mod-s',
          preventDefault: true,
          run: () => {
            const path = activePathRef.current
            if (path) onSaveRef.current(path)
            return true
          },
        },
        {
          key: 'Mod-f',
          preventDefault: true,
          run: () => {
            setSearchOpen(true)
            return true
          },
        },
      ]),
      EDITOR_THEME,
    ]
  }, [activeTab?.path])

  const updateCursor = useCallback((update: ViewUpdate) => {
    const main = update.state.selection.main
    const line = update.state.doc.lineAt(main.head)
    setCursor({
      line: line.number,
      column: main.head - line.from + 1,
      selected: Math.abs(main.head - main.anchor),
    })
  }, [])

  const handleEditorChange = useCallback((value: string) => {
    const path = activePathRef.current
    if (path) onChangeRef.current(path, value)
  }, [])

  const handleCreateEditor = useCallback((view: EditorView) => {
    editorRef.current = view
  }, [])

  const focusTab = useCallback((path: string) => {
    onActivate(path)
    window.requestAnimationFrame(() => tabRefs.current.get(path)?.focus({ preventScroll: true }))
  }, [onActivate])

  const handleTabKeyDown = useCallback((event: React.KeyboardEvent, path: string) => {
    const currentIndex = tabs.findIndex((tab) => tab.path === path)
    if (currentIndex < 0) return
    let nextIndex: number | null = null
    if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % tabs.length
    if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + tabs.length) % tabs.length
    if (event.key === 'Home') nextIndex = 0
    if (event.key === 'End') nextIndex = tabs.length - 1
    if (nextIndex == null) return
    event.preventDefault()
    focusTab(tabs[nextIndex].path)
  }, [focusTab, tabs])

  const saveIndicator = activeTab?.saveState === 'saving'
    ? <><LoaderCircle className="h-3.5 w-3.5 animate-spin" /> Speichert…</>
    : activeTab?.saveState === 'conflict'
      ? <><AlertTriangle className="h-3.5 w-3.5" /> Konflikt</>
      : activeTab?.saveState === 'error'
        ? <><AlertTriangle className="h-3.5 w-3.5" /> Speichern fehlgeschlagen</>
        : activeTab?.saveState === 'dirty'
          ? <><span className="h-2 w-2 rounded-full bg-status-warning" /> Ungespeichert</>
          : <><Check className="h-3.5 w-3.5" /> Gespeichert</>

  return (
    <section className="flex h-full min-h-[520px] min-w-0 flex-1 flex-col bg-surface-container-lowest/55 lg:min-h-0">
      <div role="tablist" aria-label={tabListLabel} className="flex min-h-10 items-end overflow-x-auto border-b border-outline-variant bg-surface-container-low/70 [scrollbar-width:thin]">
        {tabs.length === 0 ? (
          <div className="px-4 py-2.5 text-xs text-on-surface-variant">Keine Datei geöffnet</div>
        ) : tabs.map((tab) => {
          const active = tab.path === activePath
          return (
            <div
              key={tab.path}
              className={`group flex h-10 max-w-56 shrink-0 items-center gap-2 border-r border-outline-variant px-3 text-xs transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-primary ${
                active ? 'border-t-2 border-t-secondary bg-surface-container text-primary' : 'border-t-2 border-t-transparent text-on-surface-variant hover:bg-surface-container-high/60 hover:text-on-surface'
              }`}
            >
              <button
                ref={(element) => {
                  if (element) tabRefs.current.set(tab.path, element)
                  else tabRefs.current.delete(tab.path)
                }}
                type="button"
                role="tab"
                aria-selected={active}
                aria-controls="file-editor-panel"
                tabIndex={active ? 0 : -1}
                onClick={() => onActivate(tab.path)}
                onKeyDown={(event) => handleTabKeyDown(event, tab.path)}
                className="flex min-w-0 flex-1 items-center gap-2 self-stretch text-left focus-visible:outline-none"
              >
                <FileCode2 className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">{fileName(tab.path)}</span>
                {tab.saveState !== 'clean' && tab.saveState !== 'saving' && (
                  <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${tab.saveState === 'conflict' || tab.saveState === 'error' ? 'bg-status-error' : 'bg-status-warning'}`} />
                )}
              </button>
              <button
                type="button"
                aria-label={`${fileName(tab.path)} schließen`}
                onClick={(event) => {
                  event.stopPropagation()
                  onClose(tab.path)
                }}
                className="ml-auto rounded p-0.5 opacity-60 hover:bg-surface-container-highest hover:opacity-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          )
        })}
      </div>

      {activeTab ? (
        <>
          <div className="flex min-h-10 items-center justify-between gap-3 border-b border-outline-variant px-3">
            <p className="min-w-0 truncate font-mono text-[11px] text-on-surface-variant">Server-Dateien / {activeTab.path}</p>
            <div className="flex shrink-0 items-center gap-2">
              <span className={`hidden items-center gap-1.5 text-[11px] sm:inline-flex ${activeTab.saveState === 'conflict' || activeTab.saveState === 'error' ? 'text-status-error' : activeTab.saveState === 'clean' ? 'text-status-success' : 'text-status-warning'}`}>
                {saveIndicator}
              </span>
              <button
                type="button"
                onClick={() => setSearchOpen((value) => !value)}
                className="msm-btn-tertiary flex h-8 w-8 items-center justify-center rounded-md"
                aria-label="Suchen und ersetzen"
              >
                <Search className="h-4 w-4" />
              </button>
              {canWrite && (
                <button
                  type="button"
                  onClick={() => onSave(activeTab.path)}
                  disabled={activeTab.saveState === 'saving' || activeTab.saveState === 'clean' || activeTab.saveState === 'conflict'}
                  className="msm-btn-secondary inline-flex h-8 items-center gap-1.5 px-2.5 text-xs disabled:opacity-40"
                >
                  <Save className="h-3.5 w-3.5" /> Speichern
                </button>
              )}
            </div>
          </div>

          {activeTab.saveState === 'conflict' && (
            <div className="flex flex-wrap items-center gap-3 border-b border-status-warning/30 bg-status-warning/8 px-3 py-2 text-xs text-status-warning">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <p className="min-w-52 flex-1">Die Datei wurde außerhalb dieses Editors geändert. Dein lokaler Inhalt bleibt erhalten und wird nicht überschrieben.</p>
              <button type="button" className="msm-btn-secondary inline-flex h-8 items-center gap-1.5 px-2.5 text-xs" onClick={() => onReload(activeTab.path)}>
                <RotateCcw className="h-3.5 w-3.5" /> Server-Version neu laden
              </button>
            </div>
          )}

          {searchOpen && (
            <div className="grid gap-2 border-b border-outline-variant bg-surface-container-high/65 p-2.5 md:grid-cols-[minmax(160px,1fr)_minmax(160px,1fr)_auto]">
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-on-surface-variant" />
                <input
                  ref={findInputRef}
                  value={query}
                  onChange={(event) => { setQuery(event.target.value); setActiveMatch(0) }}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      selectMatch(event.shiftKey ? activeMatch - 1 : activeMatch + 1)
                    }
                    if (event.key === 'Escape') setSearchOpen(false)
                  }}
                  placeholder="Suchen…"
                  className="msm-input h-8 pl-8 pr-16 text-xs"
                />
                <span className="absolute right-2 top-1/2 -translate-y-1/2 font-mono text-[10px] text-on-surface-variant">{matches.length ? `${activeMatch + 1}/${matches.length}` : '0'}</span>
              </div>
              <div className="relative">
                <Replace className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-on-surface-variant" />
                <input
                  value={replacement}
                  onChange={(event) => setReplacement(event.target.value)}
                  placeholder="Ersetzen durch…"
                  disabled={!canWrite}
                  className="msm-input h-8 pl-8 text-xs disabled:opacity-50"
                />
              </div>
              <div className="flex flex-wrap items-center gap-1">
                <button type="button" onClick={() => setCaseSensitive((value) => !value)} className={`msm-btn-tertiary h-8 min-w-8 px-2 font-mono text-xs ${caseSensitive ? 'bg-primary/10 text-primary' : ''}`} aria-pressed={caseSensitive} title="Groß-/Kleinschreibung">Aa</button>
                <button type="button" onClick={() => selectMatch(activeMatch - 1)} disabled={!matches.length} className="msm-btn-tertiary flex h-8 w-8 items-center justify-center disabled:opacity-40" aria-label="Vorheriger Treffer"><ChevronUp className="h-3.5 w-3.5" /></button>
                <button type="button" onClick={() => selectMatch(activeMatch + 1)} disabled={!matches.length} className="msm-btn-tertiary flex h-8 w-8 items-center justify-center disabled:opacity-40" aria-label="Nächster Treffer"><ChevronDown className="h-3.5 w-3.5" /></button>
                <button type="button" onClick={replaceCurrent} disabled={!canWrite || !matches.length} className="msm-btn-secondary h-8 px-2.5 text-xs disabled:opacity-40">Ersetzen</button>
                <button type="button" onClick={replaceAll} disabled={!canWrite || !matches.length} className="msm-btn-secondary h-8 px-2.5 text-xs disabled:opacity-40">Alle ersetzen</button>
                <button type="button" onClick={() => selectMatch(0)} disabled={!matches.length} className="msm-btn-tertiary h-8 px-2.5 text-xs disabled:opacity-40">Alle finden ({matches.length})</button>
                <button type="button" onClick={() => setSearchOpen(false)} className="msm-btn-tertiary flex h-8 w-8 items-center justify-center" aria-label="Suche schließen"><X className="h-3.5 w-3.5" /></button>
              </div>
            </div>
          )}

          <div id="file-editor-panel" role="tabpanel" className="relative min-h-0 flex-1 overflow-hidden">
            {activeTab.loading ? (
              <div className="flex h-full min-h-80 items-center justify-center"><LoaderCircle className="h-6 w-6 animate-spin text-secondary" /></div>
            ) : (
              <CodeMirror
                key={activeTab.path}
                value={activeTab.content}
                onChange={handleEditorChange}
                onUpdate={updateCursor}
                onCreateEditor={handleCreateEditor}
                theme="dark"
                extensions={extensions}
                editable={canWrite}
                readOnly={!canWrite}
                basicSetup={EDITOR_BASIC_SETUP}
                height="100%"
              />
            )}
          </div>
          <footer className="flex min-h-8 flex-wrap items-center gap-x-4 gap-y-1 border-t border-outline-variant bg-surface-container-low/80 px-3 py-1 font-mono text-[10px] text-on-surface-variant">
            <span>Zeile {cursor.line}, Spalte {cursor.column}</span>
            <span>Auswahl {cursor.selected}</span>
            <span>{detectIndentation(activeTab.content)}</span>
            <span>UTF-8</span>
            <span>{activeTab.lineEnding === '\r\n' ? 'CRLF' : 'LF'}</span>
            <span>{detectLanguage(activeTab.path).toUpperCase()}</span>
            <span className="sm:hidden">{horizontalScrollHint}</span>
            <span className={`ml-auto inline-flex items-center gap-1.5 ${activeTab.saveState === 'clean' ? 'text-status-success' : activeTab.saveState === 'conflict' || activeTab.saveState === 'error' ? 'text-status-error' : 'text-status-warning'}`}>{saveIndicator}</span>
          </footer>
        </>
      ) : (
        <div className="flex min-h-[520px] flex-1 flex-col items-center justify-center gap-3 px-6 text-center text-on-surface-variant">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-outline-variant bg-surface-container-low"><FileCode2 className="h-5 w-5 text-secondary" /></div>
          <div><p className="text-sm font-medium text-on-surface">Datei zum Bearbeiten öffnen</p><p className="mt-1 max-w-sm text-xs">Mehrere Dateien bleiben als Tabs geöffnet. Änderungen werden nie stillschweigend verworfen.</p></div>
        </div>
      )}
    </section>
  )
}
