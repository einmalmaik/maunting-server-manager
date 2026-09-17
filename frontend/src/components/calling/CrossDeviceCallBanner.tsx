import React, { useEffect } from 'react'
import { Phone, PhoneOff, PhoneForwarded, Smartphone, Monitor, Globe, Radio } from 'lucide-react'
import { useCallStore } from '@/stores/useCallStore'
import { formatDeviceLabel } from '@/lib/deviceIdentity'

interface CrossDeviceCallBannerProps {
  className?: string
}

export const CrossDeviceCallBanner: React.FC<CrossDeviceCallBannerProps> = ({ className = '' }) => {
  const {
    crossDeviceCall,
    state,
    checkActiveCall,
    transferCallToThisDevice,
    terminateCrossDeviceCall,
    handleCrossDeviceEvent,
  } = useCallStore()

  // Beim Mounten und bei Reconnects nachsehen, ob ein aktiver Anruf vorliegt
  useEffect(() => {
    void checkActiveCall()
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

  // Nur anzeigen, wenn auf einem anderen Gerät ein Anruf aktiv ist und das eigene Gerät idle ist
  if (!crossDeviceCall || state !== 'idle') {
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
                Du bist bereits in einem Anruf
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
            title="Diesen Anruf auf dieses Gerät übertragen"
          >
            <PhoneForwarded className="w-3.5 h-3.5" />
            <span>Auf diesem Gerät beitreten</span>
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
