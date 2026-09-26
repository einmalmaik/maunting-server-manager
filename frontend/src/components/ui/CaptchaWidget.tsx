import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { DisBadge } from '@/components/DisBadge'

declare global {
  interface Window {
    turnstile?: {
      render: (container: HTMLElement, options: any) => any
      reset?: (id: any) => void
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
  resetKey?: any
}

interface CaptchaConfig {
  enabled: boolean
  provider: 'altcha' | 'turnstile' | 'hcaptcha' | 'recaptcha' | 'none'
  site_key: string
}

export function CaptchaWidget({ onVerify, resetKey }: CaptchaWidgetProps) {
  const { t, i18n } = useTranslation()
  const [config, setConfig] = useState<CaptchaConfig | null>(null)
  // Ohne diesen Zustand endet ein geblocktes Anbieterskript in einem leeren
  // Kasten: der Benutzer sendet ohne Token, das Backend lehnt ab, und der
  // einzige Hinweis steht in der Browserkonsole.
  const [loadFailed, setLoadFailed] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const widgetIdRef = useRef<any>(null)
  const onVerifyRef = useRef(onVerify)
  onVerifyRef.current = onVerify

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
    if (resetKey === undefined || !containerRef.current) return
    if (config?.provider === 'altcha') {
      const widget = containerRef.current.querySelector('altcha-widget') as any
      if (widget && typeof widget.reset === 'function') {
        widget.reset()
        onVerifyRef.current('')
      }
    } else if (widgetIdRef.current !== null) {
      if (config?.provider === 'turnstile' && window.turnstile?.remove) {
        window.turnstile.reset?.(widgetIdRef.current)
        onVerifyRef.current('')
      } else if (config?.provider === 'hcaptcha' && window.hcaptcha?.reset) {
        window.hcaptcha.reset(widgetIdRef.current)
        onVerifyRef.current('')
      } else if (config?.provider === 'recaptcha' && window.grecaptcha?.reset) {
        window.grecaptcha.reset(widgetIdRef.current)
        onVerifyRef.current('')
      }
    }
  }, [resetKey, config?.provider])

  useEffect(() => {
    if (!config || !config.enabled || !containerRef.current) return

    const provider = config.provider
    const siteKey = config.site_key

    if (provider === 'altcha') {
      let active = true
      import('altcha')
        .then(() => {
          if (!active || !containerRef.current) return
          containerRef.current.innerHTML = ''
          const widget = document.createElement('altcha-widget')
          const challengeUrl = typeof window !== 'undefined' && window.location?.origin
            ? new URL('/api/auth/captcha-challenge', window.location.origin).href
            : '/api/auth/captcha-challenge'
          widget.setAttribute('challenge', challengeUrl)
          widget.setAttribute('challengeurl', challengeUrl)
          widget.setAttribute('auto', 'onload')
          const lang = (i18n.language || 'de').startsWith('de') ? 'de' : 'en'
          widget.setAttribute('language', lang)
          const isDark = typeof document !== 'undefined' && document.documentElement.classList.contains('dark')
          widget.setAttribute('theme', isDark ? 'dark' : 'auto')
          widget.setAttribute('hidefooter', 'true')
          widget.setAttribute(
            'strings',
            JSON.stringify({
              label: t('captcha.altcha.label', 'Ich bin ein Mensch'),
              verifying: t('captcha.altcha.verifying', 'Sicherheitsprüfung läuft …'),
              verified: t('captcha.altcha.verified', 'Verifiziert'),
              error: t('captcha.altcha.error', 'Sicherheitsprüfung fehlgeschlagen'),
            }),
          )

          const handleStateChange = (ev: Event) => {
            const customEv = ev as CustomEvent
            const st = customEv.detail?.state
            if (st === 'verified' && customEv.detail?.payload) {
              onVerifyRef.current(customEv.detail.payload)
            } else if (st === 'expired' || st === 'unverified') {
              onVerifyRef.current('')
            } else if (st === 'error') {
              onVerifyRef.current('')
              setLoadFailed(true)
            }
          }
          const handleVerified = (ev: Event) => {
            const customEv = ev as CustomEvent
            if (customEv.detail?.payload) {
              onVerifyRef.current(customEv.detail.payload)
            }
          }
          const handleExpired = () => {
            onVerifyRef.current('')
          }
          const handleError = () => {
            onVerifyRef.current('')
            setLoadFailed(true)
          }

          widget.addEventListener('statechange', handleStateChange)
          widget.addEventListener('verified', handleVerified)
          widget.addEventListener('expired', handleExpired)
          widget.addEventListener('error', handleError)
          containerRef.current.appendChild(widget)
        })
        .catch((err) => {
          console.error('Failed to load ALTCHA widget:', err)
          setLoadFailed(true)
        })

      return () => {
        active = false
        if (containerRef.current) {
          containerRef.current.innerHTML = ''
        }
      }
    }

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
            callback: (token: string) => onVerifyRef.current(token),
          })
        } else if (provider === 'hcaptcha' && window.hcaptcha) {
          widgetIdRef.current = window.hcaptcha.render(widgetDiv, {
            sitekey: siteKey,
            callback: (token: string) => onVerifyRef.current(token),
          })
        } else if (provider === 'recaptcha' && window.grecaptcha) {
          widgetIdRef.current = window.grecaptcha.render(widgetDiv, {
            sitekey: siteKey,
            callback: (token: string) => onVerifyRef.current(token),
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
    <div className="flex flex-col items-center justify-center my-4 gap-2">
      <div ref={containerRef} />
      {config.provider === 'altcha' && (
        <DisBadge size={14} className="py-0.5 px-2" />
      )}
    </div>
  )
}
