import { useTranslation } from 'react-i18next'
import {
  Ban,
  Bell,
  BellOff,
  Image as ImageIcon,
  ImagePlus,
  LogOut,
  Share2,
  Shield,
  ShieldCheck,
  Timer,
  Trash2,
  Video,
} from 'lucide-react'
import { Blatteintrag } from '@/Singra/UI'

/** Was das Menü über den offenen Direktchat wissen muss. */
export interface MenueKontakt {
  istFreund: boolean
  blockiert: boolean
}

/** Was das Menü über die offene Gruppe wissen muss. */
export interface MenueGruppe {
  hatEinladung: boolean
  istEigentuemer: boolean
  istAdmin: boolean
  logoLaedt: boolean
}

interface ChatActionsMenuProps {
  /** Schliesst das Blatt; jede Wahl ruft es vor ihrer Aktion. */
  schliessen: () => void
  /** `null`, solange der Chat kein Postfach hat: dann gibt es nichts stummzuschalten. */
  stumm: boolean | null
  kontakt: MenueKontakt | null
  gruppe: MenueGruppe | null
  /** Die heutige Frist als Text, etwa „Aus" oder „1 Tag". */
  verfallStufe: string
  verfallErlaubt: boolean
  onStumm: () => void
  onSicherheitsnummer: () => void
  onVerfall: () => void
  onVideoanruf: () => void
  onHintergrund: () => void
  onEinladung: () => void
  onLogo: () => void
  onRollen: () => void
  onLoeschen: () => void
  onVerlassen: () => void
  onBlockieren: () => void
}

/**
 * Die Einträge im Menü des offenen Chats.
 *
 * Wer welchen Eintrag sieht, ist hier nur Anzeige: Löschen, Rollen und Logo
 * prüft der Server ohnehin. Das Menü blendet aus, was ohne Recht sicher
 * scheitern würde.
 */
export function ChatActionsMenu({
  schliessen,
  stumm,
  kontakt,
  gruppe,
  verfallStufe,
  verfallErlaubt,
  ...aktion
}: ChatActionsMenuProps) {
  const { t } = useTranslation()
  const tu = (f: () => void) => () => {
    schliessen()
    f()
  }

  return (
    <>
      {stumm !== null && (
        <Blatteintrag
          icon={stumm ? <Bell className="w-4 h-4" /> : <BellOff className="w-4 h-4" />}
          label={stumm ? t('messenger.unmute') : t('messenger.mute')}
          onClick={tu(aktion.onStumm)}
        />
      )}
      {kontakt && (
        <Blatteintrag
          icon={<ShieldCheck className="w-4 h-4" />}
          label={t('messenger.verifySafetyNumber')}
          onClick={tu(aktion.onSicherheitsnummer)}
        />
      )}
      {/* Die aktuelle Frist steht auch ohne das Recht da — wissen, wann die
          eigenen Nachrichten verschwinden, darf jedes Mitglied. */}
      <Blatteintrag
        icon={<Timer className="w-4 h-4" />}
        label={t('messenger.disappearingMessages')}
        hinweis={verfallErlaubt ? verfallStufe : `${verfallStufe} · ${t('messenger.retentionNoRight')}`}
        disabled={!verfallErlaubt}
        onClick={tu(aktion.onVerfall)}
      />
      {kontakt?.istFreund && (
        <Blatteintrag
          icon={<Video className="w-4 h-4" />}
          label={t('messenger.videoCall')}
          onClick={tu(aktion.onVideoanruf)}
        />
      )}
      <Blatteintrag
        icon={<ImageIcon className="w-4 h-4" />}
        label={t('social.wallpaper.title')}
        onClick={tu(aktion.onHintergrund)}
      />

      {gruppe && (
        <>
          {gruppe.hatEinladung && (
            <Blatteintrag
              icon={<Share2 className="w-4 h-4" />}
              label={t('messenger.copyInvite')}
              onClick={tu(aktion.onEinladung)}
            />
          )}
          {(gruppe.istEigentuemer || gruppe.istAdmin) && (
            <>
              <Blatteintrag
                icon={<ImagePlus className="w-4 h-4" />}
                label={t('messenger.changeGroupLogo')}
                disabled={gruppe.logoLaedt}
                onClick={tu(aktion.onLogo)}
              />
              <Blatteintrag
                icon={<Shield className="w-4 h-4" />}
                label={t('messenger.manageGroupRoles')}
                onClick={tu(aktion.onRollen)}
              />
            </>
          )}
          {gruppe.istEigentuemer ? (
            <Blatteintrag
              icon={<Trash2 className="w-4 h-4" />}
              label={t('messenger.deleteGroup')}
              gefahr
              onClick={tu(aktion.onLoeschen)}
            />
          ) : (
            <Blatteintrag
              icon={<LogOut className="w-4 h-4" />}
              label={t('messenger.leaveGroup')}
              gefahr
              onClick={tu(aktion.onVerlassen)}
            />
          )}
        </>
      )}

      {kontakt && (
        <Blatteintrag
          icon={<Ban className="w-4 h-4" />}
          label={kontakt.blockiert ? t('messenger.unblockContact') : t('messenger.blockContact')}
          gefahr={!kontakt.blockiert}
          onClick={tu(aktion.onBlockieren)}
        />
      )}
    </>
  )
}
