/**
 * Bausteine, aus denen jede Kategorie der Einstellungen besteht: eine Karte
 * je Abschnitt, darin Zeilen mit Name links und Bedienelement rechts. Jede
 * Zeile hat einen Namen, höchstens einen kurzen Hinweis darunter.
 */
import { useId, type ReactNode } from 'react'

import { Dropdown, Switch } from '@/Singra/UI'

export function Abschnitt({ titel, children }: { titel: string; children: ReactNode }) {
  return (
    <section className="msm-card flex flex-col gap-4 p-5">
      <h2 className="font-headline text-title-md text-on-surface">{titel}</h2>
      {children}
    </section>
  )
}

function Zeile({ name, nameId, hinweis, children }: { name: string; nameId?: string; hinweis?: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <div className="min-w-0 flex-1 basis-48">
        <p id={nameId} className="text-body-sm text-on-surface">{name}</p>
        {hinweis && <p className="mt-0.5 break-words text-label-sm text-on-surface-variant">{hinweis}</p>}
      </div>
      {children}
    </div>
  )
}

export function Schalterzeile({ name, hinweis, an, aendern }: { name: string; hinweis?: string; an: boolean; aendern: (an: boolean) => void }) {
  const id = useId()
  return (
    <Zeile name={name} nameId={id} hinweis={hinweis}>
      <Switch aria-labelledby={id} checked={an} onCheckedChange={aendern} />
    </Zeile>
  )
}

export function Auswahlzeile<T extends string>({
  name,
  hinweis,
  wert,
  optionen,
  aendern,
}: {
  name: string
  hinweis?: string
  wert: T
  optionen: { value: T; label: string; icon?: ReactNode }[]
  aendern: (wert: T) => void
}) {
  return (
    <Zeile name={name} hinweis={hinweis}>
      <Dropdown aria-label={name} value={wert} onChange={(v) => aendern(v as T)} options={optionen} className="w-56 max-w-full" />
    </Zeile>
  )
}

/** Eine Zeile mit eigenem Bedienelement (Knopf, Ordner …). */
export function Aktionszeile({ name, hinweis, children }: { name: string; hinweis?: string; children: ReactNode }) {
  return (
    <Zeile name={name} hinweis={hinweis}>
      <div className="flex shrink-0 flex-wrap gap-2">{children}</div>
    </Zeile>
  )
}
