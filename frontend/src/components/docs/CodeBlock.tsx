import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Terminal } from 'lucide-react'
import { Check, Clipboard } from 'lucide-react'

/**
 * Kopierbarer Code- oder Befehlsblock fuer die Doku-Seiten.
 *
 * Bewusst mit `<pre><code>` statt Syntax-Highlighting: der Inhalt muss sich
 * exakt so markieren und kopieren lassen, wie er einzugeben ist. Eine
 * Hervorhebung, die Zeichen einfuegt oder umbricht, waere hier ein Risiko —
 * besonders beim HMAC-Beispiel, wo ein einziges Zeichen die Signatur aendert.
 */
export function CodeBlock({
  code,
  label,
  testId,
  copyLabel,
  copiedLabel,
}: {
  code: string
  label: string
  testId?: string
  copyLabel?: string
  copiedLabel?: string
}) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  const copyText = copyLabel ?? t('docsCommon.copy')
  const copiedText = copiedLabel ?? t('docsCommon.copied')

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // Die Zwischenablage ist nur Komfort; ein Fehler wird nicht als Erfolg gezeigt.
    }
  }

  return (
    <div className="relative mt-4 overflow-hidden rounded-xl border border-outline-variant bg-surface-container-lowest">
      <div className="flex items-center justify-between gap-3 border-b border-outline-variant px-4 py-2.5">
        <span className="inline-flex items-center gap-2 font-label-md text-xs uppercase tracking-wider text-on-surface-variant">
          <Terminal className="h-4 w-4 text-primary" />
          {label}
        </span>
        <button
          type="button"
          onClick={() => void copy()}
          className="msm-btn-secondary inline-flex items-center gap-2 px-3 py-1.5 text-xs"
          aria-label={copied ? copiedText : copyText}
        >
          {copied ? <Check className="h-3.5 w-3.5 text-status-success" /> : <Clipboard className="h-3.5 w-3.5" />}
          {copied ? copiedText : copyText}
        </button>
      </div>
      <pre className="overflow-x-auto p-4 font-mono text-xs leading-6 text-on-surface sm:text-sm">
        <code data-testid={testId}>{code}</code>
      </pre>
      <span className="sr-only" role="status" aria-live="polite">
        {copied ? copiedText : ''}
      </span>
    </div>
  )
}
