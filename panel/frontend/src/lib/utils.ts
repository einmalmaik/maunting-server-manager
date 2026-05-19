import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import { ApiError } from './api'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatDateTime(iso: string): string {
  const date = new Date(iso)
  if (isNaN(date.getTime())) return iso
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

/** Get a user-friendly error message from an API error */
export function getErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    return err.getUserMessage()
  }
  if (err instanceof Error) {
    return err.message
  }
  const isGerman =
    typeof document !== 'undefined' &&
    (document.documentElement.lang || '').toLowerCase().startsWith('de')
  return isGerman ? 'Ein unerwarteter Fehler ist aufgetreten.' : 'An unexpected error occurred.'
}
