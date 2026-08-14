import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'

declare global {
  interface Window {
    turnstile?: {
      render: (container: HTMLElement, options: any) => any
      remove: (id: any) => void
    }
    hcaptcha?: {
      render: (container: HTMLElement, options: any) => any
      reset: (id: any) => void
    }
    grecaptcha?: {
      render: (container: HTMLElement, options: any) => any
      reset: (id: any) => void
    }
  }
}

interface CaptchaWidgetProps {
  onVerify: (token: string) => void
}

interface CaptchaConfig {
  enabled: boolean
  provider: 'turnstile' | 'hcaptcha' | 'recaptcha' | 'none'
  site_key: string
}

export function CaptchaWidget({ onVerify }: CaptchaWidgetProps) {
  const { t } = useTranslation()
  const [config, setConfig] = useState<CaptchaConfig | null>(null)
  // Ohne diesen Zustand endet ein geblocktes Anbieterskript in einem leeren
  // Kasten: der Benutzer sendet ohne Token, das Backend lehnt ab, und der
  // einzige Hinweis steht in der Browserkonsole.
  const [loadFailed, setLoadFailed] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const widgetIdRef = useRef<any>(null)

  useEffect(() => {
    let active = true
    api<CaptchaConfig>('/auth/captcha-config')
      .then((data) => {
        if (active) setConfig(data)
      })
      .catch((err) => {
        console.error('Failed to load CAPTCHA config:', err)
      })
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    if (!config || !config.enabled || !containerRef.current) return

    const provider = config.provider
    const siteKey = config.site_key

    let scriptUrl = ''
    let checkGlobal = ''
    if (provider === 'turnstile') {
      scriptUrl = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'
      checkGlobal = 'turnstile'
    } else if (provider === 'hcaptcha') {
      scriptUrl = 'https://js.hcaptcha.com/1/api.js?render=explicit'
      checkGlobal = 'hcaptcha'
    } else if (provider === 'recaptcha') {
      scriptUrl = 'https://www.google.com/recaptcha/api.js?render=explicit'
      checkGlobal = 'grecaptcha'
    } else {
      return
    }

    const initWidget = () => {
      if (!containerRef.current) return
      containerRef.current.innerHTML = ''
      const widgetDiv = document.createElement('div')
      containerRef.current.appendChild(widgetDiv)

      try {
        if (provider === 'turnstile' && window.turnstile) {
          widgetIdRef.current = window.turnstile.render(widgetDiv, {
            sitekey: siteKey,
            callback: onVerify,
          })
        } else if (provider === 'hcaptcha' && window.hcaptcha) {
          widgetIdRef.current = window.hcaptcha.render(widgetDiv, {
            sitekey: siteKey,
            callback: onVerify,
          })
        } else if (provider === 'recaptcha' && window.grecaptcha) {
          widgetIdRef.current = window.grecaptcha.render(widgetDiv, {
            sitekey: siteKey,
            callback: onVerify,
          })
        }
      } catch (err) {
        console.error('Failed to render CAPTCHA:', err)
      }
    }

    if ((window as any)[checkGlobal]) {
      initWidget()
    } else {
      let script = document.querySelector(`script[src^="${scriptUrl.split('?')[0]}"]`) as HTMLScriptElement
      if (!script) {
        script = document.createElement('script')
        script.src = scriptUrl
        script.async = true
        script.defer = true
        document.head.appendChild(script)
      }

      const handleLoad = () => {
        let attempts = 0
        const checkInterval = setInterval(() => {
          attempts++
          if ((window as any)[checkGlobal]) {
            clearInterval(checkInterval)
            initWidget()
          } else if (attempts > 50) {
            clearInterval(checkInterval)
            setLoadFailed(true)
          }
        }, 100)
      }

      // Wird das Skript geblockt, feuert `load` nie — ohne diesen Listener
      // liefe nicht einmal der Timeout oben, und es bliebe vollständig still.
      const handleError = () => setLoadFailed(true)

      script.addEventListener('load', handleLoad)
      script.addEventListener('error', handleError)
      return () => {
        script.removeEventListener('load', handleLoad)
        script.removeEventListener('error', handleError)
      }
    }

    return () => {
      try {
        if (widgetIdRef.current !== null) {
          if (provider === 'turnstile' && window.turnstile) {
            window.turnstile.remove(widgetIdRef.current)
          } else if (provider === 'hcaptcha' && window.hcaptcha) {
            window.hcaptcha.reset(widgetIdRef.current)
          } else if (provider === 'recaptcha' && window.grecaptcha) {
            window.grecaptcha.reset(widgetIdRef.current)
          }
        }
      } catch (e) {
        // Ignore cleanup errors
      }
    }
  }, [config])

  if (!config || !config.enabled) return null

  if (loadFailed) {
    return (
      <p role="alert" className="my-4 text-center font-body-md text-sm text-error">
        {t('auth.captchaLoadFailed')}
      </p>
    )
  }

  return (
    <div className="flex justify-center my-4 msm-captcha-container" ref={containerRef} />
  )
}
