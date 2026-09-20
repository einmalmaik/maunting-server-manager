import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Users } from 'lucide-react'
import { Button } from '@/Singra/UI'
import { apiUrl } from '@/config/api'
import { getGroupInviteInfo, type ChatGroupInvitePublic } from '@/api/social'

/**
 * Findet den ersten Gruppen-Einladungslink in einem Nachrichtentext.
 *
 * Bewusst auf den eigenen Ursprung beschränkt: ein Link auf ein fremdes Panel
 * gehört nicht als Vorschaukarte hierher, weil die Karte dann Daten von dort
 * nachladen müsste.
 */
export function findeEinladungsCode(text: string, origin: string): string | null {
  if (!text) return null
  const muster = new RegExp(
    `${origin.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/chat/join/([A-Za-z0-9_-]{8,64})`,
  )
  const treffer = muster.exec(text)
  return treffer ? treffer[1] : null
}

export interface GruppenEinladungsKarteProps {
  inviteCode: string
  /** Eigene Nachricht: dann wird die Karte etwas dezenter gezeichnet. */
  istEigene?: boolean
  onJoin: (inviteCode: string) => void | Promise<void>
}

export const GruppenEinladungsKarte: React.FC<GruppenEinladungsKarteProps> = ({
  inviteCode,
  istEigene = false,
  onJoin,
}) => {
  const { t } = useTranslation()

  const [info, setInfo] = useState<ChatGroupInvitePublic | null>(null)
  const [laedt, setLaedt] = useState(true)
  const [fehlt, setFehlt] = useState(false)
  const [tritt, setTritt] = useState(false)
  const [logoKaputt, setLogoKaputt] = useState(false)

  useEffect(() => {
    let aktiv = true
    setLaedt(true)
    setFehlt(false)
    getGroupInviteInfo(inviteCode)
      .then((daten) => {
        if (aktiv) setInfo(daten)
      })
      .catch(() => {
        if (aktiv) setFehlt(true)
      })
      .finally(() => {
        if (aktiv) setLaedt(false)
      })
    return () => {
      aktiv = false
    }
  }, [inviteCode])

  const rahmen = istEigene
    ? 'border-white/20 bg-black/20 text-white'
    : 'border-outline-variant/30 bg-surface-container-highest text-on-surface'

  if (laedt) {
    return (
      <div className={`mt-2 flex items-center gap-2 rounded-xl border p-3 text-xs ${rahmen}`}>
        <Loader2 className="h-4 w-4 animate-spin opacity-70" />
        <span className="opacity-75">{t('social.invite.loading')}</span>
      </div>
    )
  }

  if (fehlt || !info) {
    return (
      <div className={`mt-2 rounded-xl border p-3 text-xs ${rahmen}`}>
        <span className="opacity-75">{t('social.invite.expired')}</span>
      </div>
    )
  }

  return (
    <div className={`mt-2 overflow-hidden rounded-xl border ${rahmen}`}>
      <div className="flex items-center gap-3 p-3">
        <div className="h-11 w-11 shrink-0 overflow-hidden rounded-xl border border-white/10 bg-surface-container">
          {info.avatar_url && !logoKaputt ? (
            <img
              src={apiUrl(info.avatar_url)}
              alt=""
              className="h-full w-full object-cover"
              onError={() => setLogoKaputt(true)}
            />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-primary">
              <Users className="h-5 w-5" />
            </div>
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="text-label-sm uppercase tracking-[0.14em] opacity-60">
            {t('social.invite.heading')}
          </div>
          <div className="truncate text-sm font-semibold">{info.name}</div>
          <div className="mt-0.5 flex items-center gap-2 text-label-sm opacity-75">
            <span>
              {t('social.invite.memberCount', { count: info.member_count })}
            </span>
            {info.live_call && (
              <span className="inline-flex items-center gap-1 rounded-full bg-status-success/20 px-1.5 py-0.5 font-medium text-status-success">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-status-success" />
                {t('calls.live')}
                {info.live_participants > 0 && ` · ${info.live_participants}`}
              </span>
            )}
          </div>
        </div>

        <Button
          size="sm"
          disabled={tritt}
          onClick={async () => {
            setTritt(true)
            try {
              await onJoin(inviteCode)
            } finally {
              setTritt(false)
            }
          }}
        >
          {tritt ? <Loader2 className="h-4 w-4 animate-spin" /> : t('social.invite.join')}
        </Button>
      </div>
    </div>
  )
}
