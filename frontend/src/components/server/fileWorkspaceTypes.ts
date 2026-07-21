export interface FileMetadata {
  size: number
  modified: number
  mode: string | null
  owner: string | null
  group: string | null
}

export interface FileEntry extends FileMetadata {
  name: string
  is_dir: boolean
}

export interface BrowseResponse {
  path: string
  entries: FileEntry[]
  exists: boolean
}

export interface SearchResult {
  path: string
  is_dir: boolean
}

export interface SearchResponse {
  query?: string
  q?: string
  truncated: boolean
  results: SearchResult[]
}

export interface ReadResponse extends FileMetadata {
  path: string
  name: string
  content: string
  revision: string
}

export type EditorSaveState = 'clean' | 'dirty' | 'saving' | 'error' | 'conflict'

export interface EditorTab extends FileMetadata {
  path: string
  content: string
  savedContent: string
  revision: string
  lineEnding: '\n' | '\r\n'
  loading: boolean
  saveState: EditorSaveState
}
