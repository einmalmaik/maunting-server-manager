import React, { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { PhoneOff, PhoneForwarded, Smartphone, Monitor, Globe, Radio, Users } from 'lucide-react'
import { useCallStore, setzeAnrufIdentitaet } from '@/stores/useCallStore'
import { deviceLabelKey, getDeviceId } from '@/lib/deviceIdentity'
import { useAuthStore } from '@/stores/authStore'
import { eigenesGeraet, geraetVeroeffentlichen } from '@/services/e2eeGeraet'

interface CrossDeviceCallBannerProps {
  className?: string
}

export const CrossDeviceCallBanner: React.FC<CrossDeviceCallBannerProps> = ({ className = '' }) => {
  const { t } = useTranslation()

  const {
    crossDeviceCall,
    activeGroupCalls,
    state,
    checkActiveCall,
    transferCallToThisDevice,
    terminateCrossDeviceCall,
    handleCrossDeviceEvent,
    joinGroupCall,
  } = useCallStore()
  const user = useAuthStore((s) => s.user)

  // Globale E2EE-Anrufidentität beim Start und Benutzerwechsel auflösen,
  // damit Anrufe auch außerhalb des Messengers angenommen und entschlüsselt werden können.
  useEffect(() => {
    if (!user?.id) return
    let active = true
    // Der Raumschlüssel wird gegen den Geräteschlüssel versiegelt, also muss
    // dieses Gerät angemeldet sein, bevor jemand ihm etwas zustellen kann.
    geraetVeroeffentlichen()
      .then(() => eigenesGeraet())
      .then((geraet) => {
        if (!active) return
        setzeAnrufIdentitaet({
          userId: user.id,
          publicKeyJwk: geraet.paar.publicKeyJwk,
          decryptionKeys: [geraet.paar.privateKeyJwk],
        })
      })
      .catch(() => {})
    return () => { active = false }
  }, [user?.id])

  // Beim Mounten und zyklisch (alle 4s) nachsehen, ob ein eingehender oder aktiver Anruf vorliegt
  useEffect(() => {
    void checkActiveCall()
    const timer = window.setInterval(() => {
      const currentState = useCallStore.getState().state
      if (currentState === 'idle' || currentState === 'incoming') {
        void useCallStore.getState().checkActiveCall()
      }
    }, 4000)
    return () => window.clearInterval(timer)
  }, [checkActiveCall])

  // Echtzeit-Ereignisse über den globalen SSE-Event-Bus empfangen
  useEffect(() => {
    const onSyncEvent = (e: Event) => {
      const custom = e as CustomEvent<{ type?: string; [key: string]: unknown }>
      if (custom.detail) {
        handleCrossDeviceEvent(custom.detail)
      }
    }
    window.addEventListener('msm:sync-event', onSyncEvent)
    return () => window.removeEventListener('msm:sync-event', onSyncEvent)
  }, [handleCrossDeviceEvent])

  // Nur anzeigen, wenn das eigene Gerät idle ist
  if (state !== 'idle') {
    return null
  }

  // Aktive Gruppenanrufe anzeigen (z. B. wenn kein Anruf auf anderem Gerät läuft)
  if (!crossDeviceCall && activeGroupCalls && activeGroupCalls.length > 0) {
    const groupCall = activeGroupCalls[0]
    return (
      <div
        role="region"
        aria-label={t('calls.activeGroupCall')}
        data-testid="active-group-call-banner"
        className={`relative z-40 w-full shrink-0 overflow-hidden bg-gradient-to-r from-emerald-950/90 via-emerald-900/90 to-teal-950/90 border-b border-status-success/30 text-on-surface px-4 py-2.5 shadow-lg backdrop-blur-md transition-all duration-300 ${className}`}
      >
        <div className="max-w-7xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3 text-xs sm:text-sm">
          {/* Linke Seite: Pulsierender Status & Gruppenanruf-Info */}
          <div className="flex items-center gap-3 min-w-0 w-full sm:w-auto">
            <div className="relative flex items-center justify-center w-8 h-8 rounded-full bg-status-success/20 text-status-success shrink-0 border border-status-success/40">
              <Radio className="w-4 h-4 animate-pulse text-status-success" />
              <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 bg-status-success rounded-full animate-ping opacity-75" />
            </div>

            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-semibold text-white tracking-tight">
                  {t('calls.ongoingGroupCall')}
                </span>
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-label-sm font-medium bg-status-success/20 text-status-success border border-status-success/30">
                  <Users className="w-3 h-3" />
                  {groupCall.participant_count > 0 ? t('calls.activeCount', { count: groupCall.participant_count }) : t('calls.live')}
                </span>
              </div>
              <div className="text-xs text-on-surface-variant truncate">
                {t('calls.groupPrefix')} <span className="font-medium text-on-surface">{groupCall.group_name}</span>
              </div>
            </div>
          </div>

          {/* Rechte Seite: Beitritts-Knopf */}
          <div className="flex items-center gap-2 shrink-0 w-full sm:w-auto justify-end">
            <button
              type="button"
              onClick={() => {
                void joinGroupCall(
                  {
                    id: groupCall.group_id,
                    name: groupCall.group_name,
                    avatarUrl: groupCall.avatar_url,
                    canShare: true,
                    canModerate: false,
                  },
                  groupCall.room_token,
                )
              }}
              className="flex-1 sm:flex-initial inline-flex items-center justify-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-status-success hover:bg-status-success/90 text-surface-container-lowest font-semibold text-xs shadow transition-colors active:scale-95 cursor-pointer"
              title={t('calls.joinGroupCall')}
            >
              <PhoneForwarded className="w-3.5 h-3.5" />
              <span>{t('calls.joinCall')}</span>
            </button>
          </div>
        </div>
      </div>
    )
  }

  if (!crossDeviceCall) {
    return null
  }

  const istGruppe = crossDeviceCall.art === 'gruppe'
  const partnerName = istGruppe
    ? crossDeviceCall.group_name || t('calls.groupCall')
    : crossDeviceCall.partner?.username || t('calls.peer')
  const deviceLabel = t(deviceLabelKey(crossDeviceCall.device_type))

  const DeviceIcon =
    crossDeviceCall.device_type === 'mobile'
      ? Smartphone
      : crossDeviceCall.device_type === 'desktop'
        ? Monitor
        : Globe

  return (
    <div
      role="region"
      aria-label={t('calls.activeOnOtherDevice')}
      data-testid="cross-device-call-banner"
      className={`relative z-40 w-full shrink-0 overflow-hidden bg-gradient-to-r from-emerald-950/90 via-emerald-900/90 to-teal-950/90 border-b border-status-success/30 text-on-surface px-4 py-2.5 shadow-lg backdrop-blur-md transition-all duration-300 ${className}`}
    >
      <div className="max-w-7xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3 text-xs sm:text-sm">
        {/* Linke Seite: Pulsierender Status & Anruf-Info */}
        <div className="flex items-center gap-3 min-w-0 w-full sm:w-auto">
          <div className="relative flex items-center justify-center w-8 h-8 rounded-full bg-status-success/20 text-status-success shrink-0 border border-status-success/40">
            <Radio className="w-4 h-4 animate-pulse text-status-success" />
            <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 bg-status-success rounded-full animate-ping opacity-75" />
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-semibold text-white tracking-tight">
                {crossDeviceCall.device_id === getDeviceId()
                  ? t('calls.ongoingRejoin')
                  : t('calls.alreadyInCall')}
              </span>
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-label-sm font-medium bg-status-success/20 text-status-success border border-status-success/30">
                <DeviceIcon className="w-3 h-3" />
                {deviceLabel}
              </span>
            </div>
            <div className="text-xs text-on-surface-variant truncate">
              {istGruppe ? t('calls.groupPrefix') : t('calls.withPrefix')}{' '}
              <span className="font-medium text-on-surface">{partnerName}</span>
              <span className="mx-1.5 opacity-40">·</span>
              <span className="capitalize">{t('calls.modeCall', { mode: crossDeviceCall.mode })}</span>
            </div>
          </div>
        </div>

        {/* Rechte Seite: Aktionsknöpfe wie bei Discord */}
        <div className="flex items-center gap-2 shrink-0 w-full sm:w-auto justify-end">
          <button
            type="button"
            onClick={() => void transferCallToThisDevice()}
            className="flex-1 sm:flex-initial inline-flex items-center justify-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-status-success hover:bg-status-success/90 text-surface-container-lowest font-semibold text-xs shadow transition-colors active:scale-95 cursor-pointer"
            title={crossDeviceCall.device_id === getDeviceId() ? t('calls.resumeCall') : t('calls.transferToThisDevice')}
          >
            <PhoneForwarded className="w-3.5 h-3.5" />
            <span>
              {crossDeviceCall.device_id === getDeviceId()
                ? t('calls.resumeCall')
                : t('calls.joinOnThisDevice')}
            </span>
          </button>

          <button
            type="button"
            onClick={() => void terminateCrossDeviceCall()}
            className="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg bg-status-destructive/20 hover:bg-status-destructive/30 text-status-destructive hover:text-status-destructive/80 border border-status-destructive/30 text-xs font-medium transition-colors active:scale-95 cursor-pointer"
            title={t('calls.endOnAllDevices')}
          >
            <PhoneOff className="w-3.5 h-3.5" />
            <span>{t('calls.hangUp')}</span>
          </button>
        </div>
      </div>
    </div>
  )
}
