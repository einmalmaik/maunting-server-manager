/**
 * Zeigt ein Markdown-Dokument gelesen statt als Quelltext, etwa eine
 * README im Tresor.
 *
 * Fremdtext darf nichts nachladen und kein Markup einschleusen:
 * - kein rohes HTML (`react-markdown` ohne `rehype-raw` gibt es als Text aus),
 * - keine Bilder: eine Adresse im Netz würde beim bloßen Öffnen IP und
 *   Zeitpunkt verraten, ein relativer Pfad zeigt ins Panel. Stattdessen steht
 *   der Alt-Text da.
 * - Links öffnen getrennt, ohne Referrer.
 */
import { memo } from 'react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ImageOff } from 'lucide-react'

/**
 * Ersatz für `img` in jedem Markdown aus fremder Hand (Tresor, KI-Antwort,
 * Notizen): zeigt den Alt-Text, lädt nichts.
 */
export function MarkdownBild({ alt }: { alt?: string }) {
  const { t } = useTranslation()
  return (
    <span className="my-1 inline-flex items-center gap-1.5 rounded border border-outline-variant/50 px-2 py-0.5 text-label-sm text-on-surface-variant">
      <ImageOff className="h-3.5 w-3.5" aria-hidden />
      {alt ? t('common.markdown.bildMitText', { text: alt }) : t('common.markdown.bild')}
    </span>
  )
}

export const Markdownansicht = memo(function Markdownansicht({ text }: { text: string }) {
  return (
    <article className="mx-auto w-full max-w-3xl break-words px-4 py-6 text-base leading-relaxed text-on-surface sm:px-8 sm:text-sm sm:leading-6">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          img: MarkdownBild,
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer noopener" className="text-primary underline underline-offset-2 hover:text-primary/80">
              {children}
            </a>
          ),
          code: ({ className, children }) =>
            className ? (
              <code className="font-mono text-xs">{children}</code>
            ) : (
              <code className="rounded bg-surface-container-highest px-1.5 py-0.5 font-mono text-[0.85em]">{children}</code>
            ),
          pre: ({ children }) => <pre className="my-3 overflow-x-auto rounded-lg border border-outline-variant/40 bg-surface-container-low/60 p-3">{children}</pre>,
          table: ({ children }) => (
            <div className="my-3 overflow-x-auto">
              <table className="w-full border-collapse text-sm">{children}</table>
            </div>
          ),
          th: ({ children }) => <th className="border border-outline-variant/40 bg-surface-container-high px-2 py-1 text-left font-semibold">{children}</th>,
          td: ({ children }) => <td className="border border-outline-variant/40 px-2 py-1 align-top">{children}</td>,
          ul: ({ children }) => <ul className="my-3 list-disc space-y-1 pl-6">{children}</ul>,
          ol: ({ children }) => <ol className="my-3 list-decimal space-y-1 pl-6">{children}</ol>,
          p: ({ children }) => <p className="my-3 first:mt-0 last:mb-0">{children}</p>,
          h1: ({ children }) => <h1 className="mb-3 mt-6 font-headline text-2xl font-semibold first:mt-0">{children}</h1>,
          h2: ({ children }) => <h2 className="mb-2 mt-6 border-b border-outline-variant/40 pb-1 font-headline text-xl font-semibold">{children}</h2>,
          h3: ({ children }) => <h3 className="mb-2 mt-5 font-headline text-lg font-semibold">{children}</h3>,
          h4: ({ children }) => <h4 className="mb-1 mt-4 font-semibold">{children}</h4>,
          h5: ({ children }) => <h5 className="mb-1 mt-4 font-semibold">{children}</h5>,
          h6: ({ children }) => <h6 className="mb-1 mt-4 font-semibold text-on-surface-variant">{children}</h6>,
          blockquote: ({ children }) => <blockquote className="my-3 border-l-2 border-outline-variant/60 pl-3 text-on-surface-variant">{children}</blockquote>,
          hr: () => <hr className="my-5 border-outline-variant/40" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </article>
  )
})
