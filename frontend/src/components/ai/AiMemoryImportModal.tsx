import { ArrowLeft, Check, ClipboardCopy, ClipboardPaste, Download, Loader2, Pencil } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  aiApi,
  type AiMemoryImportItem,
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

const MAX_TEXT = 100_000
const MAX_WERT = 2_000

interface Zeile {
  item: AiMemoryImportPreviewItem
  text: string
  auswahl: boolean
  /** Nur mit Bestand: den bestehenden Eintrag ersetzen statt einen eigenen anzulegen. */
  ersetzen: boolean
}

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  scope: AiKnowledgeScope
  /** Nach einem Import mit mindestens einer geschriebenen Erinnerung. */
  onImported: () => void | Promise<void>
}

/**
 * Was eine Zeile ans Backend schickt.
 *
 * Schlägt das Modell vor, einen Eintrag zu ersetzen, ist das die Vorgabe:
 * meist sind es Bruchstücke eines früheren Imports, die im neuen Satz
 * aufgehen. Wer abwählt, legt einen eigenen Eintrag an.
 */
function eintrag(zeile: Zeile): AiMemoryImportItem {
  const { item } = zeile
  return {
    text: zeile.text.trim(),
    titel: item.titel,
    thema: item.thema,
    art: item.art,
    wichtigkeit: item.wichtigkeit,
    ersetzt: zeile.ersetzen ? item.ersetzt.map(({ id, fassung }) => ({ id, fassung })) : [],
  }
}

function gueltig(zeile: Zeile): boolean {
  const text = zeile.text.trim()
  return text.length > 0 && text.length <= MAX_WERT
}

/**
 * Erinnerungen importieren: aus ChatGPT, Gemini oder Claude — oder aus einem
 * eigenen Text, etwa Notizen oder einer Lebensgeschichte.
 *
 * Zwei Bildschirme statt eines Formulars: erst Prompt kopieren und Antwort
 * einfügen, dann aussuchen. Den Text liest das Gedächtnis-Modell; dass er
 * dafür einmal an den Anbieter geht, steht über dem Feld, bevor er abgeschickt
 * wird. Die Vorschau ordnet nach Thema und schreibt nichts.
 *
 * Aus der Liste fällt heraus, worüber es nichts zu entscheiden gibt: Gesperrtes
 * (Zugangsdaten) und schon Gespeichertes stehen nur als Zahl darüber.
 *
 * Geschrieben wird erst beim letzten Klick. Das Ziel ist der Bereich, in dem
 * der Knopf steht.
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
  const ersetzend = ausgewaehlt.filter((zeile) => zeile.ersetzen && zeile.item.ersetzt.length > 0)
  const neue = ausgewaehlt.length - ersetzend.length
  // Wer ersetzt, kostet keinen Platz, und jeder weitere ersetzte Eintrag geht
  // im ersten auf und macht einen frei — die Übernahme schreibt Ersetzungen
  // deshalb zuerst.
  const frei = ersetzend.reduce((summe, zeile) => summe + zeile.item.ersetzt.length - 1, 0)
  // `null` heißt unbegrenzt: dann ist nichts zu viel.
  const zuviel = vorschau && vorschau.available_slots !== null
    ? Math.max(0, neue - vorschau.available_slots - frei)
    : 0
  const ungueltig = ausgewaehlt.some((zeile) => !gueltig(zeile))
  const alleGewaehlt = zeilen.length > 0 && zeilen.every((zeile) => zeile.auswahl)

  // Nach Thema, gleich geschriebene zusammen; ohne Thema zuletzt.
  const gruppen = useMemo(() => {
    const nachThema = new Map<string, { name: string | null; eintraege: { zeile: Zeile; index: number }[] }>()
    zeilen.forEach((zeile, index) => {
      const name = zeile.item.thema?.trim() || null
      const schluessel = name?.toLocaleLowerCase() ?? ''
      const gruppe = nachThema.get(schluessel) ?? { name, eintraege: [] }
      gruppe.eintraege.push({ zeile, index })
      nachThema.set(schluessel, gruppe)
    })
    return [...nachThema.values()].sort((a, b) => {
      if (a.name === null) return 1
      if (b.name === null) return -1
      return a.name.localeCompare(b.name, i18n.language)
    })
  }, [zeilen, i18n.language])

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
      const nichts = ergebnis.items.length === 0 && ergebnis.total_known === 0
        && ergebnis.unread_parts === 0 && ergebnis.total_secrets_blocked === 0
      if (nichts) {
        toast.info(t('ai.memory.import.nothingFound'))
        return
      }
      setVorschau(ergebnis)
      setBearbeitet(null)
      setZeilen(ergebnis.items.map((item) => ({
        item,
        text: item.text,
        auswahl: true,
        ersetzen: item.ersetzt.length > 0,
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
    setZeilen((vorher) => vorher.map((zeile) => ({ ...zeile, auswahl })))
  }

  const zeileAnzeigen = (zeile: Zeile, index: number) => {
    const { item } = zeile
    const offen = bearbeitet === index
    const ersetzt = zeile.auswahl && zeile.ersetzen && item.ersetzt.length > 0
    const [erstes, ...weitere] = item.ersetzt
    return (
      <li
        key={index}
        className={`flex items-start gap-3 rounded-xl border px-3 py-2.5 transition-colors ${
          zeile.auswahl
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
            aria-label={t('ai.memory.import.select', { title: item.titel ?? zeile.text.slice(0, 40) })}
          />
        </label>

        <div className="min-w-0 flex-1 space-y-1.5">
          {item.titel && <p className="break-words text-xs font-semibold text-on-surface">{item.titel}</p>}
          {offen ? (
            <textarea
              className="msm-input min-h-[4.5rem] text-sm"
              maxLength={MAX_WERT}
              value={zeile.text}
              disabled={busy}
              autoFocus
              onChange={(event) => aendern(index, { text: event.target.value })}
              aria-label={t('ai.memory.import.valueLabel')}
            />
          ) : (
            <button
              type="button"
              className="block w-full break-words text-left text-sm text-on-surface"
              onClick={() => setBearbeitet(index)}
            >
              {zeile.text}
            </button>
          )}

          {erstes && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-on-surface-variant">
              <span className="min-w-0 break-words">
                {t('ai.memory.import.replacesText', { value: erstes.text })}
                {weitere.length > 0 && ` ${t('ai.memory.import.replacesMore', { count: weitere.length })}`}
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
                <p className="text-xs text-on-surface-variant">{t('ai.memory.import.providerNotice')}</p>
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
                  {t('ai.memory.import.found', { count: zeilen.length })}
                </p>
                {zeilen.length > 1 && (
                  <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={alleUmschalten}>
                    {alleGewaehlt ? t('ai.memory.import.selectNone') : t('ai.memory.import.selectAll')}
                  </Button>
                )}
              </div>

              {(vorschau.total_secrets_blocked > 0 || vorschau.total_known > 0 || vorschau.unread_parts > 0
                || !vorschau.memory_enabled || vorschau.total_detected > vorschau.items.length) && (
                <ul className="space-y-1 text-xs text-on-surface-variant">
                  {vorschau.total_secrets_blocked > 0 && (
                    <li>{t('ai.memory.import.blocked', { count: vorschau.total_secrets_blocked })}</li>
                  )}
                  {vorschau.total_known > 0 && (
                    <li>{t('ai.memory.import.known', { count: vorschau.total_known })}</li>
                  )}
                  {vorschau.unread_parts > 0 && (
                    <li className="text-status-warning">{t('ai.memory.import.unread', { count: vorschau.unread_parts })}</li>
                  )}
                  {vorschau.total_detected > vorschau.items.length && (
                    <li>{t('ai.memory.import.truncated', { found: vorschau.total_detected, max: vorschau.items.length })}</li>
                  )}
                  {!vorschau.memory_enabled && <li>{t('ai.memory.import.memoryOff')}</li>}
                </ul>
              )}

              {gruppen.map((gruppe) => (
                <div key={gruppe.name ?? ''} className="space-y-2">
                  {gruppen.length > 1 && (
                    <p className="text-label-sm font-semibold uppercase tracking-wider text-on-surface-variant">
                      {gruppe.name ?? t('ai.memory.import.noTopic')}
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

        <DialogFooter className="flex-wrap gap-y-2 max-sm:pb-[max(1rem,var(--msm-unten-sicher))]">
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
                {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                {busy ? t('ai.memory.import.reading') : t('ai.memory.import.analyze')}
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
