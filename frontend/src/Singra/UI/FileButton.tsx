import { useRef, type ReactNode } from 'react'
import { Button, type ButtonSize, type ButtonVariant } from '@/components/ui/Button'

export interface FileButtonProps {
  /** Dateitypen wie beim `accept`-Attribut, z. B. ".csv,.json". */
  accept?: string
  onFile?: (file: File) => void
  /** Mehrere Dateien auf einmal; dann kommt die ganze Auswahl hier an. */
  multiple?: boolean
  onFiles?: (files: File[]) => void
  children: ReactNode
  variant?: ButtonVariant
  size?: ButtonSize
  disabled?: boolean
  className?: string
  'data-testid'?: string
}

/**
 * Dateiauswahl als Knopf der Design-DNA. Das native Dateifeld bleibt
 * unsichtbar und wird nach jeder Wahl geleert — dieselbe Datei lässt sich
 * so ein zweites Mal wählen.
 */
export function FileButton({ accept, onFile, multiple, onFiles, children, variant = 'secondary', size = 'md', disabled, className, 'data-testid': testId }: FileButtonProps) {
  const input = useRef<HTMLInputElement>(null)
  return (
    <>
      <Button type="button" variant={variant} size={size} disabled={disabled} className={className} onClick={() => input.current?.click()}>
        {children}
      </Button>
      <input
        ref={input}
        type="file"
        accept={accept}
        multiple={multiple}
        className="hidden"
        data-testid={testId}
        onChange={(event) => {
          const files = Array.from(event.target.files ?? [])
          event.target.value = ''
          if (files.length === 0) return
          if (onFiles) onFiles(files)
          else onFile?.(files[0])
        }}
      />
    </>
  )
}
