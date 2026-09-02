import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Megaphone,
  Plus,
  Edit2,
  Trash2,
  Eye,
  Calendar,
  Bold,
  Italic,
  Heading,
  List,
  ListOrdered,
  Link,
  Code,
  CheckCircle,
  Clock,
  ExternalLink,
  Save,
  X,
} from 'lucide-react'
import {
  listAdminPopups,
  createAdminPopup,
  updateAdminPopup,
  deleteAdminPopup,
  type PanelPopup,
  type PanelPopupCreateInput,
} from '@/api/popups'
import { Button, Switch, DateTimePicker } from '@/Singra/UI'
import { toast } from '@/stores/toastStore'
import { confirm } from '@/stores/confirmStore'
import { useHasPermission } from '@/hooks/useHasPermission'
import { PanelPopupModal } from '@/components/popups/PanelPopupModal'

export function PopupTab() {
  const { t, i18n } = useTranslation()
  const canWrite = useHasPermission('panel.settings.write')

  const [popups, setPopups] = useState<PanelPopup[]>([])
  const [loading, setLoading] = useState(true)
  const [editingPopup, setEditingPopup] = useState<PanelPopup | null>(null)
  const [isCreating, setIsCreating] = useState(false)
  const [previewPopup, setPreviewPopup] = useState<PanelPopup | null>(null)

  // Form State
  const [title, setTitle] = useState('')
  const [contentMarkdown, setContentMarkdown] = useState('')
  const [isActive, setIsActive] = useState(true)
  const [startAt, setStartAt] = useState('')
  const [endAt, setEndAt] = useState('')
  const [buttonText, setButtonText] = useState('')
  const [buttonUrl, setButtonUrl] = useState('')
  const [saving, setSaving] = useState(false)

  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const loadPopups = async () => {
    try {
      setLoading(true)
      const data = await listAdminPopups()
      setPopups(data)
    } catch (err: any) {
      toast.error(err.message || 'Fehler beim Laden der Pop-ups')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadPopups()
  }, [])

  const startCreate = () => {
    setEditingPopup(null)
    setTitle('')
    setContentMarkdown('')
    setIsActive(true)
    setStartAt('')
    setEndAt('')
    setButtonText('')
    setButtonUrl('')
    setIsCreating(true)
  }

  const startEdit = (p: PanelPopup) => {
    setEditingPopup(p)
    setTitle(p.title)
    setContentMarkdown(p.content_markdown)
    setIsActive(p.is_active)
    setStartAt(p.start_at ? p.start_at.substring(0, 16) : '')
    setEndAt(p.end_at ? p.end_at.substring(0, 16) : '')
    setButtonText(p.button_text || '')
    setButtonUrl(p.button_url || '')
    setIsCreating(false)
  }

  const cancelEdit = () => {
    setEditingPopup(null)
    setIsCreating(false)
  }

  const insertMarkdown = (prefix: string, suffix: string = '') => {
    const textarea = textareaRef.current
    if (!textarea) return

    const start = textarea.selectionStart
    const end = textarea.selectionEnd
    const currentVal = textarea.value
    const selectedText = currentVal.substring(start, end)
    const replacement = `${prefix}${selectedText || 'Text'}${suffix}`

    const newVal = currentVal.substring(0, start) + replacement + currentVal.substring(end)
    setContentMarkdown(newVal)

    setTimeout(() => {
      textarea.focus()
      textarea.setSelectionRange(start + prefix.length, start + replacement.length - suffix.length)
    }, 0)
  }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim() || !contentMarkdown.trim()) {
      toast.error(t('popups.validationRequired', 'Titel und Textinhalt sind Pflichtfelder.'))
      return
    }

    setSaving(true)
    try {
      const payload: PanelPopupCreateInput = {
        title: title.trim(),
        content_markdown: contentMarkdown.trim(),
        is_active: isActive,
        start_at: startAt ? new Date(startAt).toISOString() : null,
        end_at: endAt ? new Date(endAt).toISOString() : null,
        button_text: buttonText.trim() || null,
        button_url: buttonUrl.trim() || null,
      }

      if (editingPopup) {
        await updateAdminPopup(editingPopup.id, payload)
        toast.success(t('popups.saved', 'Pop-up erfolgreich aktualisiert.'))
      } else {
        await createAdminPopup(payload)
        toast.success(t('popups.created', 'Pop-up erfolgreich erstellt.'))
      }

      cancelEdit()
      await loadPopups()
    } catch (err: any) {
      toast.error(err.message || 'Fehler beim Speichern des Pop-ups')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (popup: PanelPopup) => {
    const ok = await confirm({
      title: t('popups.deleteTitle', 'Pop-up löschen?'),
      message: t('popups.deleteConfirm', 'Möchtest du dieses Pop-up wirklich unwiderruflich löschen?'),
      danger: true,
      confirmText: t('common.delete', 'Löschen'),
    })
    if (!ok) return

    try {
      await deleteAdminPopup(popup.id)
      toast.success(t('popups.deleted', 'Pop-up gelöscht.'))
      if (editingPopup?.id === popup.id) {
        cancelEdit()
      }
      await loadPopups()
    } catch (err: any) {
      toast.error(err.message || 'Fehler beim Löschen des Pop-ups')
    }
  }

  const openPreview = () => {
    setPreviewPopup({
      id: editingPopup?.id || 999999,
      title: title || 'Beispiel-Ankündigung',
      content_markdown: contentMarkdown || 'Hier steht der formatierte Textinhalt der Ankündigung.',
      is_active: isActive,
      start_at: startAt ? new Date(startAt).toISOString() : null,
      end_at: endAt ? new Date(endAt).toISOString() : null,
      button_text: buttonText || null,
      button_url: buttonUrl || null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    })
  }

  return (
    <div className="space-y-6">
      {/* Header Info */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h2 className="font-headline text-headline-sm text-primary">
            {t('popups.title', 'Pop-ups & Ankündigungen')}
          </h2>
          <p className="font-body-md text-body-md text-on-surface-variant mt-1">
            {t(
              'popups.subtitle',
              'Erstelle wichtige Hinweise und Ankündigungen für alle Benutzer des Panels.'
            )}
          </p>
        </div>
        {canWrite && !isCreating && !editingPopup && (
          <Button variant="primary" onClick={startCreate} className="shrink-0">
            <Plus className="w-4 h-4 mr-1.5" />
            {t('popups.newPopup', 'Neues Pop-up anlegen')}
          </Button>
        )}
      </div>

      {/* Editor Form */}
      {(isCreating || editingPopup) && (
        <form onSubmit={handleSave} className="msm-card p-6 border-primary/40 space-y-5">
          <div className="flex items-center justify-between border-b border-outline-variant/30 pb-4">
            <h3 className="font-headline text-title-lg text-on-surface flex items-center gap-2">
              <Megaphone className="w-5 h-5 text-primary" />
              {isCreating ? t('popups.createHeader', 'Neues Pop-up erstellen') : t('popups.editHeader', 'Pop-up bearbeiten')}
            </h3>
            <button
              type="button"
              onClick={cancelEdit}
              className="p-1 rounded-lg text-on-surface-variant hover:text-on-surface hover:bg-surface-container-highest"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          <div className="space-y-4">
            {/* Titel */}
            <div>
              <label htmlFor="popup-title-input" className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                {t('popups.fieldTitle', 'Titel / Überschrift')} *
              </label>
              <input
                id="popup-title-input"
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="z. B. Geplante Wartungsarbeiten am Samstag"
                required
                maxLength={255}
                className="msm-input"
              />
            </div>

            {/* Markdown Toolbar & Content */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label htmlFor="popup-content-input" className="block font-label-md text-label-md text-on-surface-variant uppercase tracking-wider">
                  {t('popups.fieldContent', 'Inhalt (Markdown)')} *
                </label>
                <div className="flex items-center gap-1 bg-surface-container-high rounded-lg p-1 border border-outline-variant/30">
                  <button
                    type="button"
                    title="Fett (**text**)"
                    onClick={() => insertMarkdown('**', '**')}
                    className="p-1.5 rounded hover:bg-surface-container-highest text-on-surface-variant hover:text-on-surface text-xs"
                  >
                    <Bold className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    title="Kursiv (*text*)"
                    onClick={() => insertMarkdown('*', '*')}
                    className="p-1.5 rounded hover:bg-surface-container-highest text-on-surface-variant hover:text-on-surface text-xs"
                  >
                    <Italic className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    title="Überschrift (### )"
                    onClick={() => insertMarkdown('### ')}
                    className="p-1.5 rounded hover:bg-surface-container-highest text-on-surface-variant hover:text-on-surface text-xs"
                  >
                    <Heading className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    title="Aufzählung (- )"
                    onClick={() => insertMarkdown('- ')}
                    className="p-1.5 rounded hover:bg-surface-container-highest text-on-surface-variant hover:text-on-surface text-xs"
                  >
                    <List className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    title="Nummerierte Liste (1. )"
                    onClick={() => insertMarkdown('1. ')}
                    className="p-1.5 rounded hover:bg-surface-container-highest text-on-surface-variant hover:text-on-surface text-xs"
                  >
                    <ListOrdered className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    title="Link ([Text](url))"
                    onClick={() => insertMarkdown('[', '](https://example.com)')}
                    className="p-1.5 rounded hover:bg-surface-container-highest text-on-surface-variant hover:text-on-surface text-xs"
                  >
                    <Link className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    title="Code (`code`)"
                    onClick={() => insertMarkdown('`', '`')}
                    className="p-1.5 rounded hover:bg-surface-container-highest text-on-surface-variant hover:text-on-surface text-xs"
                  >
                    <Code className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
              <textarea
                id="popup-content-input"
                ref={textareaRef}
                value={contentMarkdown}
                onChange={(e) => setContentMarkdown(e.target.value)}
                placeholder="Verfasse den Text der Ankündigung in klarem, menschlichem Ton..."
                rows={7}
                required
                className="msm-input font-mono text-sm leading-relaxed"
              />
            </div>

            {/* Zeitraum (Start / Ende) */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('popups.fieldStartAt', 'Gültig ab (optional)')}
                </label>
                <DateTimePicker
                  value={startAt}
                  onChange={setStartAt}
                  locale={i18n.language.startsWith('de') ? 'de' : 'en'}
                  placeholder={t('popups.noStartAt', 'Kein Startzeitpunkt (sofort aktiv)')}
                  aria-label={t('popups.fieldStartAt', 'Gültig ab (optional)')}
                  className="w-full"
                />
              </div>
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('popups.fieldEndAt', 'Gültig bis (optional)')}
                </label>
                <DateTimePicker
                  value={endAt}
                  onChange={setEndAt}
                  locale={i18n.language.startsWith('de') ? 'de' : 'en'}
                  placeholder={t('popups.noEndAt', 'Kein Endzeitpunkt (unbegrenzt)')}
                  aria-label={t('popups.fieldEndAt', 'Gültig bis (optional)')}
                  className="w-full"
                />
              </div>
            </div>

            {/* Optionaler Aktions-Button */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('popups.fieldButtonText', 'Aktions-Button Text (optional)')}
                </label>
                <input
                  type="text"
                  value={buttonText}
                  onChange={(e) => setButtonText(e.target.value)}
                  placeholder="z. B. Statusseite öffnen"
                  maxLength={100}
                  className="msm-input"
                />
              </div>
              <div>
                <label className="block font-label-md text-label-md text-on-surface-variant mb-1.5 uppercase tracking-wider">
                  {t('popups.fieldButtonUrl', 'Aktions-Button URL (optional)')}
                </label>
                <input
                  type="url"
                  value={buttonUrl}
                  onChange={(e) => setButtonUrl(e.target.value)}
                  placeholder="https://status.example.com"
                  maxLength={2048}
                  className="msm-input"
                />
              </div>
            </div>

            {/* Aktiv-Schalter */}
            <div className="flex items-center justify-between pt-2">
              <div>
                <span className="font-label-lg text-label-lg text-on-surface font-medium block">
                  {t('popups.fieldIsActive', 'Pop-up aktivieren')}
                </span>
                <span className="text-body-sm text-on-surface-variant block">
                  {t('popups.fieldIsActiveHint', 'Wenn aktiv, wird das Pop-up berechtigten Nutzern im Panel angezeigt.')}
                </span>
              </div>
              <Switch checked={isActive} onCheckedChange={setIsActive} />
            </div>
          </div>

          {/* Action Buttons */}
          <div className="flex items-center justify-end gap-3 pt-4 border-t border-outline-variant/30">
            <Button type="button" variant="secondary" onClick={openPreview}>
              <Eye className="w-4 h-4 mr-1.5" />
              {t('popups.preview', 'Live-Vorschau')}
            </Button>
            <Button type="button" variant="ghost" onClick={cancelEdit}>
              {t('common.cancel', 'Abbrechen')}
            </Button>
            <Button type="submit" variant="primary" disabled={saving}>
              <Save className="w-4 h-4 mr-1.5" />
              {saving ? t('common.saving', 'Speichern...') : t('common.save', 'Speichern')}
            </Button>
          </div>
        </form>
      )}

      {/* Liste bestehender Popups */}
      <div className="msm-card overflow-hidden">
        <div className="p-5 border-b border-outline-variant/30 bg-surface-container-high/30">
          <h3 className="font-headline text-title-md text-on-surface">
            {t('popups.listTitle', 'Angelegte Ankündigungen')}
          </h3>
        </div>

        {loading ? (
          <div className="flex items-center justify-center h-48">
            <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
          </div>
        ) : popups.length === 0 ? (
          <div className="p-8 text-center text-on-surface-variant">
            <Megaphone className="w-10 h-10 mx-auto mb-2 opacity-40 text-primary" />
            <p className="font-body-md text-body-md font-medium">
              {t('popups.empty', 'Keine Pop-ups oder Ankündigungen vorhanden.')}
            </p>
            <p className="text-body-sm text-on-surface-variant/80 mt-1">
              {t('popups.emptyHint', 'Klicke oben auf „Neues Pop-up anlegen“, um eine Ankündigung zu erstellen.')}
            </p>
          </div>
        ) : (
          <div className="divide-y divide-outline-variant/20">
            {popups.map((p) => {
              const now = new Date()
              const isExpired = p.end_at && new Date(p.end_at) < now
              const isFuture = p.start_at && new Date(p.start_at) > now

              return (
                <div
                  key={p.id}
                  className="p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4 hover:bg-surface-container-high/20 transition-colors"
                >
                  <div className="space-y-1.5 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-headline text-title-md text-on-surface font-semibold truncate">
                        {p.title}
                      </span>
                      {p.is_active && !isExpired && !isFuture && (
                        <span className="msm-badge-success flex items-center gap-1 text-xs">
                          <CheckCircle className="w-3 h-3" />
                          {t('popups.statusActive', 'Aktiv')}
                        </span>
                      )}
                      {p.is_active && isFuture && (
                        <span className="msm-badge-info flex items-center gap-1 text-xs">
                          <Clock className="w-3 h-3" />
                          {t('popups.statusScheduled', 'Geplant')}
                        </span>
                      )}
                      {isExpired && (
                        <span className="msm-badge-warn flex items-center gap-1 text-xs">
                          {t('popups.statusExpired', 'Abgelaufen')}
                        </span>
                      )}
                      {!p.is_active && (
                        <span className="msm-badge-neutral text-xs">
                          {t('popups.statusInactive', 'Inaktiv')}
                        </span>
                      )}
                    </div>

                    <p className="text-body-sm text-on-surface-variant line-clamp-2 font-mono">
                      {p.content_markdown}
                    </p>

                    {(p.start_at || p.end_at || p.button_text) && (
                      <div className="flex items-center gap-4 text-xs text-on-surface-variant/80 pt-1">
                        {(p.start_at || p.end_at) && (
                          <span className="flex items-center gap-1">
                            <Calendar className="w-3.5 h-3.5" />
                            {p.start_at ? new Date(p.start_at).toLocaleDateString() : 'Sofort'} –{' '}
                            {p.end_at ? new Date(p.end_at).toLocaleDateString() : 'Unbegrenzt'}
                          </span>
                        )}
                        {p.button_text && (
                          <span className="flex items-center gap-1 text-primary">
                            <ExternalLink className="w-3.5 h-3.5" />
                            Button: {p.button_text}
                          </span>
                        )}
                      </div>
                    )}
                  </div>

                  <div className="flex items-center gap-2 shrink-0 self-end sm:self-center">
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setPreviewPopup(p)}
                      title={t('popups.preview', 'Vorschau')}
                    >
                      <Eye className="w-4 h-4 mr-1" />
                      {t('popups.previewBtn', 'Vorschau')}
                    </Button>
                    {canWrite && (
                      <>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => startEdit(p)}
                          title={t('common.edit', 'Bearbeiten')}
                        >
                          <Edit2 className="w-4 h-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleDelete(p)}
                          title={t('common.delete', 'Löschen')}
                          className="text-on-surface-variant hover:text-error"
                        >
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      </>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Live Preview Modal */}
      {previewPopup && (
        <PanelPopupModal
          popup={previewPopup}
          isPreview={true}
          onClose={() => setPreviewPopup(null)}
        />
      )}
    </div>
  )
}
