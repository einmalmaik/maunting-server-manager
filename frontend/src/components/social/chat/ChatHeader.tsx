import React from 'react'
import { useTranslation } from 'react-i18next'
import { BellOff, ChevronLeft, Phone, Search, UserPlus, UsersRound } from 'lucide-react'
import { Blattknopf } from '@/Singra/UI'

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
        <button
          type="button"
          onClick={onZurueck}
          className="md:hidden w-11 h-11 flex items-center justify-center rounded-full bg-surface-container-high/85 hover:bg-surface-container-high backdrop-blur-md border border-outline-variant/30 text-on-surface-variant shadow-sm transition-colors"
          aria-label={t('messenger.backToContacts')}
          title={t('messenger.backToContacts')}
        >
          <ChevronLeft className="w-4 h-4" />
        </button>

        <div className="flex items-center gap-2 min-w-0 px-3 py-1.5 rounded-full bg-surface-container-high/85 backdrop-blur-md border border-outline-variant/30 shadow-sm">
          {gruppenBild && (
            <img src={gruppenBild} alt="" className="-ml-1.5 h-5 w-5 shrink-0 rounded-full object-cover" />
          )}
          <span className="text-xs font-bold text-on-surface truncate max-w-[130px] sm:max-w-xs">{titel}</span>
          {stumm && (
            <span title="Stummgeschaltet" className="inline-flex items-center text-status-warning">
              <BellOff className="w-3.5 h-3.5" />
            </span>
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
          <button
            type="button"
            onClick={gruppenanruf.onStarten}
            disabled={!gruppenanruf.erlaubt}
            className={`${rundknopf} text-primary disabled:opacity-60`}
            title={gruppenanruf.erlaubt ? t('messenger.startGroupCall') : t('messenger.noStartCallRight')}
            aria-label={t('messenger.startGroupCall')}
          >
            <UsersRound className="w-4 h-4" />
          </button>
        )}

        {onSprachanruf && (
          <button
            type="button"
            onClick={onSprachanruf}
            className={`${rundknopf} text-primary`}
            title={t('messenger.startVoiceCall')}
            aria-label={t('messenger.startVoiceCall')}
          >
            <Phone className="w-4 h-4" />
          </button>
        )}

        {onFreundschaftsanfrage && (
          <button
            type="button"
            onClick={onFreundschaftsanfrage}
            className={`${rundknopf} text-primary`}
            title={t('messenger.sendFriendRequest')}
            aria-label={t('messenger.sendFriendRequest')}
          >
            <UserPlus className="w-4 h-4" />
          </button>
        )}

        <button
          type="button"
          onClick={onSuche}
          className={`${rundknopf} text-on-surface-variant hover:text-primary`}
          title={t('messenger.searchInChat')}
          aria-label={t('messenger.searchInChat')}
        >
          <Search className="w-4 h-4" />
        </button>

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
