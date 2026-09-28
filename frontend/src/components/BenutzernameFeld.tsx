import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '@/api/client'
import { Button, Input } from '@/Singra/UI'
import { benutzernameFehler } from '@/lib/benutzername'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'
import type { User } from '@/types'

/**
 * Speichert den Benutzernamen (`PATCH /auth/me/username`) für Wahlseite und
 * Profil. Die Antwort ist das ganze Konto; sie ersetzt das im Store, damit
 * `username_gewaehlt` sofort gilt. Die Sitzung bleibt: das Backend findet das
 * Konto über die Kennung, nicht über den Namen.
 */
export function useBenutzernameSpeichern() {
  const { t } = useTranslation()
  const setUser = useAuthStore((s) => s.setUser)
  const [speichert, setSpeichert] = useState(false)

  const speichern = async (name: string): Promise<boolean> => {
    setSpeichert(true)
    try {
      const konto = await api<User>('/auth/me/username', {
        method: 'PATCH',
        body: JSON.stringify({ username: name.trim() }),
      })
      setUser(konto)
      toast.success(t('benutzername.gespeichert'))
      return true
    } catch (err: any) {
      toast.error(err?.message || t('common.error'))
      return false
    } finally {
      setSpeichert(false)
    }
  }

  return { speichert, speichern }
}

interface Props {
  /** Womit das Feld startet. */
  start: string
}

/**
 * Die einmalige Namenswahl (`BenutzernameWaehlen`). Ändern im Profil geht
 * über `BenutzernameInline`, nicht hierüber.
 */
export function BenutzernameFeld({ start }: Props) {
  const { t } = useTranslation()
  const { speichert, speichern } = useBenutzernameSpeichern()
  const [name, setName] = useState(start)
  const [beruehrt, setBeruehrt] = useState(false)

  const fehler = benutzernameFehler(name)
  const fehlertext = beruehrt && fehler ? t(fehler.schluessel, fehler) : undefined

  const abschicken = (e: FormEvent) => {
    e.preventDefault()
    setBeruehrt(true)
    if (!fehler) void speichern(name)
  }

  return (
    <form onSubmit={abschicken} className="flex max-w-md items-start gap-3">
      <div className="flex-1">
        <Input
          id="benutzername"
          label={t('benutzername.label')}
          value={name}
          onChange={(e) => {
            setName(e.target.value)
            setBeruehrt(true)
          }}
          maxLength={32}
          autoComplete="username"
          autoFocus
          error={fehlertext}
        />
        {!fehlertext && (
          <span className="mt-1.5 block text-xs text-on-surface-variant">{t('benutzername.regel')}</span>
        )}
      </div>
      <Button type="submit" className="mt-7" disabled={speichert || Boolean(fehler)}>
        {t('benutzername.weiter')}
      </Button>
    </form>
  )
}
