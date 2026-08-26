import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/**
 * Rendert Modellausgabe als Markdown.
 *
 * Sicherheitshinweis: `react-markdown` erzeugt React-Elemente und benutzt
 * bewusst **kein** `dangerouslySetInnerHTML`. Roher HTML-Text in der Antwort
 * wird als Text ausgegeben, solange kein `rehype-raw` eingebunden ist — und das
 * wird hier absichtlich nicht getan. Modellausgabe ist Fremdtext; sie darf
 * niemals Markup in unsere Seite einbringen.
 *
 * Links oeffnen in einem neuen Tab mit `rel="noreferrer"`, damit ein vom Modell
 * erzeugter Link weder die Panelsitzung mitnimmt noch die Seite ersetzt.
 */
export const AiMarkdown = memo(function AiMarkdown({ content }: { content: string }) {
  return (
    <div className="text-base sm:text-sm leading-relaxed sm:leading-6 text-on-surface">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer noopener"
              className="text-primary underline underline-offset-2 hover:text-primary/80"
            >
              {children}
            </a>
          ),
          code: ({ className, children }) => {
            // `react-markdown` unterscheidet Inline- von Blockcode ueber die
            // Sprachklasse des umschliessenden <pre>. Ohne Klasse ist es Inline.
            const isBlock = Boolean(className)
            if (isBlock) {
              return <code className="font-mono text-xs">{children}</code>
            }
            return (
              <code className="rounded bg-surface-container-highest px-1.5 py-0.5 font-mono text-[0.85em] text-on-surface">
                {children}
              </code>
            )
          },
          pre: ({ children }) => (
            <pre className="my-2 overflow-x-auto rounded-lg border border-outline-variant/40 bg-surface-container-low/60 p-3">
              {children}
            </pre>
          ),
          table: ({ children }) => (
            <div className="my-2 overflow-x-auto">
              <table className="w-full border-collapse text-xs">{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th className="border border-outline-variant/40 bg-surface-container-high px-2 py-1 text-left font-semibold">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-outline-variant/40 px-2 py-1 align-top">{children}</td>
          ),
          ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
          ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
          p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
          h1: ({ children }) => <h3 className="mt-3 mb-1 font-headline text-base font-semibold">{children}</h3>,
          h2: ({ children }) => <h4 className="mt-3 mb-1 font-headline text-sm font-semibold">{children}</h4>,
          // Die Ebenen sind bewusst um zwei nach unten verschoben, damit Modelltext
          // die Gliederung der Seite nicht kapert. Ebene 4 bis 6 landen alle auf
          // <h6>, weil es darunter kein Element mehr gibt.
          h3: ({ children }) => <h5 className="mt-3 mb-1 font-headline text-sm font-semibold">{children}</h5>,
          h4: ({ children }) => <h6 className="mt-3 mb-1 font-headline text-sm font-semibold">{children}</h6>,
          h5: ({ children }) => <h6 className="mt-3 mb-1 font-headline text-sm font-semibold">{children}</h6>,
          h6: ({ children }) => <h6 className="mt-3 mb-1 font-headline text-sm font-semibold">{children}</h6>,
          blockquote: ({ children }) => (
            <blockquote className="my-2 border-l-2 border-outline-variant/60 pl-3 text-on-surface-variant">
              {children}
            </blockquote>
          ),
          hr: () => <hr className="my-3 border-outline-variant/40" />,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
})
