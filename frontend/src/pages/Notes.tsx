import { useState, useEffect, useMemo, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Archive,
  ArchiveRestore,
  CheckSquare,
  Edit3,
  Eye,
  Pin,
  Plus,
  Search,
  Square,
  StickyNote,
  Trash2,
  Users,
  X,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '@/api/client'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { Dropdown, type DropdownOption } from '@/Singra/UI'
import { Button } from '@/components/ui/Button'
import { Switch } from '@/components/ui/Switch'
import { teamsApi, type Team } from '@/api/teams'

export interface NoteItem {
  id: number
  note_uid: string
  title: string
  content: string
  category: string
  color: string
  is_pinned: boolean
  is_archived: boolean
  note_type: string
  user_id: number
  team_id?: number | null
  team_name?: string | null
  creator_name?: string | null
  can_edit?: boolean
  created_at: string
  updated_at: string
}

function InlineMarkdown({ text, strikethrough }: { text: string; strikethrough?: boolean }) {
  return (
    <span className={`inline-block ${strikethrough ? 'line-through text-on-surface-variant/60' : 'text-on-surface'}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <span className="m-0 inline">{children}</span>,
          strong: ({ children }) => <strong className="font-semibold text-on-surface">{children}</strong>,
          em: ({ children }) => <em className="italic">{children}</em>,
          code: ({ children }) => (
            <code className="px-1 py-0.5 rounded bg-surface-container-highest font-mono text-[0.85em]">
              {children}
            </code>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </span>
  )
}

const COLOR_THEMES = [
  { id: 'primary', label: 'Blau (Standard)', bg: 'bg-primary/10', border: 'border-primary/40', text: 'text-primary', badge: 'bg-primary/20 text-primary border-primary/30' },
  { id: 'emerald', label: 'Grün / Einkäufe', bg: 'bg-emerald-500/10', border: 'border-emerald-500/40', text: 'text-emerald-400', badge: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' },
  { id: 'amber', label: 'Gelb / Orange', bg: 'bg-amber-500/10', border: 'border-amber-500/40', text: 'text-amber-400', badge: 'bg-amber-500/20 text-amber-300 border-amber-500/30' },
  { id: 'rose', label: 'Rot / Dringend', bg: 'bg-rose-500/10', border: 'border-rose-500/40', text: 'text-rose-400', badge: 'bg-rose-500/20 text-rose-300 border-rose-500/30' },
  { id: 'purple', label: 'Lila / Server & Tech', bg: 'bg-purple-500/10', border: 'border-purple-500/40', text: 'text-purple-400', badge: 'bg-purple-500/20 text-purple-300 border-purple-500/30' },
  { id: 'cyan', label: 'Cyan / Ideen', bg: 'bg-cyan-500/10', border: 'border-cyan-500/40', text: 'text-cyan-400', badge: 'bg-cyan-500/20 text-cyan-300 border-cyan-500/30' },
]

function getColorTheme(colorId?: string) {
  let normalized = colorId || 'primary'
  if (normalized === 'blue') normalized = 'primary'
  if (normalized === 'green') normalized = 'emerald'
  return COLOR_THEMES.find((c) => c.id === normalized) || COLOR_THEMES[0]
}

export function Notes() {
  const { t } = useTranslation()
  const [notes, setNotes] = useState<NoteItem[]>([])
  const [teams, setTeams] = useState<Team[]>([])
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedCategory, setSelectedCategory] = useState('all')
  const [selectedType, setSelectedType] = useState('all')
  const [showArchived, setShowArchived] = useState(false)

  // Modal State
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [editingNote, setEditingNote] = useState<NoteItem | null>(null)
  const [formTitle, setFormTitle] = useState('')
  const [formContent, setFormContent] = useState('')
  const [formCategory, setFormCategory] = useState('personal')
  const [formColor, setFormColor] = useState('primary')
  const [formNoteType, setFormNoteType] = useState('personal')
  const [formTeamId, setFormTeamId] = useState<number | null>(null)
  const [formIsPinned, setFormIsPinned] = useState(false)
  const [modalTab, setModalTab] = useState<'edit' | 'preview'>('edit')
  const [saving, setSaving] = useState(false)

  const loadNotes = useCallback(async () => {
    try {
      setLoading(true)
      const data = await api<NoteItem[]>('/notes?include_archived=true')
      setNotes(Array.isArray(data) ? data : [])
    } catch (err: any) {
      if (err?.status === 403 || err?.status === 404) {
        setNotes([])
      } else {
        toast.error(t('notes.loadError', 'Fehler beim Laden der Notizen'))
      }
    } finally {
      setLoading(false)
    }
  }, [])

  const loadTeams = useCallback(async () => {
    try {
      const data = await teamsApi.list()
      setTeams(Array.isArray(data) ? data : [])
    } catch {
      setTeams([])
    }
  }, [])

  useEffect(() => {
    void loadNotes()
    void loadTeams()
  }, [loadNotes, loadTeams])

  const openCreateModal = () => {
    setEditingNote(null)
    setFormTitle('')
    setFormContent('')
    setFormCategory('personal')
    setFormColor('primary')
    setFormNoteType('personal')
    setFormTeamId(teams.length > 0 ? teams[0].id : null)
    setFormIsPinned(false)
    setModalTab('edit')
    setIsModalOpen(true)
  }

  const openEditModal = (note: NoteItem) => {
    setEditingNote(note)
    setFormTitle(note.title)
    setFormContent(note.content || '')
    setFormCategory(note.category || 'personal')
    setFormColor(note.color || 'primary')
    setFormNoteType(note.note_type || 'personal')
    setFormTeamId(note.team_id || (teams.length > 0 ? teams[0].id : null))
    setFormIsPinned(note.is_pinned || false)
    setModalTab('edit')
    setIsModalOpen(true)
  }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!formTitle.trim()) {
      toast.error(t('notes.titleRequired', 'Bitte gib einen Titel für die Notiz ein.'))
      return
    }

    try {
      setSaving(true)
      const payload = {
        title: formTitle.trim(),
        content: formContent,
        category: formCategory,
        color: formColor,
        is_pinned: formIsPinned,
        note_type: formNoteType,
        team_id: formNoteType === 'team' ? formTeamId : null,
      }

      if (editingNote) {
        await api(`/notes/${editingNote.note_uid}`, {
          method: 'PUT',
          body: JSON.stringify(payload),
        })
        toast.success(t('notes.updated', 'Notiz erfolgreich aktualisiert.'))
      } else {
        await api('/notes', {
          method: 'POST',
          body: JSON.stringify(payload),
        })
        toast.success(t('notes.created', 'Notiz erfolgreich erstellt.'))
      }

      setIsModalOpen(false)
      void loadNotes()
    } catch {
      toast.error(t('notes.saveError', 'Fehler beim Speichern der Notiz.'))
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (note: NoteItem) => {
    const ok = await confirm({
      title: t('notes.deleteConfirmTitle', 'Notiz löschen?'),
      message: t('notes.deleteConfirmMessage', 'Möchtest du diese Notiz wirklich unwiderruflich löschen?'),
      confirmText: t('common.delete', 'Löschen'),
      danger: true,
    })
    if (!ok) return

    try {
      await api(`/notes/${note.note_uid}`, { method: 'DELETE' })
      toast.success(t('notes.deleted', 'Notiz gelöscht.'))
      void loadNotes()
    } catch {
      toast.error(t('notes.deleteError', 'Fehler beim Löschen der Notiz.'))
    }
  }

  const handleTogglePin = async (note: NoteItem, e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await api(`/notes/${note.note_uid}/pin`, { method: 'POST' })
      void loadNotes()
    } catch {
      toast.error(t('notes.pinError', 'Fehler beim Anpinnen der Notiz.'))
    }
  }

  const handleToggleArchive = async (note: NoteItem, e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await api(`/notes/${note.note_uid}/archive`, { method: 'POST' })
      toast.success(note.is_archived ? t('notes.unarchived', 'Notiz wiederhergestellt.') : t('notes.archived', 'Notiz archiviert.'))
      void loadNotes()
    } catch {
      toast.error(t('notes.archiveError', 'Fehler beim Ändern des Archivierungsstatus.'))
    }
  }

  // Interactive Checklist Toggle
  const handleToggleCheckItem = async (note: NoteItem, itemIndex: number, e: React.MouseEvent) => {
    e.stopPropagation()
    const lines = (note.content || '').split('\n')
    let currentCheckIdx = 0
    const newLines = lines.map((line) => {
      const isUnchecked = line.trimStart().startsWith('- [ ]')
      const isChecked = line.trimStart().startsWith('- [x]') || line.trimStart().startsWith('- [X]')
      if (isUnchecked || isChecked) {
        if (currentCheckIdx === itemIndex) {
          currentCheckIdx++
          if (isUnchecked) {
            return line.replace('- [ ]', '- [x]')
          } else {
            return line.replace(/- \[[xX]\]/, '- [ ]')
          }
        }
        currentCheckIdx++
      }
      return line
    })

    const updatedContent = newLines.join('\n')

    // Optimistic UI update
    setNotes((prev) =>
      prev.map((n) => (n.id === note.id ? { ...n, content: updatedContent } : n))
    )

    try {
      await api(`/notes/${note.note_uid}`, {
        method: 'PUT',
        body: JSON.stringify({ content: updatedContent }),
      })
    } catch {
      void loadNotes()
    }
  }

  // Filtered Notes
  const filteredNotes = useMemo(() => {
    return notes.filter((note) => {
      if (showArchived) {
        if (!note.is_archived) return false
      } else {
        if (note.is_archived) return false
      }

      if (selectedCategory !== 'all' && note.category !== selectedCategory) {
        return false
      }

      if (selectedType === 'personal' && note.note_type !== 'personal') {
        return false
      }
      if (selectedType === 'team' && note.note_type !== 'team') {
        return false
      }

      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase()
        const titleMatch = (note.title || '').toLowerCase().includes(q)
        const contentMatch = (note.content || '').toLowerCase().includes(q)
        const teamMatch = (note.team_name || '').toLowerCase().includes(q)
        if (!titleMatch && !contentMatch && !teamMatch) return false
      }

      return true
    })
  }, [notes, showArchived, selectedCategory, selectedType, searchQuery])

  const categoryOptions: DropdownOption[] = [
    { value: 'all', label: t('notes.categories.all', 'Alle Kategorien') },
    { value: 'personal', label: t('notes.categories.personal', 'Persönlich') },
    { value: 'shopping', label: t('notes.categories.shopping', 'Einkaufsliste') },
    { value: 'todo', label: t('notes.categories.todo', 'Aufgaben & To-Dos') },
    { value: 'work', label: t('notes.categories.work', 'Arbeit & Server') },
    { value: 'idea', label: t('notes.categories.idea', 'Ideen & Entwürfe') },
    { value: 'meeting', label: t('notes.categories.meeting', 'Besprechung / Notizen') },
  ]

  const modalCategoryOptions: DropdownOption[] = [
    { value: 'personal', label: t('notes.categories.personal', 'Persönlich') },
    { value: 'shopping', label: t('notes.categories.shopping', 'Einkaufsliste') },
    { value: 'todo', label: t('notes.categories.todo', 'Aufgaben & To-Dos') },
    { value: 'work', label: t('notes.categories.work', 'Arbeit & Server') },
    { value: 'idea', label: t('notes.categories.idea', 'Ideen & Entwürfe') },
    { value: 'meeting', label: t('notes.categories.meeting', 'Besprechung / Notizen') },
  ]

  const typeOptions: DropdownOption[] = [
    { value: 'all', label: t('notes.types.all', 'Alle Bereiche') },
    { value: 'personal', label: t('notes.types.personal', 'Nur Persönlich') },
    { value: 'team', label: t('notes.types.team', 'Nur Team-Notizen') },
  ]

  const modalTypeOptions: DropdownOption[] = [
    { value: 'personal', label: t('notes.types.personal', 'Persönlich (privat)') },
    { value: 'team', label: t('notes.types.team', 'Im Team geteilt') },
  ]

  const teamOptions: DropdownOption[] = teams.map((team) => ({
    value: String(team.id),
    label: team.name,
  }))

  return (
    <div className="space-y-3.5">
      {/* ── Kopfzeile: Titel, Zähler & Erstellen ── */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-8 h-8 rounded-xl bg-primary/15 border border-primary/25 flex items-center justify-center text-primary shadow-xs shrink-0">
            <StickyNote className="w-4 h-4" />
          </div>
          <div className="flex items-center gap-2 min-w-0">
            <h2 className="font-headline text-base sm:text-lg font-bold text-on-surface tracking-tight truncate">
              {t('notes.title', 'Notizen & Listen')}
            </h2>
            <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-surface-container-high text-on-surface-variant border border-outline-variant/30 shrink-0">
              {filteredNotes.length}
            </span>
          </div>
        </div>

        <Button
          onClick={openCreateModal}
          size="sm"
          className="h-8.5 px-3 sm:px-3.5 rounded-xl flex items-center gap-1.5 font-medium shadow-sm shrink-0"
        >
          <Plus className="w-4 h-4" />
          <span className="hidden sm:inline">{t('notes.newNote', 'Neue Notiz')}</span>
          <span className="sm:hidden">{t('notes.newNoteShort', 'Neu')}</span>
        </Button>
      </div>

      {/* ── Werkzeugleiste: Suche & Filter ── */}
      <div className="flex flex-col gap-2.5 bg-surface-container-low/80 border border-outline-variant/30 rounded-2xl p-2.5 sm:p-3 shadow-sm backdrop-blur-sm">
        {/* Suche */}
        <div className="relative w-full">
          <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-on-surface-variant pointer-events-none" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t('notes.searchPlaceholder', 'Notizen oder Inhalte durchsuchen...')}
            className="w-full bg-surface-container border border-outline-variant/40 rounded-xl pl-9 pr-8 py-2 text-sm text-on-surface placeholder:text-on-surface-variant focus:outline-none focus:border-primary transition-colors"
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => setSearchQuery('')}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 p-1 text-on-surface-variant hover:text-on-surface rounded-lg"
              aria-label={t('common.clear', 'Löschen')}
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>

        {/* Untere Leiste: Filter */}
        <div className="flex items-center gap-2 overflow-x-auto no-scrollbar py-0.5">
          <div className="w-36 sm:w-44 shrink-0">
            <Dropdown
              value={selectedCategory}
              onChange={setSelectedCategory}
              options={categoryOptions}
              aria-label={t('notes.categories.all', 'Kategorie')}
            />
          </div>

          <div className="w-32 sm:w-40 shrink-0">
            <Dropdown
              value={selectedType}
              onChange={setSelectedType}
              options={typeOptions}
              aria-label={t('notes.types.all', 'Bereich')}
            />
          </div>

          <button
            type="button"
            onClick={() => setShowArchived(!showArchived)}
            className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium rounded-xl border transition-colors whitespace-nowrap shrink-0 ${
              showArchived
                ? 'bg-amber-500/20 text-amber-300 border-amber-500/40 font-semibold'
                : 'bg-surface-container text-on-surface-variant border-outline-variant/30 hover:text-on-surface'
            }`}
          >
            <Archive className="w-3.5 h-3.5" />
            <span>{showArchived ? t('notes.archivedView', 'Archiviert') : t('notes.activeView', 'Aktiv')}</span>
          </button>
        </div>
      </div>

      {/* Grid of Notes */}
      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3 sm:gap-4">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <div
              key={i}
              className="h-44 rounded-2xl bg-surface-container-low border border-outline-variant/20 animate-pulse"
            />
          ))}
        </div>
      ) : filteredNotes.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 px-4 bg-surface-container-low/60 border border-outline-variant/20 rounded-2xl text-center">
          <div className="w-12 h-12 rounded-2xl bg-primary/10 flex items-center justify-center text-primary mb-3">
            <StickyNote className="w-6 h-6" />
          </div>
          <h3 className="font-headline text-base font-semibold text-on-surface mb-1">
            {showArchived
              ? t('notes.emptyArchivedTitle', 'Keine archivierten Notizen')
              : searchQuery
              ? t('notes.emptySearch', 'Keine passenden Notizen gefunden.')
              : t('notes.emptyTitle', 'Noch keine Notizen vorhanden')}
          </h3>
          <p className="text-xs text-on-surface-variant max-w-sm">
            {showArchived
              ? t('notes.emptyArchivedDesc', 'Archivierte Notizen werden hier angezeigt.')
              : searchQuery
              ? t('notes.emptySearchDesc', 'Passe deine Suchbegriffe oder Filter an.')
              : t('notes.emptyDesc', 'Erstelle deine erste Notiz, Einkaufsliste oder diktiere per KI.')}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3 sm:gap-4">
          {filteredNotes.map((note) => {
            const theme = getColorTheme(note.color)
            const lines = (note.content || '').split('\n')
            let checkCounter = 0

            return (
              <div
                key={note.id}
                onClick={() => openEditModal(note)}
                className={`group relative flex flex-col justify-between rounded-2xl border transition-all duration-200 cursor-pointer overflow-hidden p-3.5 sm:p-4 ${theme.bg} ${theme.border} hover:shadow-md hover:border-outline/50 active:scale-[0.99]`}
              >
                {/* Header */}
                <div>
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <div className="flex flex-wrap items-center gap-1.5 min-w-0">
                      <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full border truncate ${theme.badge}`}>
                        {note.category === 'shopping'
                          ? 'Einkauf'
                          : note.category === 'todo'
                          ? 'To-Do'
                          : note.category === 'work'
                          ? 'Server'
                          : note.category === 'idea'
                          ? 'Idee'
                          : note.category === 'meeting'
                          ? 'Meeting'
                          : 'Notiz'}
                      </span>
                      {note.note_type === 'team' && (
                        <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 flex items-center gap-1">
                          <Users className="w-3 h-3" />
                          <span className="truncate max-w-[90px]">{note.team_name || 'Team'}</span>
                        </span>
                      )}
                    </div>

                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        type="button"
                        onClick={(e) => handleTogglePin(note, e)}
                        title={note.is_pinned ? t('notes.unpin', 'Lösen') : t('notes.pin', 'Anpinnen')}
                        className={`p-1.5 rounded-lg transition-colors ${
                          note.is_pinned
                            ? 'text-primary bg-primary/20 hover:bg-primary/30'
                            : 'text-on-surface-variant/60 hover:text-on-surface hover:bg-surface-container-high'
                        }`}
                      >
                        {note.is_pinned ? <Pin className="w-3.5 h-3.5 fill-primary" /> : <Pin className="w-3.5 h-3.5" />}
                      </button>
                    </div>
                  </div>

                  <h4 className="font-headline text-sm sm:text-base font-semibold text-on-surface mb-2 line-clamp-2 leading-snug">
                    {note.title}
                  </h4>

                  {/* Content Preview & Interactive Checklist */}
                  <div className="space-y-1 text-xs text-on-surface-variant line-clamp-6 mb-3">
                    {lines.map((line, idx) => {
                      const isUnchecked = line.trimStart().startsWith('- [ ]')
                      const isChecked = line.trimStart().startsWith('- [x]') || line.trimStart().startsWith('- [X]')

                      if (isUnchecked || isChecked) {
                        const itemIdx = checkCounter++
                        const itemText = line.replace(/^[ \t]*- \[[ xX]\][ \t]*/, '')
                        return (
                          <div
                            key={idx}
                            onClick={(e) => handleToggleCheckItem(note, itemIdx, e)}
                            className="flex items-start gap-2 py-1 px-1.5 rounded-lg hover:bg-surface-container-high/50 active:bg-surface-container-high/70 transition-colors cursor-pointer min-h-[26px]"
                          >
                            {isChecked ? (
                              <CheckSquare className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
                            ) : (
                              <Square className="w-3.5 h-3.5 text-on-surface-variant shrink-0 mt-0.5" />
                            )}
                            <InlineMarkdown text={itemText} strikethrough={isChecked} />
                          </div>
                        )
                      }

                      if (!line.trim()) return <div key={idx} className="h-1.5" />

                      return (
                        <div key={idx} className="line-clamp-2 text-on-surface-variant leading-relaxed">
                          <InlineMarkdown text={line} />
                        </div>
                      )
                    })}
                  </div>
                </div>

                {/* Card Footer */}
                <div className="flex items-center justify-between pt-2 border-t border-outline-variant/20 text-[11px] text-on-surface-variant/80 mt-auto">
                  <span>
                    {new Date(note.updated_at || note.created_at).toLocaleDateString('de-DE', {
                      day: '2-digit',
                      month: '2-digit',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </span>

                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={(e) => handleToggleArchive(note, e)}
                      title={note.is_archived ? t('notes.unarchive', 'Wiederherstellen') : t('notes.archive', 'Archivieren')}
                      className="p-1.5 text-on-surface-variant hover:text-on-surface rounded-lg hover:bg-surface-container-high transition-colors"
                    >
                      {note.is_archived ? <ArchiveRestore className="w-3.5 h-3.5" /> : <Archive className="w-3.5 h-3.5" />}
                    </button>

                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation()
                        void handleDelete(note)
                      }}
                      title={t('common.delete', 'Löschen')}
                      className="p-1.5 text-rose-400 hover:text-rose-300 rounded-lg hover:bg-rose-500/10 transition-colors"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Create / Edit Modal */}
      {isModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-[fadeIn_.15s_ease-out]">
          <div
            className="w-full max-w-xl bg-surface-container-low border border-outline-variant/30 rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-outline-variant/30">
              <h3 className="font-headline text-lg font-semibold text-on-surface">
                {editingNote ? t('notes.editNote', 'Notiz bearbeiten') : t('notes.createNote', 'Neue Notiz erstellen')}
              </h3>
              <button
                type="button"
                onClick={() => setIsModalOpen(false)}
                className="p-1.5 text-on-surface-variant hover:text-on-surface rounded-xl hover:bg-surface-container-high transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Modal Body */}
            <form onSubmit={handleSave} className="flex-1 overflow-y-auto overflow-x-hidden p-4 sm:p-6 space-y-4">
              {/* Title */}
              <div>
                <label className="block text-xs font-semibold text-on-surface-variant mb-1.5">
                  {t('notes.formTitle', 'Titel')} *
                </label>
                <input
                  type="text"
                  required
                  value={formTitle}
                  onChange={(e) => setFormTitle(e.target.value)}
                  placeholder={t('notes.formTitlePlaceholder', 'z. B. Einkaufsliste Edeka, Meeting-Punkte, Server-Check')}
                  className="w-full bg-surface-container border border-outline-variant/40 rounded-xl px-3.5 py-2 text-sm text-on-surface placeholder:text-on-surface-variant focus:outline-none focus:border-primary transition-colors"
                />
              </div>

              {/* Category & Color */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                <div>
                  <label className="block text-xs font-semibold text-on-surface-variant mb-1.5">
                    {t('notes.formCategory', 'Kategorie')}
                  </label>
                  <Dropdown
                    value={formCategory}
                    onChange={setFormCategory}
                    options={modalCategoryOptions}
                  />
                </div>

                <div>
                  <label className="block text-xs font-semibold text-on-surface-variant mb-1.5">
                    {t('notes.formType', 'Bereich & Sichtbarkeit')}
                  </label>
                  <Dropdown
                    value={formNoteType}
                    onChange={setFormNoteType}
                    options={modalTypeOptions}
                  />
                </div>
              </div>

              {/* Team Selector if team note */}
              {formNoteType === 'team' && (
                <div>
                  <label className="block text-xs font-semibold text-on-surface-variant mb-1.5">
                    {t('notes.formTeam', 'Team zuweisen')}
                  </label>
                  {teams.length === 0 ? (
                    <p className="text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded-xl p-2.5">
                      {t('notes.noTeamsAvailable', 'Du bist noch keinem Team beigetreten. Erstelle zuerst ein Team unter /teams.')}
                    </p>
                  ) : (
                    <Dropdown
                      value={formTeamId ? String(formTeamId) : String(teams[0]?.id || '')}
                      onChange={(val) => setFormTeamId(Number(val))}
                      options={teamOptions}
                    />
                  )}
                </div>
              )}

              {/* Color Accent Picker */}
              <div>
                <label className="block text-xs font-semibold text-on-surface-variant mb-1.5">
                  {t('notes.formColor', 'Farbakzent')}
                </label>
                <div className="flex items-center gap-1.5 flex-wrap">
                  {COLOR_THEMES.map((theme) => (
                    <button
                      key={theme.id}
                      type="button"
                      onClick={() => setFormColor(theme.id)}
                      className={`px-2.5 py-1.5 rounded-xl text-xs font-medium border flex items-center gap-1.5 transition-all ${
                        formColor === theme.id
                          ? `${theme.bg} ${theme.border} ${theme.text} ring-2 ring-primary/40`
                          : 'bg-surface-container border-outline-variant/30 text-on-surface-variant hover:text-on-surface'
                      }`}
                    >
                      <span className={`w-2 h-2 rounded-full ${theme.bg} border ${theme.border}`} />
                      {theme.label.split(' ')[0]}
                    </button>
                  ))}
                </div>
              </div>

              {/* Pin Switch */}
              <div className="flex items-center justify-between bg-surface-container/50 border border-outline-variant/30 rounded-xl p-3">
                <span className="text-xs font-semibold text-on-surface flex items-center gap-1.5">
                  <Pin className="w-3.5 h-3.5 text-primary" />
                  {t('notes.pinToTop', 'Oben anpinnen')}
                </span>
                <Switch
                  checked={formIsPinned}
                  onCheckedChange={setFormIsPinned}
                  aria-label={t('notes.pinToTop', 'Oben anpinnen')}
                />
              </div>

              {/* Content / Editor & Dictation */}
              <div className="space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <label className="text-xs font-semibold text-on-surface-variant">
                    {t('notes.formContent', 'Inhalt & Checkliste')}
                  </label>
                  <div className="flex items-center bg-surface-container rounded-lg p-0.5 border border-outline-variant/30 text-[11px]">
                    <button
                      type="button"
                      onClick={() => setModalTab('edit')}
                      className={`flex items-center gap-1 px-2 py-0.5 rounded-md font-medium transition-colors ${
                        modalTab === 'edit'
                          ? 'bg-primary/20 text-primary'
                          : 'text-on-surface-variant hover:text-on-surface'
                      }`}
                    >
                      <Edit3 className="w-3 h-3" />
                      {t('notes.tabEditor', 'Editor')}
                    </button>
                    <button
                      type="button"
                      onClick={() => setModalTab('preview')}
                      className={`flex items-center gap-1 px-2 py-0.5 rounded-md font-medium transition-colors ${
                        modalTab === 'preview'
                          ? 'bg-primary/20 text-primary'
                          : 'text-on-surface-variant hover:text-on-surface'
                      }`}
                    >
                      <Eye className="w-3 h-3" />
                      {t('notes.tabPreview', 'Vorschau')}
                    </button>
                  </div>
                </div>

                {modalTab === 'edit' && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => setFormContent((prev) => (prev ? `${prev}\n- [ ] ` : '- [ ] '))}
                      className="inline-flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-lg bg-surface-container text-on-surface-variant hover:text-primary hover:bg-surface-container-high border border-outline-variant/30 transition-colors"
                    >
                      <Plus className="w-3 h-3 text-primary" />
                      <span>Checkliste</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => setFormContent((prev) => (prev ? `${prev}\n- [ ] 1x  (~0,00 €)` : '- [ ] 1x  (~0,00 €)'))}
                      className="inline-flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-lg bg-surface-container text-on-surface-variant hover:text-emerald-400 hover:bg-surface-container-high border border-outline-variant/30 transition-colors"
                    >
                      <Plus className="w-3 h-3 text-emerald-400" />
                      <span>Einkaufsposten</span>
                    </button>
                  </div>
                )}

                {modalTab === 'edit' ? (
                  <textarea
                    rows={8}
                    value={formContent}
                    onChange={(e) => setFormContent(e.target.value)}
                    placeholder={t(
                      'notes.contentPlaceholder',
                      '- [ ] 1x Butter (~1,89 €)\n- [ ] 6x Eier (~1,99 €)\n- [ ] 1x Brot (~2,49 €)\n\n**Geschätzte Gesamtsumme: ca. 6,37 €**'
                    )}
                    className="w-full bg-surface-container border border-outline-variant/40 rounded-xl p-3.5 text-xs text-on-surface placeholder:text-on-surface-variant/60 focus:outline-none focus:border-primary transition-colors font-mono leading-relaxed resize-y"
                  />
                ) : (
                  <div className="w-full min-h-[190px] max-h-[300px] overflow-y-auto bg-surface-container/60 border border-outline-variant/40 rounded-xl p-3.5 text-xs space-y-1.5">
                    {formContent.trim() ? (
                      formContent.split('\n').map((line, idx) => {
                        const isUnchecked = line.trimStart().startsWith('- [ ]')
                        const isChecked = line.trimStart().startsWith('- [x]') || line.trimStart().startsWith('- [X]')

                        if (isUnchecked || isChecked) {
                          const itemText = line.replace(/^[ \t]*- \[[ xX]\][ \t]*/, '')
                          const toggleCheckInModal = () => {
                            const lines = formContent.split('\n')
                            lines[idx] = isUnchecked
                              ? line.replace('- [ ]', '- [x]')
                              : line.replace(/- \[[xX]\]/, '- [ ]')
                            setFormContent(lines.join('\n'))
                          }
                          return (
                            <div
                              key={idx}
                              onClick={toggleCheckInModal}
                              className="flex items-start gap-2 py-0.5 px-1.5 rounded hover:bg-surface-container-high/60 transition-colors cursor-pointer"
                            >
                              {isChecked ? (
                                <CheckSquare className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
                              ) : (
                                <Square className="w-3.5 h-3.5 text-on-surface-variant shrink-0 mt-0.5" />
                              )}
                              <InlineMarkdown text={itemText} strikethrough={isChecked} />
                            </div>
                          )
                        }

                        if (!line.trim()) return <div key={idx} className="h-1.5" />

                        return (
                          <div key={idx} className="text-on-surface leading-relaxed">
                            <InlineMarkdown text={line} />
                          </div>
                        )
                      })
                    ) : (
                      <p className="text-xs text-on-surface-variant/60 italic py-6 text-center">
                        {t('notes.previewEmpty', 'Noch kein Inhalt zum Anzeigen eingegeben.')}
                      </p>
                    )}
                  </div>
                )}
              </div>

              {/* Modal Footer */}
              <div className="flex items-center justify-end gap-3 pt-4 border-t border-outline-variant/30">
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => setIsModalOpen(false)}
                >
                  {t('common.cancel', 'Abbrechen')}
                </Button>
                <Button type="submit" disabled={saving}>
                  {saving ? (
                    <span className="w-4 h-4 border-2 border-on-primary border-t-transparent rounded-full animate-spin mr-1.5" />
                  ) : null}
                  {editingNote ? t('common.save', 'Speichern') : t('notes.createAction', 'Notiz anlegen')}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
