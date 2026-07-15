import { useEffect } from 'react'

const SINGRA_SCRIPT_SRC = 'https://singrabot.mauntingstudios.de/widget.js'
const SCRIPT_ATTR = 'data-msm-support-widget'

interface PublicSupportWidget {
  enabled: boolean
  mode: string
  singra_widget_id?: string
  custom_snippet?: string
  script_src?: string
}

function removeWidgetArtifacts() {
  document.querySelectorAll(`script[${SCRIPT_ATTR}]`).forEach((el) => el.remove())
  document.getElementById('msm-support-widget-custom')?.remove()
}

function injectSingra(widgetId: string, scriptSrc: string) {
  if (!widgetId.trim()) return
  const existing = document.querySelector(`script[${SCRIPT_ATTR}]`) as HTMLScriptElement | null
  if (existing?.getAttribute('data-widget-id') === widgetId) return
  removeWidgetArtifacts()
  const script = document.createElement('script')
  script.setAttribute(SCRIPT_ATTR, 'singra')
  script.src = scriptSrc
  script.defer = true
  script.setAttribute('data-widget-id', widgetId)
  document.body.appendChild(script)
}

function injectCustom(snippet: string) {
  if (!snippet.trim()) return
  removeWidgetArtifacts()
  const holder = document.createElement('div')
  holder.id = 'msm-support-widget-custom'
  holder.setAttribute(SCRIPT_ATTR, 'custom')
  holder.innerHTML = snippet
  document.body.appendChild(holder)
  holder.querySelectorAll('script').forEach((old) => {
    const fresh = document.createElement('script')
    fresh.setAttribute(SCRIPT_ATTR, 'custom')
    Array.from(old.attributes).forEach((attr) => fresh.setAttribute(attr.name, attr.value))
    if (old.textContent) fresh.textContent = old.textContent
    old.replaceWith(fresh)
  })
}

export function SupportWidgetLoader() {
  useEffect(() => {
    let cancelled = false
    fetch('/api/system/support-widget')
      .then((res) => (res.ok ? res.json() : null))
      .then((cfg: PublicSupportWidget | null) => {
        if (cancelled || !cfg?.enabled) {
          removeWidgetArtifacts()
          return
        }
        if (cfg.mode === 'custom' && cfg.custom_snippet) {
          injectCustom(cfg.custom_snippet)
          return
        }
        const id = cfg.singra_widget_id ?? ''
        const src = cfg.script_src ?? SINGRA_SCRIPT_SRC
        injectSingra(id, src)
      })
      .catch(() => {
        if (!cancelled) removeWidgetArtifacts()
      })
    return () => {
      cancelled = true
      removeWidgetArtifacts()
    }
  }, [])

  return null
}