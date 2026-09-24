import { ArrowLeft, Check, ClipboardCopy, ClipboardPaste, Download, Pencil } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  aiApi,
  type AiMemoryImportPreview,
  type AiMemoryImportPreviewItem,
  type AiMemoryImportTarget,
} from '@/api/ai'
import { SanitizedApiError } from '@/api/client'
import {
  Badge,
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/Singra/UI'
import { toast } from '@/stores/toastStore'

import { type AiKnowledgeScope, memoryScopeName, scopeServerId, scopeTeamId } from './knowledgeScope'
import { memoryImportPrompt } from './memoryImportPrompt'

/** Dasselbe Muster wie `AiMemoryImportItem.key` im Backend. */
const SCHLUESSEL_RE = /^[A-Za-z0-9_.-]{1,64}$/
const MAX_TEXT = 100_000
const MAX_WERT = 2_000
/** Die Reihenfolge der Abschnitte im Auszugs-Prompt; Unbekanntes kommt zuletzt. */
const KATEGORIEN = ['demografie', 'vorlieben', 'soziales', 'projekte', 'anweisung', 'allgemein']

interface Zeile {
  item: AiMemoryImportPreviewItem
  key: string
  value: string
  auswahl: boolean
  /** Nur bei „ähnlich": statt eines eigenen Eintrags den bestehenden ersetzen. */
  ersetzen: boolean
}

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  scope: AiKnowledgeScope
  /** Nach einem Import mit mindestens einem geschriebenen Eintrag. */
  onImported: () => void | Promise<void>
}

/** Schon wortgleich gespeichert — dann gibt es nichts zu übernehmen. */
function istIdentisch(item: AiMemoryImportPreviewItem): boolean {
  return item.status === 'exact_duplicate' && item.existing_value === item.value
}

/**
 * Was eine Zeile ans Backend schickt.
 *
 * „Ähnlich" legt standardmäßig einen **eigenen** Eintrag an und ersetzt nur
 * auf ausdrücklichen Wunsch — dieselbe Abwägung wie in
 * `ai_memory_service.DUPLIKAT_AB`: ein fälschlich zusammengelegter Fakt ist
 * teurer als ein doppelter. Ein belegter Schlüssel wird ersetzt, solange der
 * Benutzer ihn nicht umbenennt; wer umbenennt, will einen neuen Eintrag.
 */
function eintrag(zeile: Zeile): { key: string; value: string; replace_existing: boolean } {
  const value = zeile.value.trim()
  if (zeile.item.status === 'similar_existing' && zeile.ersetzen && zeile.item.existing_key) {
    return { key: zeile.item.existing_key, value, replace_existing: true }
  }
  const key = zeile.key.trim()
  return {
    key,
    value,
    replace_existing: zeile.item.status === 'exact_duplicate' && key === zeile.item.key,
  }
}

function gueltig(zeile: Zeile): boolean {
  const { key, value } = eintrag(zeile)
  return SCHLUESSEL_RE.test(key) && value.length > 0 && value.length <= MAX_WERT
}

/**
 * Memory Bridge: Erinnerungen aus ChatGPT, Gemini oder Claude übernehmen.
 *
 * Zwei Bildschirme statt eines Formulars: erst Prompt kopieren und Antwort
 * einfügen, dann aussuchen. Wenig Text, weil der Weg selbst schon fremd genug
 * ist — was nur bei Bedarf zählt (Prompt, Schlüssel, Beleg), liegt hinter
 * einem Klick. Auf dem Telefon füllt der Dialog den Bildschirm.
 *
 * Aus der Liste fällt heraus, worüber es nichts zu entscheiden gibt: Gesperrtes
 * (Zugangsdaten) und wortgleich Gespeichertes stehen nur als Zahl darüber.
 *
 * Geschrieben wird erst beim letzten Klick; die Vorschau selbst speichert
 * nichts. Das Ziel ist der Bereich, in dem der Knopf steht.
 */
export function AiMemoryImportModal({ open, onOpenChange, scope, onImported }: Props) {
  const { t, i18n } = useTranslation()
  const [rohtext, setRohtext] = useState('')
  const [vorschau, setVorschau] = useState<AiMemoryImportPreview | null>(null)
  const [zeilen, setZeilen] = useState<Zeile[]>([])
  const [bearbeitet, setBearbeitet] = useState<number | null>(null)
  const [promptSichtbar, setPromptSichtbar] = useState(false)
  const [kopiert, setKopiert] = useState(false)
  const [busy, setBusy] = useState(false)

  const prompt = memoryImportPrompt(i18n.language)
  const kannEinfuegen = typeof navigator !== 'undefined' && typeof navigator.clipboard?.readText === 'function'
  const ziel: AiMemoryImportTarget = {
    scope: memoryScopeName(scope),
    ...(scopeTeamId(scope) !== undefined ? { team_id: scopeTeamId(scope) } : {}),
    ...(scopeServerId(scope) !== undefined ? { server_id: scopeServerId(scope) } : {}),
  }

  useEffect(() => {
    if (!open) return
    setRohtext(''); setVorschau(null); setZeilen([]); setBearbeitet(null)
    setPromptSichtbar(false); setKopiert(false); setBusy(false)
  }, [open])

  useEffect(() => {
    if (!kopiert) return
    const uhr = window.setTimeout(() => setKopiert(false), 2000)
    return () => window.clearTimeout(uhr)
  }, [kopiert])

  const ausgewaehlt = useMemo(() => zeilen.filter((zeile) => zeile.auswahl), [zeilen])
  const neue = ausgewaehlt.filter((zeile) => !eintrag(zeile).replace_existing).length
  const zuviel = vorschau ? Math.max(0, neue - vorschau.available_slots) : 0
  const ungueltig = ausgewaehlt.some((zeile) => !gueltig(zeile))
  const gesperrt = zeilen.filter((zeile) => zeile.item.status === 'has_secret').length
  const bekannt = zeilen.filter((zeile) => istIdentisch(zeile.item)).length
  const waehlbar = zeilen
    .map((zeile, index) => ({ zeile, index }))
    .filter(({ zeile }) => zeile.item.status !== 'has_secret' && !istIdentisch(zeile.item))
  const gruppen = KATEGORIEN
    .map((kategorie) => ({
      kategorie,
      eintraege: waehlbar.filter(({ zeile }) => (
        KATEGORIEN.includes(zeile.item.category) ? zeile.item.category : 'allgemein'
      ) === kategorie),
    }))
    .filter((gruppe) => gruppe.eintraege.length > 0)
  const alleGewaehlt = waehlbar.length > 0 && waehlbar.every(({ zeile }) => zeile.auswahl)

  const kopieren = async () => {
    try {
      await navigator.clipboard.writeText(prompt)
      setKopiert(true)
    } catch {
      setPromptSichtbar(true)
      toast.error(t('ai.memory.import.copyFailed'))
    }
  }

  const pruefen = async (text = rohtext) => {
    if (!text.trim() || busy) return
    setBusy(true)
    try {
      const ergebnis = await aiApi.importMemoryPreview({ ...ziel, raw_text: text })
      if (ergebnis.items.length === 0) {
        toast.info(t('ai.memory.import.nothingFound'))
        return
      }
      setVorschau(ergebnis)
      setBearbeitet(null)
      setZeilen(ergebnis.items.map((item) => ({
        item,
        key: item.key,
        value: item.value,
        auswahl: item.status === 'new' || item.status === 'similar_existing',
        ersetzen: false,
      })))
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.memory.import.failed'))
    } finally { setBusy(false) }
  }

  /** Ein Tipp statt Gedrückthalten und Menü — gerade auf dem Telefon. */
  const einfuegen = async () => {
    let text: string
    try {
      text = (await navigator.clipboard.readText()).slice(0, MAX_TEXT)
    } catch {
      toast.error(t('ai.memory.import.pasteFailed'))
      return
    }
    if (!text.trim()) return
    setRohtext(text)
    await pruefen(text)
  }

  const importieren = async () => {
    if (ausgewaehlt.length === 0 || zuviel > 0 || ungueltig || busy) return
    setBusy(true)
    try {
      const ergebnis = await aiApi.executeMemoryImport({
        ...ziel,
        ...(vorschau?.detected_source ? { source_provider: vorschau.detected_source } : {}),
        items: ausgewaehlt.map(eintrag),
      })
      if (ergebnis.imported_count + ergebnis.updated_count > 0) {
        toast.success(t('ai.memory.import.done', {
          imported: ergebnis.imported_count, updated: ergebnis.updated_count,
        }))
        await onImported()
      }
      if (ergebnis.skipped_count > 0) {
        toast.warning(t('ai.memory.import.skipped', { count: ergebnis.skipped_count }))
      }
      onOpenChange(false)
    } catch (error: unknown) {
      toast.error(error instanceof SanitizedApiError ? error.message : t('ai.memory.import.failed'))
    } finally { setBusy(false) }
  }

  const aendern = (index: number, teil: Partial<Zeile>) => {
    setZeilen((vorher) => vorher.map((zeile, i) => (i === index ? { ...zeile, ...teil } : zeile)))
  }

  const alleUmschalten = () => {
    const auswahl = !alleGewaehlt
    const indizes = new Set(waehlbar.map(({ index }) => index))
    setZeilen((vorher) => vorher.map((zeile, i) => (indizes.has(i) ? { ...zeile, auswahl } : zeile)))
  }

  const zeileAnzeigen = (zeile: Zeile, index: number) => {
    const { item } = zeile
    const offen = bearbeitet === index
    const schluesselFalsch = zeile.auswahl && !zeile.ersetzen && !SCHLUESSEL_RE.test(zeile.key.trim())
    const ersetzt = zeile.auswahl && eintrag(zeile).replace_existing
    return (
      <li
        key={`${item.key}-${index}`}
        className={`flex items-start gap-3 rounded-xl border px-3 py-2.5 transition-colors ${
          schluesselFalsch
            ? 'border-status-destructive/60 bg-primary/5'
            : zeile.auswahl
              ? 'border-primary/40 bg-primary/5'
              : 'border-outline-variant/30 bg-surface-container-lowest/60'
        }`}
      >
        {/* Der Rand um das Kästchen vergrößert die Trefferfläche auf Fingerbreite. */}
        <label className="-m-2 flex shrink-0 cursor-pointer p-2.5">
          <Checkbox
            checked={zeile.auswahl}
            disabled={busy}
            onCheckedChange={(checked) => aendern(index, { auswahl: checked })}
            aria-label={t('ai.memory.import.select', { key: item.key })}
          />
        </label>

        <div className="min-w-0 flex-1 space-y-1.5">
          {offen ? (
            <div className="space-y-2">
              <textarea
                className="msm-input min-h-[4.5rem] text-sm"
                maxLength={MAX_WERT}
                value={zeile.value}
                disabled={busy}
                autoFocus
                onChange={(event) => aendern(index, { value: event.target.value })}
                aria-label={t('ai.memory.import.valueLabel')}
              />
              <input
                className="msm-input font-mono text-xs"
                maxLength={64}
                value={zeile.ersetzen && item.existing_key ? item.existing_key : zeile.key}
                disabled={busy || zeile.ersetzen}
                aria-invalid={schluesselFalsch}
                onChange={(event) => aendern(index, { key: event.target.value })}
                aria-label={t('ai.memory.import.keyLabel')}
              />
              {item.evidence && (
                <p className="break-words text-label-sm italic text-on-surface-variant/80">
                  {t('ai.memory.import.evidence', { text: item.evidence })}
                </p>
              )}
            </div>
          ) : (
            <button
              type="button"
              className="block w-full break-words text-left text-sm text-on-surface"
              onClick={() => setBearbeitet(index)}
            >
              {zeile.value}
            </button>
          )}
          {schluesselFalsch && (
            <p className="text-label-sm text-status-destructive">{t('ai.memory.import.invalidKey')}</p>
          )}

          {item.status === 'exact_duplicate' && (
            <p className="break-words text-xs text-on-surface-variant">
              {item.existing_value === null
                ? t('ai.memory.import.previousUnreadable')
                : t('ai.memory.import.previous', { value: item.existing_value })}
            </p>
          )}
          {item.status === 'similar_existing' && item.existing_value !== null && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-on-surface-variant">
              <span className="min-w-0 break-words">
                {t('ai.memory.import.similarTo', { value: item.existing_value })}
              </span>
              {zeile.auswahl && (
                <button
                  type="button"
                  aria-pressed={zeile.ersetzen}
                  disabled={busy}
                  onClick={() => aendern(index, { ersetzen: !zeile.ersetzen })}
                  className={`min-h-8 rounded-full border px-3 text-xs transition-colors ${
                    zeile.ersetzen
                      ? 'border-primary/60 bg-primary/15 font-medium text-primary'
                      : 'border-outline-variant/40 text-on-surface-variant hover:text-on-surface'
                  }`}
                >
                  {t('ai.memory.import.replace')}
                </button>
              )}
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-1">
          {ersetzt && <Badge variant="warning">{t('ai.memory.import.replaces')}</Badge>}
          {item.status === 'similar_existing' && !ersetzt && (
            <Badge variant="info">{t('ai.memory.import.similar')}</Badge>
          )}
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => setBearbeitet(offen ? null : index)}
            aria-label={offen ? t('ai.memory.import.editDone') : t('ai.memory.import.edit')}
          >
            {offen ? <Check className="h-4 w-4" aria-hidden="true" /> : <Pencil className="h-4 w-4" aria-hidden="true" />}
          </Button>
        </div>
      </li>
    )
  }

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!busy) onOpenChange(next) }}>
      <DialogContent
        className="max-w-2xl p-0 max-sm:h-[100dvh] max-sm:max-w-none max-sm:rounded-none"
        overlayClassName="max-sm:p-0"
        aria-labelledby="ai-memory-import-title"
      >
        <DialogHeader className="py-4 pr-14">
          <DialogTitle id="ai-memory-import-title" className="flex items-center gap-2">
            <Download className="h-5 w-5" aria-hidden="true" />
            {t('ai.memory.import.title')}
          </DialogTitle>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4 sm:max-h-[calc(100vh-12rem)] sm:px-6">
          {vorschau === null ? (
            <>
              <section className="space-y-2">
                <p className="text-sm font-semibold text-on-surface">{t('ai.memory.import.copyStep')}</p>
                <p className="text-xs text-on-surface-variant">{t('ai.memory.import.copyHint')}</p>
                <div className="flex flex-wrap items-center gap-2">
                  <Button type="button" onClick={() => void kopieren()}>
                    {kopiert
                      ? <Check className="h-4 w-4" aria-hidden="true" />
                      : <ClipboardCopy className="h-4 w-4" aria-hidden="true" />}
                    {kopiert ? t('ai.memory.import.copied') : t('ai.memory.import.copy')}
                  </Button>
                  <Button type="button" variant="ghost" size="sm" onClick={() => setPromptSichtbar((wert) => !wert)}>
                    {promptSichtbar ? t('ai.memory.import.hidePrompt') : t('ai.memory.import.showPrompt')}
                  </Button>
                </div>
                {promptSichtbar && (
                  <textarea
                    className="msm-input h-40 font-mono text-xs"
                    readOnly
                    value={prompt}
                    aria-label={t('ai.memory.import.promptLabel')}
                    onFocus={(event) => event.currentTarget.select()}
                  />
                )}
              </section>

              <section className="space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-on-surface">{t('ai.memory.import.pasteStep')}</p>
                  {kannEinfuegen && (
                    <Button type="button" variant="secondary" size="sm" disabled={busy} onClick={() => void einfuegen()}>
                      <ClipboardPaste className="h-4 w-4" aria-hidden="true" />
                      {t('ai.memory.import.paste')}
                    </Button>
                  )}
                </div>
                <textarea
                  className="msm-input min-h-[9rem] font-mono text-xs"
                  maxLength={MAX_TEXT}
                  value={rohtext}
                  disabled={busy}
                  onChange={(event) => setRohtext(event.target.value)}
                  placeholder={t('ai.memory.import.answerPlaceholder')}
                  aria-label={t('ai.memory.import.answerLabel')}
                />
              </section>
            </>
          ) : (
            <section className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-semibold text-on-surface">
                  {t('ai.memory.import.found', { count: waehlbar.length })}
                </p>
                {waehlbar.length > 1 && (
                  <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={alleUmschalten}>
                    {alleGewaehlt ? t('ai.memory.import.selectNone') : t('ai.memory.import.selectAll')}
                  </Button>
                )}
              </div>

              {(gesperrt > 0 || bekannt > 0 || !vorschau.memory_enabled
                || vorschau.total_detected > vorschau.items.length) && (
                <ul className="space-y-1 text-xs text-on-surface-variant">
                  {gesperrt > 0 && <li>{t('ai.memory.import.blocked', { count: gesperrt })}</li>}
                  {bekannt > 0 && <li>{t('ai.memory.import.known', { count: bekannt })}</li>}
                  {vorschau.total_detected > vorschau.items.length && (
                    <li>{t('ai.memory.import.truncated', { found: vorschau.total_detected, max: vorschau.items.length })}</li>
                  )}
                  {!vorschau.memory_enabled && <li>{t('ai.memory.import.memoryOff')}</li>}
                </ul>
              )}

              {gruppen.map((gruppe) => (
                <div key={gruppe.kategorie} className="space-y-2">
                  {gruppen.length > 1 && (
                    <p className="text-label-sm font-semibold uppercase tracking-wider text-on-surface-variant">
                      {t(`ai.memory.import.categories.${gruppe.kategorie}`)}
                    </p>
                  )}
                  <ul className="space-y-2">
                    {gruppe.eintraege.map(({ zeile, index }) => zeileAnzeigen(zeile, index))}
                  </ul>
                </div>
              ))}
            </section>
          )}
        </div>

        <DialogFooter className="flex-wrap gap-y-2 max-sm:pb-[max(1rem,env(safe-area-inset-bottom))]">
          {vorschau !== null && zuviel > 0 && (
            <p className="mr-auto text-xs text-status-warning">
              {t('ai.memory.import.tooMany', { over: zuviel })}
            </p>
          )}
          {vorschau === null ? (
            <>
              <Button type="button" variant="ghost" disabled={busy} onClick={() => onOpenChange(false)}>
                {t('common.cancel')}
              </Button>
              <Button type="button" disabled={busy || !rohtext.trim()} onClick={() => void pruefen()}>
                {t('ai.memory.import.analyze')}
              </Button>
            </>
          ) : (
            <>
              <Button
                type="button"
                variant="ghost"
                disabled={busy}
                onClick={() => { setVorschau(null); setZeilen([]); setBearbeitet(null) }}
              >
                <ArrowLeft className="h-4 w-4" aria-hidden="true" />
                {t('ai.memory.import.back')}
              </Button>
              <Button
                type="button"
                disabled={busy || ausgewaehlt.length === 0 || zuviel > 0 || ungueltig}
                onClick={() => void importieren()}
              >
                <Download className="h-4 w-4" aria-hidden="true" />
                {t('ai.memory.import.submit', { count: ausgewaehlt.length })}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
