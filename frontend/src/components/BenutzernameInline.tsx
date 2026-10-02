import { useState, type FormEvent } from 'react'
import { Pencil } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { useBenutzernameSpeichern } from '@/components/BenutzernameFeld'
import { Button, Input, Kurzinfo } from '@/Singra/UI'
import { benutzernameFehler } from '@/lib/benutzername'
import { useAuthStore } from '@/stores/authStore'

interface Props {
  /** Klassen für den Namen in der Anzeige; Profil und App setzen ihn verschieden groß. */
  className?: string
}

/**
 * Der Benutzername neben dem Profilbild, mit Stift zum Ändern. Die einzige
 * Stelle zum Ändern, im Panel (Profil → Konto) wie in der App (Einstellungen
 * → Konto). Der Name liegt am Konto auf dem Server, eine Änderung gilt also
 * überall.
 */
export function BenutzernameInline({ className = '' }: Props) {
  const { t } = useTranslation()
  const username = useAuthStore((s) => s.user?.username ?? '')
  const { speichert, speichern } = useBenutzernameSpeichern()
  const [offen, setOffen] = useState(false)
  const [name, setName] = useState(username)

  const fehler = benutzernameFehler(name)
  const unveraendert = name.trim() === username

  const oeffnen = () => {
    setName(username)
    setOffen(true)
  }

  const abschicken = async (e: FormEvent) => {
    e.preventDefault()
    if (fehler || unveraendert) return
    if (await speichern(name)) setOffen(false)
  }

  if (!offen) {
    return (
      <div className="flex min-w-0 items-center gap-1.5">
        <span className={`truncate ${className}`}>{username}</span>
        <Kurzinfo text={t('benutzername.aendern')} aussen="shrink-0">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={oeffnen}
            aria-label={t('benutzername.aendern')}
          >
            <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
        </Kurzinfo>
      </div>
    )
  }

  return (
    <form onSubmit={abschicken} className="flex max-w-md flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <Input
            id="benutzername"
            aria-label={t('benutzername.label')}
            aria-invalid={Boolean(fehler)}
            aria-describedby="benutzername-hinweis"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setOffen(false)
            }}
            maxLength={32}
            autoComplete="username"
            autoFocus
            className={`h-8 ${fehler ? 'border-status-destructive focus:ring-status-destructive' : ''}`}
          />
        </div>
        <Button type="submit" size="sm" disabled={speichert || Boolean(fehler) || unveraendert}>
          {t('common.save')}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={() => setOffen(false)}>
          {t('common.cancel')}
        </Button>
      </div>
      {/* Unter der ganzen Zeile, nicht unter dem Feld: dort bräche der Satz nach zwei Wörtern um. */}
      <p id="benutzername-hinweis" className={`text-xs ${fehler ? 'text-status-destructive' : 'text-on-surface-variant'}`}>
        {fehler ? t(fehler.schluessel, fehler) : t('benutzername.regel')}
      </p>
    </form>
  )
}
