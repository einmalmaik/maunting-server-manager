import { useEffect } from 'react'
import { loadSupportWidget, SUPPORT_WIDGET_UPDATED_EVENT } from '@/lib/supportWidgetLoader'

export function SupportWidgetLoader({ enabled }: { enabled: boolean }) {
  useEffect(() => {
    if (!enabled) return
    void loadSupportWidget()
    const onUpdate = () => {
      void loadSupportWidget()
    }
    window.addEventListener(SUPPORT_WIDGET_UPDATED_EVENT, onUpdate)
    return () => {
      window.removeEventListener(SUPPORT_WIDGET_UPDATED_EVENT, onUpdate)
    }
  }, [enabled])

  return null
}
