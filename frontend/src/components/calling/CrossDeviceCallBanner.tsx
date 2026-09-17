import React, { useEffect } from 'react'
import { PhoneOff, PhoneForwarded, Smartphone, Monitor, Globe, Radio, Users } from 'lucide-react'
import { useCallStore, setzeAnrufIdentitaet } from '@/stores/useCallStore'
import { formatDeviceLabel, getDeviceId } from '@/lib/deviceIdentity'
import { useAuthStore } from '@/stores/authStore'
import { eigenesGeraet, geraetVeroeffentlichen } from '@/services/e2eeGeraet'

interface CrossDeviceCallBannerProps {
  className?: string
}

export const CrossDeviceCallBanner: React.FC<CrossDeviceCallBannerProps> = ({ className = '' }) => {
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
        aria-label="Aktiver Gruppenanruf"
        data-testid="active-group-call-banner"
        className={`relative z-40 w-full shrink-0 overflow-hidden bg-gradient-to-r from-emerald-950/90 via-emerald-900/90 to-teal-950/90 border-b border-emerald-500/30 text-emerald-100 px-4 py-2.5 shadow-lg backdrop-blur-md transition-all duration-300 ${className}`}
      >
        <div className="max-w-7xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3 text-xs sm:text-sm">
          {/* Linke Seite: Pulsierender Status & Gruppenanruf-Info */}
          <div className="flex items-center gap-3 min-w-0 w-full sm:w-auto">
            <div className="relative flex items-center justify-center w-8 h-8 rounded-full bg-emerald-500/20 text-emerald-400 shrink-0 border border-emerald-500/40">
              <Radio className="w-4 h-4 animate-pulse text-emerald-400" />
              <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 bg-emerald-400 rounded-full animate-ping opacity-75" />
            </div>

            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-semibold text-white tracking-tight">
                  Laufender Gruppenanruf
                </span>
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                  <Users className="w-3 h-3" />
                  {groupCall.participant_count > 0 ? `${groupCall.participant_count} aktiv` : 'Live'}
                </span>
              </div>
              <div className="text-xs text-emerald-200/80 truncate">
                Gruppe: <span className="font-medium text-emerald-100">{groupCall.group_name}</span>
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
              className="flex-1 sm:flex-initial inline-flex items-center justify-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-emerald-950 font-semibold text-xs shadow transition-colors active:scale-95 cursor-pointer"
              title="Dem laufenden Gruppenanruf beitreten"
            >
              <PhoneForwarded className="w-3.5 h-3.5" />
              <span>Anruf beitreten</span>
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
    ? crossDeviceCall.group_name || 'Gruppenanruf'
    : crossDeviceCall.partner?.username || 'Gesprächspartner'
  const deviceLabel = formatDeviceLabel(crossDeviceCall.device_type)

  const DeviceIcon =
    crossDeviceCall.device_type === 'mobile'
      ? Smartphone
      : crossDeviceCall.device_type === 'desktop'
        ? Monitor
        : Globe

  return (
    <div
      role="region"
      aria-label="Aktiver Anruf auf anderem Gerät"
      data-testid="cross-device-call-banner"
      className={`relative z-40 w-full shrink-0 overflow-hidden bg-gradient-to-r from-emerald-950/90 via-emerald-900/90 to-teal-950/90 border-b border-emerald-500/30 text-emerald-100 px-4 py-2.5 shadow-lg backdrop-blur-md transition-all duration-300 ${className}`}
    >
      <div className="max-w-7xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-3 text-xs sm:text-sm">
        {/* Linke Seite: Pulsierender Status & Anruf-Info */}
        <div className="flex items-center gap-3 min-w-0 w-full sm:w-auto">
          <div className="relative flex items-center justify-center w-8 h-8 rounded-full bg-emerald-500/20 text-emerald-400 shrink-0 border border-emerald-500/40">
            <Radio className="w-4 h-4 animate-pulse text-emerald-400" />
            <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 bg-emerald-400 rounded-full animate-ping opacity-75" />
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-semibold text-white tracking-tight">
                {crossDeviceCall.device_id === getDeviceId()
                  ? 'Laufender Anruf (wieder beitreten)'
                  : 'Du bist bereits in einem Anruf'}
              </span>
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                <DeviceIcon className="w-3 h-3" />
                {deviceLabel}
              </span>
            </div>
            <div className="text-xs text-emerald-200/80 truncate">
              {istGruppe ? 'Gruppe: ' : 'Mit: '}
              <span className="font-medium text-emerald-100">{partnerName}</span>
              <span className="mx-1.5 opacity-40">·</span>
              <span className="capitalize">{crossDeviceCall.mode}-Anruf</span>
            </div>
          </div>
        </div>

        {/* Rechte Seite: Aktionsknöpfe wie bei Discord */}
        <div className="flex items-center gap-2 shrink-0 w-full sm:w-auto justify-end">
          <button
            type="button"
            onClick={() => void transferCallToThisDevice()}
            className="flex-1 sm:flex-initial inline-flex items-center justify-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-emerald-500 hover:bg-emerald-400 text-emerald-950 font-semibold text-xs shadow transition-colors active:scale-95 cursor-pointer"
            title={crossDeviceCall.device_id === getDeviceId() ? 'Anruf fortsetzen' : 'Diesen Anruf auf dieses Gerät übertragen'}
          >
            <PhoneForwarded className="w-3.5 h-3.5" />
            <span>
              {crossDeviceCall.device_id === getDeviceId()
                ? 'Anruf fortsetzen'
                : 'Auf diesem Gerät beitreten'}
            </span>
          </button>

          <button
            type="button"
            onClick={() => void terminateCrossDeviceCall()}
            className="inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-500/20 hover:bg-red-500/30 text-red-300 hover:text-red-200 border border-red-500/30 text-xs font-medium transition-colors active:scale-95 cursor-pointer"
            title="Den Anruf auf allen Geräten beenden"
          >
            <PhoneOff className="w-3.5 h-3.5" />
            <span>Auflegen</span>
          </button>
        </div>
      </div>
    </div>
  )
}
