/**
 * Das Funken-Abzeichen — in der Chatliste, im Chatkopf und in der Freundesliste.
 *
 * `FunkenBadge` zeigt nur, was ihm gegeben wird, und lässt sich so ohne Store
 * prüfen. `FunkenAbzeichen` holt sich den Zustand selbst, wie die anderen
 * Bausteine der Chatliste ihre Store-Werte: die Seite reicht nichts durch.
 */

import React, { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Flame, Hourglass, RotateCcw, Sparkles } from 'lucide-react'

import { Badge, Blattmenue, Blatteintrag } from '@/Singra/UI'

import { angemeldetesKonto } from '@/lib/angemeldetesKonto'
import { STUNDE_MS, TAG_MS, berechneFunkenZustand, type FunkenZustand } from '@/services/funkenService'
import { useFunkenStore } from '@/stores/funkenStore'

/** Die Uhr der Abzeichen: eine Minute genügt, angezeigt werden Stunden. */
export function useJetzt(takt = 60_000): number {
  const [jetzt, setJetzt] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setJetzt(Date.now()), takt)
    return () => clearInterval(id)
  }, [takt])
  return jetzt
}

/** Angefangene Stunden bis zu einem Zeitpunkt; nie weniger als eine. */
export function stundenBis(ziel: number | null, jetzt: number): number {
  if (ziel === null) return 0
  return Math.max(1, Math.ceil((ziel - jetzt) / STUNDE_MS))
}

/** Was über einen Funken zu sagen ist: der Stand und was jetzt gilt. */
export function funkenZeilen(
  z: FunkenZustand,
  name: string,
  jetzt: number,
  t: (k: string, o?: Record<string, unknown>) => string,
): { kopf: string; lage: string } {
  const hours = stundenBis(z.deadlineAt, jetzt)
  const kopf = t('messenger.streak.active', { count: z.streakCount, name })
  switch (z.status) {
    case 'active':
      if (z.erledigt) return { kopf, lage: t('messenger.streak.doneToday', { hours: stundenBis(z.cycleStartAt, jetzt) }) }
      return {
        kopf,
        lage: z.meSent
          ? t('messenger.streak.waitingForPartner', { name, hours })
          : t('messenger.streak.waitingForYou', { hours }),
      }
    case 'grace':
      return { kopf, lage: t('messenger.streak.buffer', { hours }) }
    case 'extended':
      return { kopf, lage: t('messenger.streak.extended', { hours }) }
    case 'pending':
      return { kopf: '', lage: t('messenger.streak.pending', { name }) }
    case 'expired': {
      const verloren = t('messenger.streak.expired', { count: z.lostCount })
      if (z.canRestore) return { kopf: verloren, lage: t('messenger.streak.restoreRule') }
      if (z.restoreAvailableAt !== null) {
        const tage = Math.max(1, Math.ceil((z.restoreAvailableAt - jetzt) / TAG_MS))
        return { kopf: verloren, lage: t('messenger.streak.restoreCooldown', { count: tage }) }
      }
      return { kopf: verloren, lage: '' }
    }
    default:
      return { kopf: '', lage: '' }
  }
}

/** Beides in einem Satz — für Screenreader. */
export function funkenText(
  z: FunkenZustand,
  name: string,
  jetzt: number,
  t: (k: string, o?: Record<string, unknown>) => string,
): string {
  const { kopf, lage } = funkenZeilen(z, name, jetzt, t)
  return [kopf, lage].filter(Boolean).join(' · ')
}

const VARIANTE = {
  active: 'warning',
  grace: 'warning',
  extended: 'destructive',
  expired: 'default',
} as const

interface FunkenBadgeProps {
  zustand: FunkenZustand
  /** Der Name der Gegenseite. */
  name: string
  jetzt: number
  /**
   * Antippen öffnet ein Blatt mit dem, was gerade gilt. Aus in der Chatliste:
   * dort ist die ganze Zeile schon ein Knopf, und ein Knopf im Knopf öffnet
   * beides.
   */
  interaktiv?: boolean
  /** Nur wo eine Wiederherstellung angeboten werden soll. */
  onWiederherstellen?: () => void
}

/**
 * Das Abzeichen selbst: die `Badge` der Oberfläche, kein eigener Nachbau und
 * kein Browser-Tooltip. Was ein Tooltip sagen würde, steht im Blatt — am
 * Telefon gibt es kein Überfahren mit der Maus.
 */
export function FunkenBadge({ zustand: z, name, jetzt, interaktiv = false, onWiederherstellen }: FunkenBadgeProps) {
  const { t } = useTranslation()
  const [offen, setOffen] = useState(false)
  if (z.status === 'inactive' || z.status === 'pending') return null

  const { kopf, lage } = funkenZeilen(z, name, jetzt, t)
  const zahl = z.status === 'expired' ? z.lostCount : z.streakCount
  const zeichen =
    z.status === 'grace' ? (
      <Hourglass className="w-3 h-3 animate-pulse" aria-hidden="true" />
    ) : z.status === 'extended' ? (
      <Flame className="w-3 h-3" aria-hidden="true" />
    ) : (
      <Sparkles className="w-3 h-3" aria-hidden="true" />
    )
  const oeffnen = (e: React.SyntheticEvent) => {
    e.stopPropagation()
    setOffen(true)
  }

  return (
    <>
      <Badge
        variant={VARIANTE[z.status]}
        className={`gap-1 shrink-0 font-bold tabular-nums ${interaktiv ? 'cursor-pointer' : ''} ${
          z.status === 'expired' ? 'opacity-70' : ''
        }`}
        data-funke={z.status}
        aria-label={funkenText(z, name, jetzt, t)}
        {...(interaktiv
          ? {
              role: 'button',
              tabIndex: 0,
              'aria-haspopup': 'dialog' as const,
              onClick: oeffnen,
              onKeyDown: (e: React.KeyboardEvent) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  oeffnen(e)
                }
              },
            }
          : {})}
      >
        {zeichen}
        <span>{zahl}</span>
        {z.status === 'extended' && <span className="font-medium">{t('messenger.streak.extendedShort')}</span>}
        {z.status === 'expired' && z.canRestore && onWiederherstellen && (
          <RotateCcw className="w-3 h-3" aria-hidden="true" />
        )}
      </Badge>

      {interaktiv && (
        <Blattmenue offen={offen} onSchliessen={() => setOffen(false)} titel={t('messenger.streak.sheetTitle', { name })}>
          <div className="px-4 pt-2 pb-3 space-y-1">
            <div className="flex items-center gap-2 text-sm font-semibold text-on-surface">
              <span className={z.status === 'extended' ? 'text-status-destructive' : 'text-status-warning'}>
                {zeichen}
              </span>
              <span>{kopf}</span>
            </div>
            {lage && <p className="text-label-sm text-on-surface-variant">{lage}</p>}
          </div>
          {z.canRestore && onWiederherstellen && (
            <div className="pb-2 border-t border-outline-variant/20">
              <Blatteintrag
                icon={<RotateCcw className="w-4 h-4 text-status-warning" />}
                label={t('messenger.streak.restoreTitle')}
                hinweis={t('messenger.streak.restoreRuleShort')}
                onClick={() => {
                  setOffen(false)
                  onWiederherstellen()
                }}
              />
            </div>
          )}
        </Blattmenue>
      )}
    </>
  )
}

/**
 * Der Zustand des Funkens mit einem Kontakt — oder `null`, wenn es keinen
 * geben kann. Samt der Uhrzeit, zu der er gilt.
 *
 * Die Uhr wird bei jeder Änderung der Akte neu gelesen, nicht nur im Takt:
 * sonst stünde ein Augenblick von eben neben einer Uhr von vor einer Minute,
 * und das Abzeichen rechnete mit einer Zeit, in der es ihn noch nicht gab.
 */
export function useFunkenZustand(
  partnerId: number | null | undefined,
): { zustand: FunkenZustand; jetzt: number } | null {
  const takt = useJetzt()
  const akte = useFunkenStore((s) => (partnerId ? s.akten[partnerId] : undefined))
  const seit = useFunkenStore((s) => (partnerId ? s.freunde[partnerId] : undefined))
  const lade = useFunkenStore((s) => s.lade)
  useEffect(() => {
    void lade()
  }, [lade])
  return useMemo(() => {
    const ich = angemeldetesKonto()
    // Nur unter Freunden. Wer hier nicht steht, hat keinen Funken — auch nicht
    // mit einer alten Akte von früher.
    if (!partnerId || !seit || !ich) return null
    const jetzt = Math.max(takt, Date.now())
    return { zustand: berechneFunkenZustand(akte, { ich, partner: partnerId, seit, jetzt }), jetzt }
  }, [akte, seit, partnerId, takt])
}

/** Das Abzeichen für einen Kontakt, mit eigenem Zustand. */
export function FunkenAbzeichen({
  partnerId,
  name,
  interaktiv = false,
  onWiederherstellen,
}: {
  partnerId: number
  name: string
  interaktiv?: boolean
  onWiederherstellen?: () => void
}) {
  const stand = useFunkenZustand(partnerId)
  if (!stand) return null
  return (
    <FunkenBadge
      zustand={stand.zustand}
      name={name}
      jetzt={stand.jetzt}
      interaktiv={interaktiv}
      onWiederherstellen={onWiederherstellen}
    />
  )
}
