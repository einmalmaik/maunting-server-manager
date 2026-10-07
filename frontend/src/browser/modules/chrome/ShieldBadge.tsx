import { useState, useRef, useEffect } from 'react'
import { Shield, ShieldAlert, ShieldCheck, Check, Power } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useBrowserStore } from '../../services/browserStore'

export function ShieldBadge() {
  const { t } = useTranslation()
  const {
    adblockEnabled,
    blockedAdsCount,
    blockedTrackersCount,
    toggleAdblock,
    tabs,
    activeTabId,
  } = useBrowserStore()

  const [isOpen, setIsOpen] = useState(false)
  const popupRef = useRef<HTMLDivElement>(null)

  const activeTab = tabs.find((t) => t.id === activeTabId)
  let domain = t('browser.shield.thisPage')
  try {
    if (activeTab?.url && !activeTab.url.startsWith('about:')) {
      domain = new URL(activeTab.url).hostname
    }
  } catch {
    // Ungültige URL oder lokale Seite
  }

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (popupRef.current && !popupRef.current.contains(e.target as Node)) {
        setIsOpen(false)
      }
    }
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [isOpen])

  return (
    <div className="relative" ref={popupRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        aria-label={adblockEnabled ? t('browser.shield.active') : t('browser.shield.paused')}
        className={`flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium transition-colors ${
          adblockEnabled
            ? 'text-status-success hover:bg-muted'
            : 'text-status-warning hover:bg-muted'
        }`}
      >
        {adblockEnabled ? (
          <ShieldCheck className="w-4 h-4 text-status-success" />
        ) : (
          <ShieldAlert className="w-4 h-4 text-status-warning" />
        )}
        <span className="hidden sm:inline">
          {blockedAdsCount + blockedTrackersCount}
        </span>
      </button>

      {isOpen && (
        <div className="absolute right-0 mt-2 w-72 bg-popover/95 backdrop-blur-md border border-border rounded-xl shadow-2xl p-4 z-50 text-foreground animate-scale-in">
          <div className="flex items-center justify-between pb-3 border-b border-border">
            <div className="flex items-center gap-2">
              <Shield className="w-5 h-5 text-status-success" />
              <div>
                <h4 className="text-sm font-semibold leading-tight">{t('browser.shield.title')}</h4>
                <p className="text-xs text-muted-foreground truncate max-w-[140px]">{domain}</p>
              </div>
            </div>
            <button
              onClick={toggleAdblock}
              className={`p-1.5 rounded-lg border transition-colors ${
                adblockEnabled
                  ? 'bg-muted border-border text-status-success hover:bg-muted/80'
                  : 'bg-muted border-border text-muted-foreground hover:bg-muted/80'
              }`}
              aria-label={adblockEnabled ? t('browser.shield.disable') : t('browser.shield.enable')}
            >
              <Power className="w-4 h-4" />
            </button>
          </div>

          <div className="py-3 space-y-2 text-xs">
            <div className="flex justify-between items-center py-1">
              <span className="text-muted-foreground">{t('browser.shield.adsBlocked')}</span>
              <span className="font-semibold px-2 py-0.5 rounded bg-muted">
                {t('browser.shield.blockedCount', { count: blockedAdsCount })}
              </span>
            </div>
            <div className="flex justify-between items-center py-1">
              <span className="text-muted-foreground">{t('browser.shield.trackersBlocked')}</span>
              <span className="font-semibold px-2 py-0.5 rounded bg-muted">
                {t('browser.shield.blockedCount', { count: blockedTrackersCount })}
              </span>
            </div>
            <div className="flex justify-between items-center py-1">
              <span className="text-muted-foreground">{t('browser.shield.privacyMode')}</span>
              <span className="text-status-success font-medium flex items-center gap-1">
                <Check className="w-3 h-3" /> {t('browser.shield.httpsEnforced')}
              </span>
            </div>
          </div>

          <div className="pt-2 border-t border-border flex justify-between items-center text-label-sm text-muted-foreground">
            <span>{t('browser.shield.strict')}</span>
          </div>
        </div>
      )}
    </div>
  )
}
