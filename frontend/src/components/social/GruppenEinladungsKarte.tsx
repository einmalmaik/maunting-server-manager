import React, { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Users } from 'lucide-react'
import { Button } from '@/Singra/UI'
import { apiUrl } from '@/config/api'
import { getGroupInviteInfo, type ChatGroupInvitePublic } from '@/api/social'
import { lieseEinladungsKarte, type EinladungsInhalt } from '@/services/einladungsKarte'

/** Eine gefundene Einladung: der Code, und — wenn vorhanden — ihr Schlüssel. */
export interface GefundeneEinladung {
  code: string
  /**
   * Der Wert hinter `#k=`, falls der Link einen trägt.
   *
   * Er steht **im Nachrichtentext**, nicht in `location.hash`: die Karte wird
   * aus einer Chatnachricht gezeichnet, und dort steht der ganze Link. Der
   * Server bekommt ihn trotzdem nie zu sehen — die Nachricht ist verschlüsselt,
   * und beim Aufruf des Links lässt der Browser alles hinter der Raute weg.
   */
  schluessel: string | null
}

/**
 * Findet den ersten Gruppen-Einladungslink in einem Nachrichtentext.
 *
 * Bewusst auf den eigenen Ursprung beschränkt: ein Link auf ein fremdes Panel
 * gehört nicht als Vorschaukarte hierher, weil die Karte dann Daten von dort
 * nachladen müsste.
 */
export function findeEinladung(text: string, origin: string): GefundeneEinladung | null {
  if (!text) return null
  const muster = new RegExp(
    `${origin.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/chat/join/([A-Za-z0-9_-]{8,64})(#k=([0-9a-f]{64}))?`,
  )
  const treffer = muster.exec(text)
  if (!treffer) return null
  return { code: treffer[1], schluessel: treffer[3] ?? null }
}

/** Nur der Code — für Aufrufer, die den Schlüssel nicht brauchen. */
export function findeEinladungsCode(text: string, origin: string): string | null {
  return findeEinladung(text, origin)?.code ?? null
}

export interface GruppenEinladungsKarteProps {
  inviteCode: string
  /**
   * Der Schlüssel aus dem Link. Ohne ihn bleibt eine verschlüsselte Karte zu,
   * und die Vorschau fällt auf den Klartext zurück — solange es den noch gibt.
   */
  schluessel?: string | null
  /** Eigene Nachricht: dann wird die Karte etwas dezenter gezeichnet. */
  istEigene?: boolean
  onJoin: (inviteCode: string) => void | Promise<void>
}

export const GruppenEinladungsKarte: React.FC<GruppenEinladungsKarteProps> = ({
  inviteCode,
  schluessel = null,
  istEigene = false,
  onJoin,
}) => {
  const { t } = useTranslation()

  const [info, setInfo] = useState<ChatGroupInvitePublic | null>(null)
  const [karte, setKarte] = useState<EinladungsInhalt | null>(null)
  const [laedt, setLaedt] = useState(true)
  const [fehlt, setFehlt] = useState(false)
  const [tritt, setTritt] = useState(false)
  const [logoKaputt, setLogoKaputt] = useState(false)

  useEffect(() => {
    let aktiv = true
    setLaedt(true)
    setFehlt(false)
    setKarte(null)
    getGroupInviteInfo(inviteCode)
      .then(async (daten) => {
        if (!aktiv) return
        setInfo(daten)
        // Erst nach dem Abruf: vorher steht nicht fest, ob es überhaupt eine
        // verschlüsselte Karte gibt. Scheitert das Öffnen, bleibt `karte` leer
        // und die Anzeige fällt auf den Klartext zurück — den es nur gibt,
        // solange die Gruppe keine Karte hat.
        const inhalt = await lieseEinladungsKarte(daten.invite_card, schluessel, inviteCode)
        if (aktiv) setKarte(inhalt)
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
  }, [inviteCode, schluessel])

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

  /*
   * Zwei Quellen, eine Anzeige — und der Vorrang ist nicht beliebig.
   *
   * Die entschlüsselte Karte gewinnt immer. Sie kommt von einem Mitglied und
   * ist an diesen Einladungscode gebunden; der Klartext daneben kommt aus
   * Spalten, die der Server kennt und die in Stufe 6 verschwinden. Wäre es
   * umgekehrt, zeigte die Karte bis dahin weiter den Serverstand und niemandem
   * fiele auf, dass die Verschlüsselung nichts bewirkt.
   *
   * Das Logo: aus der Karte als Data-URL, im Altweg als Adresse. Eine Adresse
   * heisst, dass der Server das Bild ausliefert — und damit mitbekommt, wer
   * sich eine Einladung gerade ansieht.
   */
  const name = karte?.name ?? info.name ?? null
  const logo = karte?.logo ?? (info.avatar_url ? apiUrl(info.avatar_url) : null)

  return (
    <div className={`mt-2 overflow-hidden rounded-xl border ${rahmen}`}>
      <div className="flex items-center gap-3 p-3">
        <div className="h-11 w-11 shrink-0 overflow-hidden rounded-xl border border-white/10 bg-surface-container">
          {logo && !logoKaputt ? (
            <img
              src={logo}
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
          <div className="truncate text-sm font-semibold">
            {name ?? t('social.invite.sealed')}
          </div>
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
