import { useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'

import { api } from '@/api/client'
import { Button, Input } from '@/Singra/UI'
import { benutzernameFehler } from '@/lib/benutzername'
import { useAuthStore } from '@/stores/authStore'
import { toast } from '@/stores/toastStore'
import type { User } from '@/types'

interface Props {
  /** Womit das Feld startet. */
  start: string
  /** Beschriftung des Knopfs; Standard „Speichern“. */
  knopf?: string
  /** Auch ohne Änderung speichern: die Wahlseite bestätigt den Vorschlag. */
  unveraendertErlaubt?: boolean
  autoFocus?: boolean
}

/**
 * Benutzername wählen oder ändern. Gemeinsam für die Wahlseite, das Profil
 * und die Konto-Einstellungen der App.
 *
 * Die Antwort ist das ganze Konto (`PATCH /auth/me/username`); sie ersetzt
 * das im Store, damit `username_gewaehlt` sofort gilt und die Wahlseite
 * verschwindet. Die Sitzung bleibt: das Backend findet das Konto über die
 * Kennung, nicht über den Namen.
 */
export function BenutzernameFeld({ start, knopf, unveraendertErlaubt = false, autoFocus }: Props) {
  const { t } = useTranslation()
  const user = useAuthStore((s) => s.user)
  const setUser = useAuthStore((s) => s.setUser)
  const [name, setName] = useState(start)
  const [beruehrt, setBeruehrt] = useState(false)
  const [speichert, setSpeichert] = useState(false)

  const fehler = benutzernameFehler(name)
  const fehlertext = beruehrt && fehler ? t(fehler.schluessel, fehler) : undefined
  const unveraendert = name.trim() === (user?.username ?? '')

  const speichern = async (e: FormEvent) => {
    e.preventDefault()
    setBeruehrt(true)
    if (fehler) return
    setSpeichert(true)
    try {
      const konto = await api<User>('/auth/me/username', {
        method: 'PATCH',
        body: JSON.stringify({ username: name.trim() }),
      })
      setUser(konto)
      toast.success(t('benutzername.gespeichert'))
    } catch (err: any) {
      toast.error(err?.message || t('common.error'))
    } finally {
      setSpeichert(false)
    }
  }

  return (
    <form onSubmit={speichern} className="flex max-w-md items-start gap-3">
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
          autoFocus={autoFocus}
          error={fehlertext}
          aria-describedby="benutzername-regel"
        />
        {!fehlertext && (
          <span id="benutzername-regel" className="mt-1.5 block text-xs text-on-surface-variant">
            {t('benutzername.regel')}
          </span>
        )}
      </div>
      <Button
        type="submit"
        className="mt-7"
        disabled={speichert || Boolean(fehler) || (!unveraendertErlaubt && unveraendert)}
      >
        {knopf ?? t('common.save')}
      </Button>
    </form>
  )
}
