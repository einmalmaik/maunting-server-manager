import { useState, type FormEvent } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'
import { ServerCog, Eye, EyeOff, Loader2, Shield } from 'lucide-react'
import toast from 'react-hot-toast'
import { useQueryClient } from '@tanstack/react-query'
import { useAuth, ApiError } from '@/hooks/useAuth'
import { authApi, auth2faApi } from '@/lib/api'
import { getDefaultRoute } from '@/lib/permissions'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useUiLanguage } from '@/lib/ui-language'

const BRAND_LINKS = [
  { key: 'website', labelKey: 'website', href: 'https://mauntingstudios.de' },
  { key: 'passwordManager', labelKey: 'passwordManager', href: 'https://singravault.mauntingstudios.de' },
  { key: 'ai', labelKey: 'ai', href: 'https://singra.mauntingstudios.de' },
] as const

export default function LoginPage() {
  const { user, isLoading } = useAuth()
  const { copy } = useUiLanguage()
  const t = copy.loginPage
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPw, setShowPw] = useState(false)
  const [pending, setPending] = useState(false)
  const [needs2fa, setNeeds2fa] = useState(false)
  const [twoFaCode, setTwoFaCode] = useState('')

  if (isLoading) return null
  if (user) return <Navigate to={getDefaultRoute(user)} replace />

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!username.trim() || !password) {
      toast.error(t.credentialsRequired)
      return
    }
    setPending(true)
    try {
      const res = await authApi.login(username.trim(), password)
      if ('needs_2fa' in res && res.needs_2fa) {
        setNeeds2fa(true)
      } else if ('user' in res) {
        await queryClient.invalidateQueries({ queryKey: ['auth', 'me'] })
        navigate(getDefaultRoute(res.user), { replace: true })
      }
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t.loginFailed)
    } finally {
      setPending(false)
    }
  }

  const handle2fa = async (e: FormEvent) => {
    e.preventDefault()
    const normalized = twoFaCode.trim()
    if (!normalized) return
    setPending(true)
    try {
      const res = await auth2faApi.verify(normalized)
      await queryClient.invalidateQueries({ queryKey: ['auth', 'me'] })
      navigate(getDefaultRoute(res.user), { replace: true })
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t.invalidTwoFactorCode)
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      <div className="animate-auth-slide-in flex w-full flex-col items-center justify-center px-8 sm:w-[55%]">
        <div className="w-full max-w-sm space-y-8">
          <div className="flex flex-col items-center gap-3">
            <div
              className="flex h-14 w-14 items-center justify-center rounded-xl border border-accent/30 bg-accent/10 shadow-lg"
              style={{ boxShadow: '0 0 32px hsl(194 44% 68% / 0.15)' }}
            >
              {needs2fa ? <Shield className="h-7 w-7 text-accent" /> : <ServerCog className="h-7 w-7 text-accent" />}
            </div>
            <div className="text-center">
              <h1 className="font-display text-2xl font-bold uppercase tracking-widest text-foreground">
                Maunting Server Manager
              </h1>
              <p className="mt-1 text-xs tracking-wide text-muted-foreground">
                {needs2fa ? t.twoFactorTitle : t.panelTitle}
              </p>
            </div>
          </div>

          {!needs2fa && (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="username">{t.username}</Label>
                <Input
                  id="username"
                  autoComplete="username"
                  placeholder="admin"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  disabled={pending}
                  required
                  className="h-10"
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="password">{t.password}</Label>
                <div className="relative">
                  <Input
                    id="password"
                    type={showPw ? 'text' : 'password'}
                    autoComplete="current-password"
                    placeholder="........"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    disabled={pending}
                    required
                    className="h-10 pr-10"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPw((s) => !s)}
                    aria-label={showPw ? t.hidePassword : t.showPassword}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
                  >
                    {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>

              <Button type="submit" className="mt-2 h-10 w-full" disabled={pending}>
                {pending ? <><Loader2 className="h-4 w-4 animate-spin" />&nbsp;{t.signingIn}</> : t.signIn}
              </Button>

              <div className="flex items-center justify-between text-xs">
                <Link to="/register" className="text-accent hover:underline">
                  Create account
                </Link>
                <Link to="/forgot-password" className="text-muted-foreground transition-colors hover:text-foreground">
                  Forgot password?
                </Link>
              </div>
            </form>
          )}

          {needs2fa && (
            <form onSubmit={handle2fa} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="2fa-code">{t.twoFactorLabel}</Label>
                <Input
                  id="2fa-code"
                  inputMode="text"
                  autoComplete="one-time-code"
                  placeholder={t.twoFactorPlaceholder}
                  value={twoFaCode}
                  onChange={(e) => setTwoFaCode(e.target.value.toUpperCase().replace(/[^A-Z0-9-]/g, '').slice(0, 9))}
                  disabled={pending}
                  autoFocus
                  className="h-10 text-center font-mono text-xl tracking-widest"
                  maxLength={9}
                />
                <p className="text-center text-xs text-muted-foreground">
                  {t.twoFactorHelp}
                </p>
              </div>
              <Button type="submit" className="h-10 w-full" disabled={!twoFaCode.trim() || pending}>
                {pending ? <><Loader2 className="h-4 w-4 animate-spin" />&nbsp;{t.verifying}</> : t.verify}
              </Button>
              <button
                type="button"
                className="w-full text-xs text-muted-foreground transition-colors hover:text-foreground"
                onClick={() => { setNeeds2fa(false); setTwoFaCode('') }}
              >
                {t.backToLogin}
              </button>
            </form>
          )}

          <div className="rounded-xl border border-accent/15 bg-accent/5 p-3 text-center">
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-accent/80">
              {copy.branding.byline}
            </p>
            <div className="mt-2 flex flex-wrap justify-center gap-1.5">
              {BRAND_LINKS.map((link) => {
                const label = copy.branding[link.labelKey]
                return (
                  <a
                    key={link.key}
                    href={link.href}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-full border border-accent/20 bg-background/80 px-2.5 py-1 text-[11px] text-foreground/75 transition-colors hover:border-accent/40 hover:text-accent"
                    title={copy.branding.openExternal(label)}
                    aria-label={copy.branding.openExternal(label)}
                  >
                    {label}
                  </a>
                )
              })}
            </div>
          </div>
        </div>
      </div>

      <div
        className="relative hidden w-[45%] flex-col items-center justify-center overflow-hidden border-l border-border sm:flex"
        style={{ background: 'var(--el-1)' }}
      >
        <div
          className="pointer-events-none absolute inset-0"
          style={{ background: 'radial-gradient(ellipse 80% 70% at 50% 50%, hsl(194 44% 68% / 0.08) 0%, transparent 70%)' }}
        />
        <div
          className="pointer-events-none absolute inset-0 opacity-30"
          style={{ backgroundImage: 'radial-gradient(hsl(194 44% 68% / 0.2) 1px, transparent 1px)', backgroundSize: '24px 24px' }}
        />
        <div className="relative z-10 flex flex-col items-center gap-6 p-12 text-center">
          <div
            className="flex h-24 w-24 items-center justify-center rounded-2xl border border-accent/20 bg-accent/5"
            style={{ boxShadow: '0 0 60px hsl(194 44% 68% / 0.12)' }}
          >
            <ServerCog className="h-12 w-12 animate-pulse-glow text-accent" />
          </div>
          <div className="space-y-2">
            <h2 className="font-display text-xl font-bold uppercase tracking-widest text-foreground/90">
              {t.heroTitle}
            </h2>
            <p className="max-w-xs text-sm leading-relaxed text-muted-foreground">
              {t.heroDescription}
            </p>
          </div>
          <div className="mt-2 flex flex-wrap justify-center gap-2">
            {[t.featureServerControl, t.featureAutoRestart, t.featureBackups, t.featureWorkshopMods, t.featureAuditLog].map((feature) => (
              <span key={feature} className="rounded-full border border-accent/20 bg-accent/5 px-3 py-1 text-xs text-accent/80">
                {feature}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
