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

/**
 * - `loading`: Konfiguration oder Anbieterskript noch nicht da
 * - `ready`: Widget steht, wartet auf den Menschen
 * - `verified`: gültiges Token liegt vor
 * - `failed`: Skript oder Konfiguration nicht ladbar
 * - `disabled`: keine Sicherheitsabfrage konfiguriert
 */
export type CaptchaStatus = 'loading' | 'ready' | 'verified' | 'failed' | 'disabled'

/** Solange das gilt, bleiben Anmelden und Social Login gesperrt. */
export function captchaSperrt(status: CaptchaStatus): boolean {
  return status !== 'verified' && status !== 'disabled'
}

interface CaptchaWidgetProps {
  onVerify: (token: string) => void
  onStatusChange?: (status: CaptchaStatus) => void
}

interface CaptchaConfig {
  enabled: boolean
  provider: 'turnstile' | 'hcaptcha' | 'recaptcha' | 'none'
  site_key: string
}

export function CaptchaWidget({ onVerify, onStatusChange }: CaptchaWidgetProps) {
  const { t } = useTranslation()
  const [config, setConfig] = useState<CaptchaConfig | null>(null)
  // Ohne diesen Zustand endet ein geblocktes Anbieterskript in einem leeren
  // Kasten: der Benutzer sendet ohne Token, das Backend lehnt ab, und der
  // einzige Hinweis steht in der Browserkonsole.
  const [loadFailed, setLoadFailed] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const widgetIdRef = useRef<any>(null)
  // Die Anbieter behalten die Rückrufe vom ersten Rendern; über Refs sehen sie
  // trotzdem immer die aktuellen Funktionen der Seite.
  const onVerifyRef = useRef(onVerify)
  const onStatusRef = useRef(onStatusChange)
  onVerifyRef.current = onVerify
  onStatusRef.current = onStatusChange

  const melde = (status: CaptchaStatus) => onStatusRef.current?.(status)

  useEffect(() => {
    let active = true
    melde('loading')
    api<CaptchaConfig>('/auth/captcha-config')
      .then((data) => {
        if (!active) return
        setConfig(data)
        if (!data.enabled || data.provider === 'none') melde('disabled')
      })
      .catch((err) => {
        console.error('Failed to load CAPTCHA config:', err)
        // Ohne Konfiguration wissen wir nicht, ob das Backend eine Abfrage
        // verlangt. Gesperrt bleiben ist die ehrliche Antwort.
        if (!active) return
        setLoadFailed(true)
        melde('failed')
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

    // Tokens laufen ab (Turnstile nach 300 s) und gelten nur einmal. Ein
    // abgelaufenes Token sperrt deshalb wieder, statt still weiterzugelten.
    const options = {
      sitekey: siteKey,
      callback: (token: string) => {
        onVerifyRef.current(token)
        melde('verified')
      },
      'expired-callback': () => {
        onVerifyRef.current('')
        melde('ready')
      },
      'error-callback': () => {
        onVerifyRef.current('')
        melde('ready')
      },
    }

    const initWidget = () => {
      if (!containerRef.current) return
      containerRef.current.innerHTML = ''
      const widgetDiv = document.createElement('div')
      containerRef.current.appendChild(widgetDiv)

      try {
        if (provider === 'turnstile' && window.turnstile) {
          widgetIdRef.current = window.turnstile.render(widgetDiv, options)
        } else if (provider === 'hcaptcha' && window.hcaptcha) {
          widgetIdRef.current = window.hcaptcha.render(widgetDiv, options)
        } else if (provider === 'recaptcha' && window.grecaptcha) {
          widgetIdRef.current = window.grecaptcha.render(widgetDiv, options)
        }
        melde('ready')
      } catch (err) {
        console.error('Failed to render CAPTCHA:', err)
        setLoadFailed(true)
        melde('failed')
      }
    }

    const scheitern = () => {
      setLoadFailed(true)
      melde('failed')
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

      let checkInterval: ReturnType<typeof setInterval> | null = null
      const handleLoad = () => {
        let attempts = 0
        checkInterval = setInterval(() => {
          attempts++
          if ((window as any)[checkGlobal]) {
            if (checkInterval) clearInterval(checkInterval)
            initWidget()
          } else if (attempts > 50) {
            if (checkInterval) clearInterval(checkInterval)
            scheitern()
          }
        }, 100)
      }

      // Wird das Skript geblockt, feuert `load` nie — ohne diesen Listener
      // liefe nicht einmal der Timeout oben, und es bliebe vollständig still.
      script.addEventListener('load', handleLoad)
      script.addEventListener('error', scheitern)
      return () => {
        if (checkInterval) clearInterval(checkInterval)
        script.removeEventListener('load', handleLoad)
        script.removeEventListener('error', scheitern)
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

  if (loadFailed) {
    return (
      <p role="alert" className="my-4 text-center font-body-md text-sm text-error">
        {t('auth.captchaLoadFailed')}
      </p>
    )
  }

  if (!config || !config.enabled) return null

  return (
    <div className="flex justify-center my-4" ref={containerRef} />
  )
}
