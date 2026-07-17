import { useEffect } from 'react'
import { loadSupportWidget, SUPPORT_WIDGET_UPDATED_EVENT } from '@/lib/supportWidgetLoader'

export function SupportWidgetLoader() {
  useEffect(() => {
    void loadSupportWidget()
    const onUpdate = () => {
      void loadSupportWidget()
    }
    window.addEventListener(SUPPORT_WIDGET_UPDATED_EVENT, onUpdate)
    return () => {
      window.removeEventListener(SUPPORT_WIDGET_UPDATED_EVENT, onUpdate)
    }
  }, [])

  return null
}