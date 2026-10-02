import React from 'react'
import { useTranslation } from 'react-i18next'
import { BellOff, Camera, ChevronLeft, Phone, Search, UserPlus, UsersRound } from 'lucide-react'
import { Blattknopf, Kurzinfo } from '@/Singra/UI'

interface ChatHeaderProps {
  /** Während Auswahl oder Suche treten die schwebenden Knöpfe zurück. */
  verborgen: boolean
  titel: string
  gruppenBild?: string | null
  stumm: boolean
  blockiert: boolean
  onZurueck: () => void
  /** Nur in Gruppen. */
  gruppenanruf?: { erlaubt: boolean; onStarten: () => void }
  /** Nur bei Freunden. */
  onSprachanruf?: () => void
  /** Nur bei Kontakten, die noch keine Freunde sind. */
  onFreundschaftsanfrage?: () => void
  /** Öffnet das Profil des Gegenübers; nur im Direktchat. */
  onProfil?: () => void
  /** Das Funken-Abzeichen neben dem Namen; nur bei Freunden. */
  funke?: React.ReactNode
  /** Kamera für einen Augenblick; nur bei Freunden. */
  onAugenblick?: () => void
  onSuche: () => void
  /** Der Inhalt des Chatmenüs; `schliessen` klappt das Blatt zu. */
  menue: (schliessen: () => void) => React.ReactNode
}

// Schlichte <button> statt der Button-Komponente: deren size="icon" setzt
// h-8 w-8 fest, und weil die Klassen nur aneinandergehängt werden, gewinnt im
// CSS die feste Größe gegen jede mitgegebene. Am Telefon braucht es 44 px.
const rundknopf =
  'flex items-center justify-center rounded-md h-11 w-11 sm:h-8 sm:w-8 bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 shadow-sm'

/** Die schwebende Kopfzeile des offenen Chats. */
export function ChatHeader({
  verborgen,
  titel,
  gruppenBild,
  stumm,
  blockiert,
  onZurueck,
  gruppenanruf,
  onSprachanruf,
  onFreundschaftsanfrage,
  onProfil,
  funke,
  onAugenblick,
  onSuche,
  menue,
}: ChatHeaderProps) {
  const { t } = useTranslation()

  return (
    <div
      className={`absolute top-2.5 left-3 right-3 z-30 flex items-center justify-between gap-2 pointer-events-none ${
        verborgen ? 'hidden' : ''
      }`}
    >
      <div className="flex items-center gap-2 min-w-0 pointer-events-auto">
        <Kurzinfo text={t('messenger.backToContacts')} seite="anfang" aussen="shrink-0 md:hidden">
          <button
            type="button"
            onClick={onZurueck}
            className="w-11 h-11 flex items-center justify-center rounded-full bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 text-on-surface-variant shadow-sm transition-colors"
            aria-label={t('messenger.backToContacts')}
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
        </Kurzinfo>

        <div className="flex items-center gap-2 min-w-0 px-3 py-1.5 rounded-full bg-surface-container-high/85 backdrop-blur-md border border-outline-variant/30 shadow-sm">
          {gruppenBild && (
            <img src={gruppenBild} alt="" className="-ml-1.5 h-5 w-5 shrink-0 rounded-full object-cover" />
          )}
          {/* Nur der Name ist der Knopf: das Funken-Abzeichen daneben ist selbst
              einer, und Knopf in Knopf gibt es nicht. */}
          {onProfil ? (
            <Kurzinfo text={t('social.profile.open', { name: titel })} seite="anfang" aussen="min-w-0">
              <button
                type="button"
                onClick={onProfil}
                className="text-xs font-bold text-on-surface truncate max-w-[130px] sm:max-w-xs hover:underline"
                aria-description={t('social.profile.open', { name: titel })}
              >
                {titel}
              </button>
            </Kurzinfo>
          ) : (
            <span className="text-xs font-bold text-on-surface truncate max-w-[130px] sm:max-w-xs">{titel}</span>
          )}
          {funke}
          {stumm && (
            <Kurzinfo text={t('messenger.chatMuted')}>
              <span role="img" aria-label={t('messenger.chatMuted')} className="inline-flex items-center text-status-warning">
                <BellOff className="w-3.5 h-3.5" />
              </span>
            </Kurzinfo>
          )}
          {blockiert && (
            <span className="px-1.5 py-0.2 rounded-md bg-status-destructive/15 text-status-destructive text-label-sm font-semibold">
              Blockiert
            </span>
          )}
        </div>
      </div>

      {/* Was oft gebraucht wird, steht hier. Alles Übrige liegt im Menü:
          acht Knöpfe passten bei 375 px nicht nebeneinander, die Gruppe lief
          41 px über den rechten Rand hinaus und drängte den Namen auf null
          Abstand. */}
      <div className="flex items-center gap-1.5 shrink-0 pointer-events-auto">
        {gruppenanruf && (
          <Kurzinfo
            text={gruppenanruf.erlaubt ? t('messenger.startGroupCall') : t('messenger.noStartCallRight')}
            seite="ende"
          >
            <button
              type="button"
              onClick={gruppenanruf.onStarten}
              disabled={!gruppenanruf.erlaubt}
              className={`${rundknopf} text-primary disabled:opacity-60`}
              aria-label={t('messenger.startGroupCall')}
              aria-description={gruppenanruf.erlaubt ? undefined : t('messenger.noStartCallRight')}
            >
              <UsersRound className="w-4 h-4" />
            </button>
          </Kurzinfo>
        )}

        {onSprachanruf && (
          <Kurzinfo text={t('messenger.startVoiceCall')} seite="ende">
            <button
              type="button"
              onClick={onSprachanruf}
              className={`${rundknopf} text-primary`}
              aria-label={t('messenger.startVoiceCall')}
            >
              <Phone className="w-4 h-4" />
            </button>
          </Kurzinfo>
        )}

        {onAugenblick && (
          <Kurzinfo text={t('messenger.moment.take')} seite="ende">
            <button
              type="button"
              onClick={onAugenblick}
              className={`${rundknopf} text-status-warning`}
              aria-label={t('messenger.moment.take')}
            >
              <Camera className="w-4 h-4" />
            </button>
          </Kurzinfo>
        )}

        {onFreundschaftsanfrage && (
          <Kurzinfo text={t('messenger.sendFriendRequest')} seite="ende">
            <button
              type="button"
              onClick={onFreundschaftsanfrage}
              className={`${rundknopf} text-primary`}
              aria-label={t('messenger.sendFriendRequest')}
            >
              <UserPlus className="w-4 h-4" />
            </button>
          </Kurzinfo>
        )}

        <Kurzinfo text={t('messenger.searchInChat')} seite="ende">
          <button
            type="button"
            onClick={onSuche}
            className={`${rundknopf} text-on-surface-variant hover:text-primary`}
            aria-label={t('messenger.searchInChat')}
          >
            <Search className="w-4 h-4" />
          </button>
        </Kurzinfo>

        <Blattknopf
          variante="schwebend"
          label={t('messenger.moreChatSettings')}
          titel={titel || t('messenger.chat')}
          ueberschrift={titel || undefined}
        >
          {menue}
        </Blattknopf>
      </div>
    </div>
  )
}
