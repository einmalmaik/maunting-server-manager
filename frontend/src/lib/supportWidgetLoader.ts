/**
 * Loads / refreshes the support widget from GET /api/system/support-widget.
 * Call after saving widget settings (dispatch msm:support-widget-updated).
 */
const SCRIPT_ATTR = 'data-msm-support-widget'
const SINGRA_SCRIPT_SRC = 'https://singrabot.mauntingstudios.de/widget.js'

export const SUPPORT_WIDGET_UPDATED_EVENT = 'msm:support-widget-updated'

interface PublicSupportWidget {
  enabled: boolean
  provider: string
  singra_widget_id?: string
  script_src?: string
  crisp_website_id?: string
  tawk_property_id?: string
  tawk_widget_id?: string
  custom_snippet?: string
}

function removeWidgetArtifacts() {
  document.querySelectorAll(`[${SCRIPT_ATTR}]`).forEach((el) => el.remove())
  document.getElementById('msm-support-widget-custom')?.remove()
}

function injectSingra(widgetId: string, scriptSrc: string) {
  if (!widgetId.trim()) return
  removeWidgetArtifacts()
  const script = document.createElement('script')
  script.setAttribute(SCRIPT_ATTR, 'singra')
  script.src = scriptSrc
  script.defer = true
  script.setAttribute('data-widget-id', widgetId)
  document.body.appendChild(script)
}

function injectCrisp(websiteId: string) {
  if (!websiteId.trim()) return
  removeWidgetArtifacts()
  const inline = document.createElement('script')
  inline.setAttribute(SCRIPT_ATTR, 'crisp')
  inline.textContent = `window.$crisp=[];window.CRISP_WEBSITE_ID="${websiteId.replace(/"/g, '')}";(function(){var d=document,s=d.createElement("script");s.src="https://client.crisp.chat/l.js";s.async=1;d.getElementsByTagName("head")[0].appendChild(s);})();`
  document.body.appendChild(inline)
}

function injectTawk(propertyId: string, widgetId: string) {
  if (!propertyId.trim() || !widgetId.trim()) return
  removeWidgetArtifacts()
  const inline = document.createElement('script')
  inline.setAttribute(SCRIPT_ATTR, 'tawk')
  inline.textContent = `var Tawk_API=Tawk_API||{},Tawk_LoadStart=new Date();(function(){var s1=document.createElement("script"),s0=document.getElementsByTagName("script")[0];s1.async=true;s1.src='https://embed.tawk.to/${propertyId.replace(/'/g, '')}/${widgetId.replace(/'/g, '')}';s1.charset='UTF-8';s1.setAttribute('crossorigin','*');s0.parentNode.insertBefore(s1,s0);})();`
  document.body.appendChild(inline)
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

export async function loadSupportWidget(): Promise<void> {
  try {
    const res = await fetch('/api/system/support-widget', { cache: 'no-store' })
    const cfg: PublicSupportWidget | null = res.ok ? await res.json() : null
    if (!cfg?.enabled) {
      removeWidgetArtifacts()
      return
    }
    switch (cfg.provider) {
      case 'crisp':
        injectCrisp(cfg.crisp_website_id ?? '')
        break
      case 'tawk':
        injectTawk(cfg.tawk_property_id ?? '', cfg.tawk_widget_id ?? '')
        break
      case 'custom':
        injectCustom(cfg.custom_snippet ?? '')
        break
      case 'singra':
      default:
        injectSingra(cfg.singra_widget_id ?? '', cfg.script_src ?? SINGRA_SCRIPT_SRC)
    }
  } catch {
    removeWidgetArtifacts()
  }
}

export function notifySupportWidgetUpdated() {
  window.dispatchEvent(new CustomEvent(SUPPORT_WIDGET_UPDATED_EVENT))
}