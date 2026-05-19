export type CompareMode = 'none' | 'saved' | 'backup'

export interface EditorTabWorkspace {
  path: string
  name: string
  content: string
  originalContent: string
  isDirty: boolean
  hasLoaded: boolean
  loadError: string | null
}

export interface ConfigEditorTabWorkspace extends EditorTabWorkspace {
  compareMode: CompareMode
  selectedBackupTimestamp: string
}

export interface FileManagerWorkspace {
  currentPath: string
  activeTabPath: string | null
  tabs: EditorTabWorkspace[]
}

export interface ConfigCenterWorkspace {
  activeMainTab: 'serverconfig' | 'files'
  activeRawTabPath: string | null
  rawTabs: ConfigEditorTabWorkspace[]
  serverConfigKnown: Record<string, unknown>
  serverConfigOriginalKnown: Record<string, unknown>
  serverConfigCustom: string
  serverConfigOriginalCustom: string
}

function normalizeEditorTabWorkspace<T extends EditorTabWorkspace>(tab: T): T {
  if (tab.isDirty) {
    return {
      ...tab,
      hasLoaded: true,
      loadError: null,
    }
  }

  return {
    ...tab,
    content: '',
    originalContent: '',
    hasLoaded: false,
    loadError: null,
  }
}

function hasServerConfigDraft(workspace: ConfigCenterWorkspace): boolean {
  return JSON.stringify(workspace.serverConfigKnown) !== JSON.stringify(workspace.serverConfigOriginalKnown)
    || workspace.serverConfigCustom !== workspace.serverConfigOriginalCustom
}

export function createEditorTabWorkspace(path: string, name: string): EditorTabWorkspace {
  return {
    path,
    name,
    content: '',
    originalContent: '',
    isDirty: false,
    hasLoaded: false,
    loadError: null,
  }
}

export function createConfigEditorTabWorkspace(path: string, name: string): ConfigEditorTabWorkspace {
  return {
    ...createEditorTabWorkspace(path, name),
    compareMode: 'none',
    selectedBackupTimestamp: '',
  }
}

export function createFileManagerWorkspace(): FileManagerWorkspace {
  return {
    currentPath: '',
    activeTabPath: null,
    tabs: [],
  }
}

export function createConfigCenterWorkspace(): ConfigCenterWorkspace {
  return {
    activeMainTab: 'serverconfig',
    activeRawTabPath: null,
    rawTabs: [],
    serverConfigKnown: {},
    serverConfigOriginalKnown: {},
    serverConfigCustom: '',
    serverConfigOriginalCustom: '',
  }
}

export function prepareFileManagerWorkspaceForStorage(workspace: FileManagerWorkspace): FileManagerWorkspace {
  const tabs = workspace.tabs.map((tab) => normalizeEditorTabWorkspace(tab))
  return {
    ...workspace,
    activeTabPath: tabs.some((tab) => tab.path === workspace.activeTabPath) ? workspace.activeTabPath : null,
    tabs,
  }
}

export function restoreFileManagerWorkspace(workspace: unknown): FileManagerWorkspace {
  const fallback = createFileManagerWorkspace()
  if (workspace == null || typeof workspace !== 'object') return fallback
  const ws = workspace as Record<string, unknown>
  const tabs = Array.isArray(ws.tabs)
    ? ws.tabs
        .filter((tab): tab is EditorTabWorkspace => {
          if (tab == null || typeof tab !== 'object') return false
          const t = tab as Record<string, unknown>
          if (typeof t.path !== 'string' || typeof t.name !== 'string') return false
          if (t.isDirty !== undefined && typeof t.isDirty !== 'boolean') return false
          if (t.isDirty === true && (typeof t.content !== 'string' || typeof t.originalContent !== 'string')) return false
          return true
        })
        .map((tab) => {
          const record = tab as unknown as Record<string, unknown>
          return normalizeEditorTabWorkspace({
            ...createEditorTabWorkspace(record.path as string, record.name as string),
            ...(record as Partial<EditorTabWorkspace>),
          })
        })
    : fallback.tabs
  return {
    currentPath: typeof ws.currentPath === 'string' ? ws.currentPath : fallback.currentPath,
    activeTabPath: tabs.some((tab) => tab.path === ws.activeTabPath) ? ws.activeTabPath as string : null,
    tabs,
  }
}

export function prepareConfigCenterWorkspaceForStorage(workspace: ConfigCenterWorkspace): ConfigCenterWorkspace {
  const rawTabs = workspace.rawTabs.map((tab) => normalizeEditorTabWorkspace(tab))
  const keepServerConfigDraft = hasServerConfigDraft(workspace)
  return {
    ...workspace,
    activeRawTabPath: rawTabs.some((tab) => tab.path === workspace.activeRawTabPath) ? workspace.activeRawTabPath : null,
    rawTabs,
    serverConfigKnown: keepServerConfigDraft ? workspace.serverConfigKnown : {},
    serverConfigOriginalKnown: keepServerConfigDraft ? workspace.serverConfigOriginalKnown : {},
    serverConfigCustom: keepServerConfigDraft ? workspace.serverConfigCustom : '',
    serverConfigOriginalCustom: keepServerConfigDraft ? workspace.serverConfigOriginalCustom : '',
  }
}

export function restoreConfigCenterWorkspace(workspace: unknown): ConfigCenterWorkspace {
  const fallback = createConfigCenterWorkspace()
  if (workspace == null || typeof workspace !== 'object') return fallback
  const ws = workspace as Record<string, unknown>
  const rawTabs = Array.isArray(ws.rawTabs)
    ? ws.rawTabs
        .filter((tab): tab is ConfigEditorTabWorkspace => {
          if (tab == null || typeof tab !== 'object') return false
          const t = tab as Record<string, unknown>
          if (typeof t.path !== 'string' || typeof t.name !== 'string') return false
          if (t.isDirty !== undefined && typeof t.isDirty !== 'boolean') return false
          if (t.isDirty === true && (typeof t.content !== 'string' || typeof t.originalContent !== 'string')) return false
          if (t.compareMode !== undefined && t.compareMode !== 'none' && t.compareMode !== 'saved' && t.compareMode !== 'backup') return false
          if (t.selectedBackupTimestamp !== undefined && typeof t.selectedBackupTimestamp !== 'string') return false
          return true
        })
        .map((tab) => ({
          ...normalizeEditorTabWorkspace({ ...createConfigEditorTabWorkspace(tab.path, tab.name), ...tab }),
          compareMode: tab.compareMode ?? 'none',
          selectedBackupTimestamp: tab.selectedBackupTimestamp ?? '',
        }))
    : fallback.rawTabs
  const serverConfigKnown = ws.serverConfigKnown && typeof ws.serverConfigKnown === 'object' && !Array.isArray(ws.serverConfigKnown) ? ws.serverConfigKnown as Record<string, unknown> : fallback.serverConfigKnown
  const serverConfigOriginalKnown = ws.serverConfigOriginalKnown && typeof ws.serverConfigOriginalKnown === 'object' && !Array.isArray(ws.serverConfigOriginalKnown)
    ? ws.serverConfigOriginalKnown as Record<string, unknown>
    : fallback.serverConfigOriginalKnown

  return {
    activeMainTab: ws.activeMainTab === 'files' ? 'files' : 'serverconfig',
    activeRawTabPath: rawTabs.some((tab) => tab.path === ws.activeRawTabPath) ? ws.activeRawTabPath as string : null,
    rawTabs,
    serverConfigKnown,
    serverConfigOriginalKnown,
    serverConfigCustom: typeof ws.serverConfigCustom === 'string' ? ws.serverConfigCustom : fallback.serverConfigCustom,
    serverConfigOriginalCustom: typeof ws.serverConfigOriginalCustom === 'string'
      ? ws.serverConfigOriginalCustom
      : fallback.serverConfigOriginalCustom,
  }
}
