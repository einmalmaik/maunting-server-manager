/**
 * FilePreview – displays the content of a text or JSON file.
 *
 * Calls the Rust `read_text_file` command to load the file, then renders it.
 * JSON files are pretty-printed with syntax highlighting (keys, strings,
 * numbers, booleans, null each get a distinct token color via Design-DNA
 * CSS classes). Plain text files are shown in a monospace block.
 *
 * VAL-EXTRACT-003: text file preview shows content
 * VAL-EXTRACT-004: JSON file preview shows formatted content
 */

import { useEffect, useState, type ReactElement } from 'react';
import type { FileTreeNode } from '@/lib/tauri-commands';
import { readTextFile } from '@/lib/tauri-commands';
import { useLanguage } from '@/lib/useLanguage';

/** Returns true if the file name looks like a JSON file. */
function isJsonFile(name: string): boolean {
  return name.toLowerCase().endsWith('.json');
}

/** Returns true if the file name looks like a text file (previewable). */
function isTextFile(name: string): boolean {
  const lower = name.toLowerCase();
  return (
    lower.endsWith('.txt') ||
    lower.endsWith('.json') ||
    lower.endsWith('.md') ||
    lower.endsWith('.log') ||
    lower.endsWith('.csv') ||
    lower.endsWith('.yml') ||
    lower.endsWith('.yaml') ||
    lower.endsWith('.conf') ||
    lower.endsWith('.cfg') ||
    lower.endsWith('.ini') ||
    lower.endsWith('.sh') ||
    lower.endsWith('.env')
  );
}

/**
 * Pretty-print a JSON string with indentation. Returns the raw string if the
 * input is not valid JSON so the user still sees *something* rather than a
 * blank panel.
 */
function tryFormatJson(raw: string): { formatted: string; valid: boolean } {
  try {
    const parsed = JSON.parse(raw);
    return { formatted: JSON.stringify(parsed, null, 2), valid: true };
  } catch {
    return { formatted: raw, valid: false };
  }
}

/**
 * Render JSON with syntax highlighting. Splits the pretty-printed JSON into
 * tokens (keys, strings, numbers, booleans, null) and wraps each in a
 * `<span>` with a Design-DNA color class.
 *
 * The highlighting is done with a simple regex-based tokenizer to avoid
 * pulling in a syntax-highlighting dependency (KISS).
 */
function renderJsonHighlight(json: string): ReactElement[] {
  // Token pattern: strings (with optional colon for keys), numbers, booleans, null
  const tokenRegex =
    /("(?:[^"\\]|\\.)*")(\s*:)?|(\b-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|(\btrue\b|\bfalse\b)|(\bnull\b)/g;

  const lines = json.split('\n');
  return lines.map((line, lineIdx) => {
    const parts: ReactElement[] = [];
    let lastIdx = 0;
    let match: RegExpExecArray | null;
    tokenRegex.lastIndex = 0;

    while ((match = tokenRegex.exec(line)) !== null) {
      // Text before the token
      if (match.index > lastIdx) {
        parts.push(
          <span key={`txt-${lineIdx}-${lastIdx}`}>{line.slice(lastIdx, match.index)}</span>,
        );
      }
      if (match[1] !== undefined) {
        // String (key if followed by colon, else value)
        const isKey = match[2] !== undefined;
        parts.push(
          <span key={`str-${lineIdx}-${match.index}`} className={isKey ? 'json-key' : 'json-string'}>
            {match[1]}
          </span>,
        );
        if (match[2] !== undefined) {
          parts.push(<span key={`colon-${lineIdx}-${match.index}`}>{match[2]}</span>);
        }
      } else if (match[3] !== undefined) {
        parts.push(
          <span key={`num-${lineIdx}-${match.index}`} className="json-number">
            {match[3]}
          </span>,
        );
      } else if (match[4] !== undefined) {
        parts.push(
          <span key={`bool-${lineIdx}-${match.index}`} className="json-boolean">
            {match[4]}
          </span>,
        );
      } else if (match[5] !== undefined) {
        parts.push(
          <span key={`null-${lineIdx}-${match.index}`} className="json-null">
            {match[5]}
          </span>,
        );
      }
      lastIdx = match.index + match[0].length;
    }
    // Remaining text after the last token
    if (lastIdx < line.length) {
      parts.push(<span key={`rem-${lineIdx}-${lastIdx}`}>{line.slice(lastIdx)}</span>);
    }
    if (parts.length === 0) {
      parts.push(<span key={`empty-${lineIdx}`}>&nbsp;</span>);
    }
    return (
      <div key={`line-${lineIdx}`} className="json-line">
        {parts}
      </div>
    );
  });
}

export interface FilePreviewProps {
  /** The selected file node from the tree, or null if nothing is selected. */
  file: FileTreeNode | null;
  /** Override the read function for tests. */
  readFileFn?: (path: string) => Promise<string>;
}

export function FilePreview({ file, readFileFn }: FilePreviewProps) {
  const { t } = useLanguage();
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!file) {
      setContent(null);
      setError(null);
      setLoading(false);
      return;
    }

    if (!isTextFile(file.name)) {
      setContent(null);
      setError(t('preview.binary'));
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);
    setContent(null);

    const readFn = readFileFn ?? readTextFile;
    readFn(file.path)
      .then((text) => {
        if (cancelled) return;
        setContent(text);
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(typeof err === 'string' ? err : t('preview.error'));
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [file, readFileFn, t]);

  // Empty state – no file selected
  if (!file) {
    return (
      <div
        className="msm-card flex items-center justify-center p-6"
        data-testid="file-preview"
      >
        <p className="text-sm text-muted-foreground/60">{t('preview.empty')}</p>
      </div>
    );
  }

  // Binary file state
  if (error && !loading && !content) {
    return (
      <div
        className="msm-card flex items-center justify-center p-6"
        data-testid="file-preview"
      >
        <p className="text-sm text-muted-foreground" data-testid="preview-error">
          {error}
        </p>
      </div>
    );
  }

  const isJson = isJsonFile(file.name);
  const jsonResult = isJson && content ? tryFormatJson(content) : null;
  const displayContent = jsonResult ? jsonResult.formatted : content;

  return (
    <div
      className="msm-card flex flex-col gap-2 overflow-hidden p-4"
      data-testid="file-preview"
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="truncate text-sm font-medium text-foreground" data-testid="preview-filename">
          {file.name}
        </h3>
        {isJson && jsonResult?.valid ? (
          <span className="shrink-0 text-xs text-accent" data-testid="preview-json-badge">
            JSON
          </span>
        ) : null}
      </div>
      <div className="msm-preview-body overflow-auto">
        {loading ? (
          <div className="flex items-center justify-center py-8" data-testid="preview-loading">
            <span className="msm-spinner" aria-hidden="true" />
            <span className="ml-2 text-sm text-muted-foreground">{t('preview.loading')}</span>
          </div>
        ) : isJson && displayContent ? (
          <pre className="msm-code-block" data-testid="preview-json">
            <code>{renderJsonHighlight(displayContent)}</code>
          </pre>
        ) : displayContent ? (
          <pre className="msm-code-block" data-testid="preview-text">
            <code>{displayContent}</code>
          </pre>
        ) : null}
      </div>
    </div>
  );
}
