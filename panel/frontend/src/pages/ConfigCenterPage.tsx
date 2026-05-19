import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Editor, { DiffEditor } from '@monaco-editor/react'
import { AlertTriangle, FileCode2, FileText, FolderOpen, History, RotateCcw, Save, Settings2, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { ApiError, backupsApi, configApi, filesApi, serversApi } from '@/lib/api'
import type { ConfigQuickFile, FileContent, RecentFileEntry, ServerConfigData, ServersData } from '@/lib/types'
import { CONFIG_FIELD_HELP, CONFIG_GROUP_LABELS } from '@/lib/config-schema'
import { bindMonacoSaveShortcut, useSaveShortcut } from '@/hooks/useSaveShortcut'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/hooks/useAuth'
import { hasPermission, UI_PERMISSIONS } from '@/lib/permissions'
import { useUiLanguage } from '@/lib/ui-language'
import { loadServerWorkspace, saveServerWorkspace } from '@/lib/workspace'
import {
  createConfigCenterWorkspace,
  createConfigEditorTabWorkspace,
  createFileManagerWorkspace,
  prepareConfigCenterWorkspaceForStorage,
  restoreConfigCenterWorkspace,
  restoreFileManagerWorkspace,
  type CompareMode,
} from '@/lib/workspaces'

const CONFIG_CENTER_WORKSPACE_SCOPE = 'config-page'
const FILE_MANAGER_WORKSPACE_SCOPE = 'files-page'

function detectEditorLanguage(path: string | null): string {
  const ext = path?.split('.').pop()?.toLowerCase() ?? ''
  if (ext === 'xml') return 'xml'
  if (ext === 'json') return 'json'
  if (ext === 'ini' || ext === 'cfg') return 'ini'
  if (ext === 'sh') return 'shell'
  return 'plaintext'
}

function formatModified(timestamp: number, language: 'en' | 'de'): string {
  const date = new Date(timestamp * 1000)
  return new Intl.DateTimeFormat(language === 'de' ? 'de-DE' : 'en-US', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

function getQuickFileBadge(entry: ConfigQuickFile, t: any) {
  return entry.exists ? (
    <Badge variant="success">{t.exists}</Badge>
  ) : (
    <Badge variant="secondary">{t.missing}</Badge>
  )
}

function getFieldText(name: string, language: 'en' | 'de') {
  const field = CONFIG_FIELD_HELP[name]
  if (!field) {
    return {
      label: name,
      description: name,
      placeholder: '',
    }
  }
  return {
    label: field.label[language],
    description: field.description[language],
    placeholder: field.placeholder?.[language] ?? '',
  }
}

function toTextValue(value: unknown): string {
  if (value == null) return ''
  if (Array.isArray(value)) return value.join('\n')
  return String(value)
}

function toBooleanValue(value: unknown): boolean {
  return value === true
}

function resolveNextValue<T>(value: T | ((previous: T) => T), previous: T): T {
  return typeof value === 'function' ? (value as (previous: T) => T)(previous) : value
}

export default function ConfigCenterPage() {
  const { user } = useAuth()
  const { copy, language } = useUiLanguage()
  const t = copy.configCenter
  const commonFiles = copy.files
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [workspace, setWorkspace] = useState(() => createConfigCenterWorkspace())
  const rawEditorContentRef = useRef('')
  const serverConfigCustomRef = useRef('')
  const serverConfigKnownRef = useRef<Record<string, unknown>>({})
  const currentServerRef = useRef<string | null>(null)
  const selectedFilePathRef = useRef<string | null>(null)
  const activeRawTab = useMemo(
    () => workspace.rawTabs.find((tab) => tab.path === workspace.activeRawTabPath) ?? null,
    [workspace.activeRawTabPath, workspace.rawTabs],
  )
  const selectedFilePath = activeRawTab?.path ?? null
  const editorContent = activeRawTab?.content ?? ''
  const originalContent = activeRawTab?.originalContent ?? ''
  const compareMode: CompareMode = activeRawTab?.compareMode ?? 'none'
  const selectedBackupTimestamp = activeRawTab?.selectedBackupTimestamp ?? ''
  const serverConfigKnown = workspace.serverConfigKnown
  const serverConfigOriginalKnown = workspace.serverConfigOriginalKnown
  const serverConfigCustom = workspace.serverConfigCustom
  const serverConfigOriginalCustom = workspace.serverConfigOriginalCustom

  const { data: serversData, isLoading: isServersLoading } = useQuery<ServersData>({
    queryKey: ['servers'],
    queryFn: serversApi.list,
  })
  const hasCurrentServer = Boolean(serversData?.current)
  const currentServer = serversData?.current ?? null
  const canWriteFiles = hasPermission(user, UI_PERMISSIONS.filesWrite)
  const canViewBackups = hasPermission(user, UI_PERMISSIONS.backupsView)
  const canRestoreBackups = hasPermission(user, UI_PERMISSIONS.backupsRestore)

  useEffect(() => {
    currentServerRef.current = currentServer
  }, [currentServer])

  useEffect(() => {
    selectedFilePathRef.current = selectedFilePath
  }, [selectedFilePath])

  useEffect(() => {
    rawEditorContentRef.current = editorContent
  }, [editorContent])

  useEffect(() => {
    serverConfigCustomRef.current = serverConfigCustom
  }, [serverConfigCustom])

  useEffect(() => {
    serverConfigKnownRef.current = serverConfigKnown
  }, [serverConfigKnown])

  useEffect(() => {
    if (!currentServer) {
      setWorkspace(createConfigCenterWorkspace())
      return
    }
    setWorkspace(restoreConfigCenterWorkspace(
      loadServerWorkspace(CONFIG_CENTER_WORKSPACE_SCOPE, currentServer, createConfigCenterWorkspace()),
    ))
  }, [currentServer])

  useEffect(() => {
    if (!currentServer) return
    saveServerWorkspace(CONFIG_CENTER_WORKSPACE_SCOPE, currentServer, prepareConfigCenterWorkspaceForStorage(workspace))
  }, [currentServer, workspace])

  const setServerConfigKnown = useCallback((value: Record<string, unknown> | ((previous: Record<string, unknown>) => Record<string, unknown>)) => {
    setWorkspace((prev) => ({
      ...prev,
      serverConfigKnown: resolveNextValue(value, prev.serverConfigKnown),
    }))
  }, [])

  const setServerConfigCustom = useCallback((value: string | ((previous: string) => string)) => {
    setWorkspace((prev) => ({
      ...prev,
      serverConfigCustom: resolveNextValue(value, prev.serverConfigCustom),
    }))
  }, [])

  const openRawTab = useCallback((path: string, label?: string) => {
    const name = label ?? path.split('/').pop() ?? path
    setWorkspace((prev) => {
      const existing = prev.rawTabs.find((tab) => tab.path === path)
      if (existing) {
        return {
          ...prev,
          activeMainTab: 'files',
          activeRawTabPath: path,
          rawTabs: existing.loadError
            ? prev.rawTabs.map((tab) => (tab.path === path ? { ...tab, hasLoaded: false, loadError: null } : tab))
            : prev.rawTabs,
        }
      }
      return {
        ...prev,
        activeMainTab: 'files',
        activeRawTabPath: path,
        rawTabs: [...prev.rawTabs, createConfigEditorTabWorkspace(path, name)],
      }
    })
  }, [])

  const setEditorContentForPath = useCallback((path: string, value: string | ((previous: string) => string)) => {
    setWorkspace((prev) => ({
      ...prev,
      rawTabs: prev.rawTabs.map((tab) => {
        if (tab.path !== path) return tab
        const nextContent = resolveNextValue(value, tab.content)
        return {
          ...tab,
          content: nextContent,
          isDirty: nextContent !== tab.originalContent,
          hasLoaded: true,
          loadError: null,
        }
      }),
    }))
  }, [])

  const setEditorContent = useCallback((value: string | ((previous: string) => string)) => {
    if (!selectedFilePath) return
    setEditorContentForPath(selectedFilePath, value)
  }, [selectedFilePath, setEditorContentForPath])

  const setCompareMode = useCallback((value: CompareMode) => {
    if (!selectedFilePath) return
    setWorkspace((prev) => ({
      ...prev,
      rawTabs: prev.rawTabs.map((tab) => (
        tab.path === selectedFilePath
          ? { ...tab, compareMode: value, selectedBackupTimestamp: value === 'backup' ? tab.selectedBackupTimestamp : '' }
          : tab
      )),
    }))
  }, [selectedFilePath])

  const setSelectedBackupTimestamp = useCallback((value: string) => {
    if (!selectedFilePath) return
    setWorkspace((prev) => ({
      ...prev,
      rawTabs: prev.rawTabs.map((tab) => (
        tab.path === selectedFilePath ? { ...tab, selectedBackupTimestamp: value } : tab
      )),
    }))
  }, [selectedFilePath])

  const setOriginalContent = useCallback((value: string | ((previous: string) => string)) => {
    if (!selectedFilePath) return
    setWorkspace((prev) => ({
      ...prev,
      rawTabs: prev.rawTabs.map((tab) => {
        if (tab.path !== selectedFilePath) return tab
        const nextOriginal = resolveNextValue(value, tab.originalContent)
        return {
          ...tab,
          originalContent: nextOriginal,
          isDirty: tab.content !== nextOriginal,
          hasLoaded: true,
          loadError: null,
        }
      }),
    }))
  }, [selectedFilePath])

  const overviewQuery = useQuery({
    queryKey: ['config-overview', currentServer],
    queryFn: configApi.overview,
    enabled: hasCurrentServer,
    staleTime: 30_000,
  })

  const serverConfigQuery = useQuery<ServerConfigData>({
    queryKey: ['config-serverconfig', currentServer],
    queryFn: configApi.getServerConfig,
    enabled: hasCurrentServer,
  })

  const backupsQuery = useQuery({
    queryKey: ['backups', currentServer],
    queryFn: backupsApi.list,
    enabled: hasCurrentServer && canViewBackups,
  })

  const fileQuery = useQuery<FileContent>({
    queryKey: ['config-file', currentServer, selectedFilePath],
    queryFn: () => filesApi.readContent(selectedFilePath!),
    enabled: hasCurrentServer && selectedFilePath !== null && activeRawTab !== null && !activeRawTab.hasLoaded && activeRawTab.loadError === null,
    retry: false,
    refetchOnWindowFocus: false,
  })

  const backupContentQuery = useQuery({
    queryKey: ['config-backup-content', currentServer, selectedBackupTimestamp, selectedFilePath],
    queryFn: () => backupsApi.readFileContent(selectedBackupTimestamp, selectedFilePath!),
    enabled: false,
    retry: false,
  })
  const { refetch: refetchBackupContent } = backupContentQuery

  const invalidateRelatedFileQueries = useCallback((server: string | null, path: string) => {
    if (!server) return
    void queryClient.invalidateQueries({ queryKey: ['config-file', server, path] })
    void queryClient.invalidateQueries({ queryKey: ['files', server, 'content', path] })
    void queryClient.invalidateQueries({ queryKey: ['config-overview', server] })
    void queryClient.invalidateQueries({ queryKey: ['files', server, 'list'] })
  }, [queryClient])

  const invalidateServerConfigQueries = useCallback((server: string | null) => {
    if (!server) return
    void queryClient.invalidateQueries({ queryKey: ['config-overview', server] })
    void queryClient.invalidateQueries({ queryKey: ['config-serverconfig', server] })
  }, [queryClient])

  useEffect(() => {
    if (fileQuery.data) {
      setWorkspace((prev) => ({
        ...prev,
        rawTabs: prev.rawTabs.map((tab) => (
          tab.path === fileQuery.data.path
            ? {
                ...tab,
                content: fileQuery.data.content ?? '',
                originalContent: fileQuery.data.content ?? '',
                isDirty: false,
                hasLoaded: true,
                loadError: null,
              }
            : tab
        )),
      }))
    }
  }, [fileQuery.data])

  useEffect(() => {
    if (fileQuery.error instanceof ApiError && fileQuery.error.status === 404) {
      setOriginalContent('')
      setEditorContent('')
    }
  }, [fileQuery.error])

  useEffect(() => {
    if (serverConfigQuery.data) {
      setWorkspace((prev) => {
        const hasDraft =
          JSON.stringify(prev.serverConfigKnown) !== JSON.stringify(prev.serverConfigOriginalKnown)
          || prev.serverConfigCustom !== prev.serverConfigOriginalCustom
        if (hasDraft) return prev
        return {
          ...prev,
          serverConfigKnown: serverConfigQuery.data.known,
          serverConfigOriginalKnown: serverConfigQuery.data.known,
          serverConfigCustom: serverConfigQuery.data.custom_raw,
          serverConfigOriginalCustom: serverConfigQuery.data.custom_raw,
        }
      })
    }
  }, [serverConfigQuery.data])

  const saveServerConfigMutation = useMutation({
    mutationFn: (payload: { server: string; known: Record<string, unknown>; custom_raw: string }) =>
      configApi.saveServerConfig({ known: payload.known, custom_raw: payload.custom_raw }),
    onSuccess: (data, variables) => {
      const isCurrentServer = variables.server === currentServerRef.current
      if (isCurrentServer) {
        toast.success(t.serverConfigSaved)
        setWorkspace((prev) => {
          const knownChangedSinceSave = JSON.stringify(prev.serverConfigKnown) !== JSON.stringify(variables.known)
          const customChangedSinceSave = prev.serverConfigCustom !== variables.custom_raw
          return {
            ...prev,
            serverConfigKnown: knownChangedSinceSave ? prev.serverConfigKnown : data.known,
            serverConfigOriginalKnown: data.known,
            serverConfigCustom: customChangedSinceSave ? prev.serverConfigCustom : data.custom_raw,
            serverConfigOriginalCustom: data.custom_raw,
            rawTabs: prev.rawTabs.map((tab) => (
              tab.path === data.path
                ? {
                    ...tab,
                    content: tab.isDirty ? tab.content : data.raw,
                    originalContent: data.raw,
                    isDirty: tab.isDirty ? tab.content !== data.raw : false,
                    hasLoaded: true,
                    loadError: null,
                  }
                : tab
            )),
          }
        })
      }
      invalidateServerConfigQueries(variables.server)
      invalidateRelatedFileQueries(variables.server, data.path)
    },
    onError: (error: unknown) => {
      toast.error(error instanceof ApiError ? error.message : t.serverConfigSaveFailed)
    },
  })

  const saveFileMutation = useMutation({
    mutationFn: ({ path, content }: { path: string; content: string; server: string }) => filesApi.writeContent(path, content),
    onSuccess: (_data, variables) => {
      const isCurrentServer = variables.server === currentServerRef.current
      if (isCurrentServer) {
        toast.success(commonFiles.fileSaved)
        setWorkspace((prev) => ({
          ...prev,
          rawTabs: prev.rawTabs.map((tab) => (
            tab.path === variables.path
              ? {
                  ...tab,
                  originalContent: variables.content,
                  isDirty: tab.content !== variables.content,
                  hasLoaded: true,
                  loadError: null,
                }
              : tab
          )),
        }))
      }
      invalidateRelatedFileQueries(variables.server, variables.path)
    },
    onError: (error: unknown) => {
      const detail = error instanceof ApiError ? error.message : undefined
      toast.error(commonFiles.fileSaveFailed(detail))
    },
  })

  const restoreFileMutation = useMutation({
    mutationFn: ({ path, timestamp }: { path: string; timestamp: string; server: string }) => backupsApi.restoreFile(timestamp, path),
    onSuccess: (_data, variables) => {
      const isCurrentServer = variables.server === currentServerRef.current
      if (isCurrentServer) {
        toast.success(t.restoreSuccess)
        setWorkspace((prev) => ({
          ...prev,
          rawTabs: prev.rawTabs.map((tab) => (
            tab.path === variables.path ? { ...tab, hasLoaded: false, loadError: null } : tab
          )),
        }))
      }
      invalidateRelatedFileQueries(variables.server, variables.path)
    },
    onError: (error: unknown) => {
      toast.error(error instanceof ApiError ? error.message : t.restoreFailed)
    },
  })

  const quickFiles = overviewQuery.data?.quick_files ?? []
  const quickDirectories = overviewQuery.data?.quick_directories ?? []
  const recentFiles = overviewQuery.data?.recent_files ?? []
  const backupRuns = backupsQuery.data?.runs ?? []
  const fileDirty = editorContent !== originalContent
  const serverConfigDirty = JSON.stringify(serverConfigKnown) !== JSON.stringify(serverConfigOriginalKnown) || serverConfigCustom !== serverConfigOriginalCustom
  const supportsBackupRestore = Boolean(
    selectedFilePath && (
      selectedFilePath.startsWith('serverfiles/ConanSandbox/Saved/')
      || ['config.ini', 'workshop.cfg', 'mod_timestamps.json'].includes(selectedFilePath)
    ),
  )

  const closeRawTab = useCallback((path: string) => {
    const tab = workspace.rawTabs.find((entry) => entry.path === path)
    if (!tab) return
    if (tab.isDirty && !confirm(commonFiles.unsavedChanges)) return

    setWorkspace((prev) => {
      const remainingTabs = prev.rawTabs.filter((entry) => entry.path !== path)
      const activeRawTabPath = prev.activeRawTabPath === path
        ? (remainingTabs.length > 0 ? remainingTabs[remainingTabs.length - 1].path : null)
        : prev.activeRawTabPath
      return {
        ...prev,
        activeRawTabPath,
        rawTabs: remainingTabs,
      }
    })
  }, [commonFiles.unsavedChanges, workspace.rawTabs])

  const openQuickDirectory = useCallback((path: string) => {
    if (!currentServer) return
    const nextWorkspace = {
      ...restoreFileManagerWorkspace(
        loadServerWorkspace(FILE_MANAGER_WORKSPACE_SCOPE, currentServer, createFileManagerWorkspace()),
      ),
      currentPath: path,
    }
    saveServerWorkspace(FILE_MANAGER_WORKSPACE_SCOPE, currentServer, nextWorkspace)
    navigate('/files')
  }, [currentServer, navigate])

  const saveActiveRawTab = useCallback(() => {
    if (!selectedFilePath || !fileDirty || saveFileMutation.isPending || !canWriteFiles || !currentServer) return
    saveFileMutation.mutate({ path: selectedFilePath, content: rawEditorContentRef.current, server: currentServer })
  }, [canWriteFiles, currentServer, fileDirty, saveFileMutation, selectedFilePath])

  const saveCurrentContext = useCallback(() => {
    if (workspace.activeMainTab === 'serverconfig') {
      if (serverConfigDirty && !saveServerConfigMutation.isPending && canWriteFiles && currentServer) {
        saveServerConfigMutation.mutate({
          server: currentServer,
          known: serverConfigKnownRef.current,
          custom_raw: serverConfigCustomRef.current,
        })
      }
      return
    }

    saveActiveRawTab()
  }, [
    canWriteFiles,
    currentServer,
    saveActiveRawTab,
    saveServerConfigMutation,
    serverConfigDirty,
    workspace.activeMainTab,
  ])

  const saveCurrentContextRef = useRef(saveCurrentContext)

  useEffect(() => {
    saveCurrentContextRef.current = saveCurrentContext
  }, [saveCurrentContext])

  const diffEditorListenerRef = useRef<{ dispose: () => void } | null>(null)

  useEffect(() => {
    return () => {
      diffEditorListenerRef.current?.dispose()
      diffEditorListenerRef.current = null
    }
  }, [])

  const handleDiffEditorMount = useCallback((editor: Parameters<typeof bindMonacoSaveShortcut>[0], monaco: Parameters<typeof bindMonacoSaveShortcut>[1]) => {
    diffEditorListenerRef.current?.dispose()
    diffEditorListenerRef.current = null
    bindMonacoSaveShortcut(editor, monaco, () => saveCurrentContextRef.current())
    const modifiedEditor = (editor as { getModifiedEditor: () => { getValue: () => string; onDidChangeModelContent: (listener: () => void) => { dispose: () => void } } }).getModifiedEditor()
    diffEditorListenerRef.current = modifiedEditor.onDidChangeModelContent(() => {
      const activePath = selectedFilePathRef.current
      if (!activePath) return
      const nextValue = modifiedEditor.getValue()
      rawEditorContentRef.current = nextValue
      setEditorContentForPath(activePath, nextValue)
    })
  }, [setEditorContentForPath])

  useSaveShortcut({
    enabled: hasCurrentServer,
    allowWhileTyping: true,
    onSave: saveCurrentContext,
  })

  useEffect(() => {
    if (compareMode === 'backup' && !supportsBackupRestore) {
      setCompareMode('none')
    }
  }, [compareMode, setCompareMode, supportsBackupRestore])

  useEffect(() => {
    if (
      hasCurrentServer
      && compareMode === 'backup'
      && selectedBackupTimestamp.length > 0
      && selectedFilePath !== null
      && supportsBackupRestore
      && canViewBackups
    ) {
      void refetchBackupContent()
    }
  }, [
    canViewBackups,
    compareMode,
    hasCurrentServer,
    refetchBackupContent,
    selectedBackupTimestamp,
    selectedFilePath,
    supportsBackupRestore,
  ])

  const compareOriginal = useMemo(() => {
    if (compareMode === 'saved') return originalContent
    if (compareMode === 'backup') return backupContentQuery.data?.content ?? ''
    return ''
  }, [backupContentQuery.data?.content, compareMode, originalContent])

  if (!hasCurrentServer && !isServersLoading) {
    return (
      <Alert>
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>{t.noServerTitle}</AlertTitle>
        <AlertDescription>{t.noServerDescription}</AlertDescription>
      </Alert>
    )
  }

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-center gap-3">
        <Settings2 className="h-5 w-5 text-accent" />
        <div>
          <h1 className="text-lg font-semibold">{t.title}</h1>
          <p className="text-sm text-muted-foreground">{t.description}</p>
        </div>
      </div>

      <Tabs
        value={workspace.activeMainTab}
        onValueChange={(value) => setWorkspace((prev) => ({ ...prev, activeMainTab: value as 'serverconfig' | 'files' }))}
        className="space-y-4"
      >
        <TabsList>
          <TabsTrigger value="serverconfig">{t.serverConfigTab}</TabsTrigger>
          <TabsTrigger value="files">{t.filesTab}</TabsTrigger>
        </TabsList>

        <TabsContent value="serverconfig" className="space-y-4">
          <Card>
            <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="space-y-1">
                <CardTitle>{t.serverConfigTitle}</CardTitle>
                <CardDescription>{t.serverConfigDescription}</CardDescription>
                {serverConfigQuery.data && (
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <span className="font-mono">{serverConfigQuery.data.path}</span>
                    <a className="text-accent underline-offset-4 hover:underline" href={serverConfigQuery.data.schema_source} target="_blank" rel="noreferrer">
                      {t.openDocs}
                    </a>
                  </div>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="outline" disabled={!serverConfigDirty || saveServerConfigMutation.isPending || !canWriteFiles} onClick={() => {
                  setServerConfigKnown(serverConfigOriginalKnown)
                  setServerConfigCustom(serverConfigOriginalCustom)
                }}>
                  <RotateCcw className="mr-2 h-4 w-4" />
                  {t.reset}
                </Button>
                <Button
                  type="button"
                  disabled={!serverConfigDirty || saveServerConfigMutation.isPending || !canWriteFiles}
                  onClick={() => currentServer && saveServerConfigMutation.mutate({
                    server: currentServer,
                    known: serverConfigKnownRef.current,
                    custom_raw: serverConfigCustomRef.current,
                  })}
                >
                  <Save className="mr-2 h-4 w-4" />
                  {t.saveServerConfig}
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-6">
              {serverConfigQuery.isPending ? (
                <div className="space-y-4">
                  <Skeleton className="h-28 w-full" />
                  <Skeleton className="h-28 w-full" />
                  <Skeleton className="h-28 w-full" />
                </div>
              ) : (
                <>
                  {!canWriteFiles && (
                    <Alert>
                      <AlertTriangle className="h-4 w-4" />
                      <AlertTitle>{t.readOnlyTitle}</AlertTitle>
                      <AlertDescription>{t.readOnlyDescription}</AlertDescription>
                    </Alert>
                  )}
                  {serverConfigQuery.data?.groups.map((group) => {
                    const fields = serverConfigQuery.data.fields.filter((field) => field.group === group.key)
                    const groupLabel = CONFIG_GROUP_LABELS[group.key]?.[language] ?? group.title
                    return (
                      <Card key={group.key} className="border-border/70">
                        <CardHeader>
                          <CardTitle className="text-base">{groupLabel}</CardTitle>
                        </CardHeader>
                        <CardContent className="grid gap-4 md:grid-cols-2">
                          {fields.map((field) => {
                            const help = getFieldText(field.name, language)
                            const value = serverConfigKnown[field.name]
                            if (field.kind === 'flag01' || field.kind === 'boolean') {
                              return (
                                <label key={field.name} className="flex items-start gap-3 rounded-lg border border-border/60 p-3">
                                  <input
                                    type="checkbox"
                                    className="mt-1 h-4 w-4 rounded border-border accent-[var(--accent)]"
                                    checked={toBooleanValue(value)}
                                    onChange={(event) => setServerConfigKnown((prev) => ({ ...prev, [field.name]: event.target.checked }))}
                                  />
                                  <div className="space-y-1">
                                    <div className="text-sm font-medium">{help.label}</div>
                                    <p className="text-xs text-muted-foreground">{help.description}</p>
                                  </div>
                                </label>
                              )
                            }

                            if (field.kind === 'string_array') {
                              return (
                                <div key={field.name} className="space-y-2 md:col-span-2">
                                  <Label>{help.label}</Label>
                                  <p className="text-xs text-muted-foreground">{help.description}</p>
                                  <textarea
                                    className="min-h-28 w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none ring-offset-background focus-visible:ring-2 focus-visible:ring-ring"
                                    value={Array.isArray(value) ? value.join('\n') : ''}
                                    onChange={(event) =>
                                      setServerConfigKnown((prev) => ({
                                        ...prev,
                                        [field.name]: event.target.value
                                          .split(/\r?\n/)
                                          .map((item) => item.trim())
                                          .filter(Boolean),
                                      }))
                                    }
                                  />
                                </div>
                              )
                            }

                            return (
                              <div key={field.name} className="space-y-2">
                                <Label>{help.label}</Label>
                                <p className="text-xs text-muted-foreground">{help.description}</p>
                                <Input
                                  type={field.kind === 'int' || field.kind === 'float' ? 'number' : 'text'}
                                  step={field.kind === 'float' ? 'any' : '1'}
                                  placeholder={help.placeholder}
                                  value={toTextValue(value)}
                                  onChange={(event) => setServerConfigKnown((prev) => ({ ...prev, [field.name]: event.target.value }))}
                                />
                              </div>
                            )
                          })}
                        </CardContent>
                      </Card>
                    )
                  })}

                  <div className="space-y-2">
                    <Label>{t.customBlockTitle}</Label>
                    <p className="text-xs text-muted-foreground">{t.customBlockDescription}</p>
                    <div className="overflow-hidden rounded-md border border-border">
                      <Editor
                        height="240px"
                        language="plaintext"
                        theme="vs-dark"
                        value={serverConfigCustom}
                        onChange={(value) => {
                          const nextValue = value ?? ''
                          serverConfigCustomRef.current = nextValue
                          setServerConfigCustom(nextValue)
                        }}
                        onMount={(editor, monaco) => bindMonacoSaveShortcut(editor, monaco, () => saveCurrentContextRef.current())}
                        options={{
                          minimap: { enabled: false },
                          fontSize: 13,
                          wordWrap: 'on',
                          scrollBeyondLastLine: false,
                          automaticLayout: true,
                        }}
                      />
                    </div>
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="files" className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <FileCode2 className="h-4 w-4 text-accent" />
                    {t.quickFilesTitle}
                  </CardTitle>
                  <CardDescription>{t.quickFilesDescription}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-2">
                  {overviewQuery.isPending ? (
                    <>
                      <Skeleton className="h-10 w-full" />
                      <Skeleton className="h-10 w-full" />
                      <Skeleton className="h-10 w-full" />
                    </>
                  ) : quickFiles.map((entry) => (
                    <button
                      key={entry.key}
                      type="button"
                      onClick={() => openRawTab(entry.path, entry.label)}
                      className={`flex w-full items-center justify-between rounded-lg border px-3 py-2 text-left transition-colors ${
                        selectedFilePath === entry.path ? 'border-accent bg-accent/5' : 'border-border hover:bg-accent/5'
                      }`}
                    >
                      <div>
                        <div className="text-sm font-medium">{entry.label}</div>
                        <div className="text-[11px] text-muted-foreground font-mono">{entry.path}</div>
                      </div>
                      {getQuickFileBadge(entry, t)}
                    </button>
                  ))}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <FolderOpen className="h-4 w-4 text-accent" />
                    {t.quickDirectoriesTitle}
                  </CardTitle>
                  <CardDescription>{t.quickDirectoriesDescription}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-2">
                  {quickDirectories.map((entry) => (
                    <button
                      key={entry.key}
                      type="button"
                      disabled={!entry.exists}
                      onClick={() => openQuickDirectory(entry.path)}
                      className={`flex w-full items-center justify-between rounded-lg border px-3 py-2 text-left transition-colors ${
                        entry.exists ? 'border-border hover:bg-accent/5' : 'cursor-not-allowed border-border/60 bg-muted/40 text-muted-foreground'
                      }`}
                    >
                      <div>
                        <div className="text-sm font-medium">{entry.label}</div>
                        <div className="text-[11px] text-muted-foreground font-mono">{entry.path || '.'}</div>
                      </div>
                      <Badge variant={entry.exists ? 'success' : 'secondary'}>
                        {entry.exists ? t.openFolder : t.missing}
                      </Badge>
                    </button>
                  ))}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <History className="h-4 w-4 text-accent" />
                    {t.recentTitle}
                  </CardTitle>
                  <CardDescription>{t.recentDescription}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-2">
                  {recentFiles.map((entry: RecentFileEntry) => (
                    <button
                      key={entry.path}
                      type="button"
                      onClick={() => openRawTab(entry.path, entry.name)}
                      className={`flex w-full items-start justify-between rounded-lg border px-3 py-2 text-left transition-colors ${
                        selectedFilePath === entry.path ? 'border-accent bg-accent/5' : 'border-border hover:bg-accent/5'
                      }`}
                    >
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium">{entry.name}</div>
                        <div className="truncate text-[11px] text-muted-foreground font-mono">{entry.path}</div>
                      </div>
                      <div className="ml-3 shrink-0 text-[11px] text-muted-foreground">
                        {formatModified(entry.modified, language)}
                      </div>
                    </button>
                  ))}
                  {recentFiles.length === 0 && (
                    <div className="text-sm text-muted-foreground">{t.noRecentFiles}</div>
                  )}
                </CardContent>
              </Card>
            </div>

            <Card className="min-w-0">
              <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="space-y-1">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <FileText className="h-4 w-4 text-accent" />
                    {selectedFilePath ?? t.selectFileTitle}
                  </CardTitle>
                  <CardDescription>{t.fileEditorDescription}</CardDescription>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button type="button" variant="outline" disabled={!fileDirty || saveFileMutation.isPending || !canWriteFiles} onClick={() => setEditorContent(originalContent)}>
                    <RotateCcw className="mr-2 h-4 w-4" />
                    {t.reset}
                  </Button>
                  <Button type="button" disabled={!selectedFilePath || saveFileMutation.isPending || !fileDirty || !canWriteFiles} onClick={saveActiveRawTab}>
                    <Save className="mr-2 h-4 w-4" />
                    {commonFiles.save}
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                {workspace.rawTabs.length > 0 && (
                  <div className="flex items-center gap-1 overflow-x-auto rounded-md border border-border/70 bg-muted/20 p-1">
                    {workspace.rawTabs.map((tab) => (
                      <button
                        key={tab.path}
                        type="button"
                        onClick={() => setWorkspace((prev) => ({ ...prev, activeRawTabPath: tab.path }))}
                        className={`flex items-center gap-2 rounded-md px-2.5 py-1 text-xs transition-colors ${
                          workspace.activeRawTabPath === tab.path
                            ? 'bg-card text-foreground shadow-sm'
                            : 'text-muted-foreground hover:bg-accent/5 hover:text-foreground'
                        }`}
                        title={tab.path}
                      >
                        <span className="truncate">{tab.name}</span>
                        {tab.isDirty && <span className="text-amber-400">●</span>}
                        <span
                          role="button"
                          tabIndex={0}
                          className="rounded p-0.5 hover:bg-background"
                          onClick={(event) => {
                            event.stopPropagation()
                            closeRawTab(tab.path)
                          }}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') {
                              event.preventDefault()
                              event.stopPropagation()
                              closeRawTab(tab.path)
                            }
                          }}
                        >
                          <X className="h-3 w-3" />
                        </span>
                      </button>
                    ))}
                  </div>
                )}

                {selectedFilePath === 'config.ini' && (
                  <Alert>
                    <AlertTriangle className="h-4 w-4" />
                    <AlertTitle>{t.configIniHintTitle}</AlertTitle>
                    <AlertDescription>{t.configIniHintDescription}</AlertDescription>
                  </Alert>
                )}

                <div className="flex flex-wrap items-center gap-2">
                  <Button type="button" variant={compareMode === 'none' ? 'default' : 'outline'} size="sm" onClick={() => setCompareMode('none')}>
                    {t.compareNone}
                  </Button>
                  <Button type="button" variant={compareMode === 'saved' ? 'default' : 'outline'} size="sm" onClick={() => setCompareMode('saved')} disabled={!selectedFilePath}>
                    {t.compareSaved}
                  </Button>
                  <Button
                    type="button"
                    variant={compareMode === 'backup' ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => setCompareMode('backup')}
                    disabled={!selectedFilePath || !supportsBackupRestore || !canViewBackups}
                  >
                    {t.compareBackup}
                  </Button>
                  {compareMode === 'backup' && supportsBackupRestore && canViewBackups && (
                    <>
                      <select
                        className="rounded-md border border-border bg-background px-3 py-2 text-sm"
                        value={selectedBackupTimestamp}
                        onChange={(event) => setSelectedBackupTimestamp(event.target.value)}
                      >
                        <option value="">{t.selectBackup}</option>
                        {backupRuns.map((run) => (
                          <option key={run.timestamp} value={run.timestamp}>{run.timestamp}</option>
                        ))}
                      </select>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={!selectedBackupTimestamp || !selectedFilePath || restoreFileMutation.isPending || !canRestoreBackups}
                        onClick={() => currentServer && restoreFileMutation.mutate({ path: selectedFilePath!, timestamp: selectedBackupTimestamp, server: currentServer })}
                      >
                        {t.restoreFromBackup}
                      </Button>
                    </>
                  )}
                </div>

                {!canWriteFiles && (
                  <Alert>
                    <AlertTriangle className="h-4 w-4" />
                    <AlertTitle>{t.readOnlyTitle}</AlertTitle>
                    <AlertDescription>{t.readOnlyDescription}</AlertDescription>
                  </Alert>
                )}

                {selectedFilePath && supportsBackupRestore && !canViewBackups && (
                  <Alert>
                    <AlertTriangle className="h-4 w-4" />
                    <AlertTitle>{t.backupComparePermissionTitle}</AlertTitle>
                    <AlertDescription>{t.backupComparePermissionDescription}</AlertDescription>
                  </Alert>
                )}

                {backupContentQuery.error instanceof ApiError && compareMode === 'backup' && selectedBackupTimestamp && (
                  <Alert variant="destructive">
                    <AlertTriangle className="h-4 w-4" />
                    <AlertTitle>{t.compareUnavailableTitle}</AlertTitle>
                    <AlertDescription>{backupContentQuery.error.message}</AlertDescription>
                  </Alert>
                )}

                {fileQuery.isPending ? (
                  <Skeleton className="h-[520px] w-full" />
                ) : selectedFilePath ? (
                  <div className="overflow-hidden rounded-md border border-border">
                    {compareMode === 'none' ? (
                      <Editor
                        height="520px"
                        language={detectEditorLanguage(selectedFilePath)}
                        theme="vs-dark"
                        value={editorContent}
                        onChange={(value) => {
                          const nextValue = value ?? ''
                          rawEditorContentRef.current = nextValue
                          setEditorContent(nextValue)
                        }}
                        onMount={(editor, monaco) => bindMonacoSaveShortcut(editor, monaco, () => saveCurrentContextRef.current())}
                        options={{
                          minimap: { enabled: false },
                          fontSize: 13,
                          wordWrap: 'on',
                          scrollBeyondLastLine: false,
                          automaticLayout: true,
                        }}
                      />
                    ) : (
                      <DiffEditor
                        height="520px"
                        language={detectEditorLanguage(selectedFilePath)}
                        theme="vs-dark"
                        original={compareOriginal}
                        modified={editorContent}
                        onMount={handleDiffEditorMount}
                        options={{
                          minimap: { enabled: false },
                          fontSize: 13,
                          wordWrap: 'on',
                          scrollBeyondLastLine: false,
                          automaticLayout: true,
                        }}
                      />
                    )}
                  </div>
                ) : (
                  <div className="flex h-60 items-center justify-center rounded-md border border-dashed border-border text-sm text-muted-foreground">
                    {t.selectFileTitle}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}
