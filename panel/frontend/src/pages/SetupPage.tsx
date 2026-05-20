import { useState, type FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { ServerCog, Eye, EyeOff, Loader2 } from 'lucide-react'
import toast from 'react-hot-toast'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { setupApi, ApiError } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useUiLanguage } from '@/lib/ui-language'

export default function SetupPage() {
  const { copy } = useUiLanguage()
  const t = copy.setupPage
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPw, setShowPw] = useState(false)
  const [showConfirmPw, setShowConfirmPw] = useState(false)
  const [pending, setPending] = useState(false)

  const { data: setupStatus, isLoading: statusLoading, isError: statusError } = useQuery({
    queryKey: ['setup', 'status'],
    queryFn: setupApi.status,
    retry: false,
    staleTime: Infinity,
  })

  // Redirect away if setup is already done
  if (statusLoading) return (
    <div className="flex h-screen w-full items-center justify-center bg-background">
      <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
    </div>
  )
  if (statusError) return (
    <div className="flex h-screen w-full items-center justify-center bg-background">
      <p className="text-sm text-destructive">{t.loadFailed}</p>
    </div>
  )
  if (setupStatus && !setupStatus.needs_setup) return <Navigate to="/login" replace />

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!username.trim()) {
      toast.error(t.usernameRequired)
      return
    }
    if (password.length < 8) {
      toast.error(t.passwordTooShort)
      return
    }
    if (password !== confirmPassword) {
      toast.error(t.passwordsDoNotMatch)
      return
    }
    setPending(true)
    try {
      await setupApi.createOwner(username.trim(), password)
      // Invalidate auth and setup queries so guards re-evaluate
      await queryClient.invalidateQueries({ queryKey: ['auth', 'me'] })
      queryClient.setQueryData(['setup', 'status'], { needs_setup: false })
      navigate('/dashboard', { replace: true })
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : t.setupFailed
      toast.error(msg)
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="flex h-screen w-full bg-background overflow-hidden">
      {/* ── Left: Form panel ──────────────────────────────────── */}
      <div className="flex w-full flex-col items-center justify-center px-8 sm:w-[55%] animate-auth-slide-in">
        <div className="w-full max-w-sm space-y-8">
          {/* Brand */}
          <div className="flex flex-col items-center gap-3">
            <div
              className="flex h-14 w-14 items-center justify-center rounded-xl border border-accent/30 bg-accent/10 shadow-lg"
              style={{ boxShadow: '0 0 32px hsl(194 44% 68% / 0.15)' }}
            >
              <ServerCog className="h-7 w-7 text-accent" />
            </div>
            <div className="text-center">
              <h1 className="font-display text-2xl font-bold uppercase tracking-widest text-foreground">
                Maunting Server Panel
              </h1>
              <p className="mt-1 text-xs text-muted-foreground tracking-wide">
                {t.subtitle}
              </p>
            </div>
          </div>

          {/* Form */}
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
                  autoComplete="new-password"
                  placeholder={t.passwordPlaceholder}
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
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                >
                  {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="confirm-password">{t.confirmPassword}</Label>
              <div className="relative">
                <Input
                  id="confirm-password"
                  type={showConfirmPw ? 'text' : 'password'}
                  autoComplete="new-password"
                  placeholder={t.confirmPasswordPlaceholder}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  disabled={pending}
                  required
                  className="h-10 pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowConfirmPw((s) => !s)}
                  aria-label={showConfirmPw ? t.hideConfirmPassword : t.showConfirmPassword}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                >
                  {showConfirmPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            <Button type="submit" className="w-full h-10 mt-2" disabled={pending}>
              {pending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t.creatingAccount}
                </>
              ) : (
                t.createOwner
              )}
            </Button>
          </form>

          <p className="text-center text-xs text-muted-foreground/60">
            {t.appearsOnce}
          </p>
        </div>
      </div>

      {/* ── Right: Glow / brand panel (hidden on small screens) ── */}
      <div
        className="hidden sm:flex w-[45%] flex-col items-center justify-center relative overflow-hidden border-l border-border"
        style={{ background: 'var(--el-1)' }}
      >
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            background:
              'radial-gradient(ellipse 80% 70% at 50% 50%, hsl(194 44% 68% / 0.08) 0%, transparent 70%)',
          }}
        />
        <div
          className="absolute inset-0 pointer-events-none opacity-30"
          style={{
            backgroundImage: 'radial-gradient(hsl(194 44% 68% / 0.2) 1px, transparent 1px)',
            backgroundSize: '24px 24px',
          }}
        />
        <div className="relative z-10 flex flex-col items-center gap-6 p-12 text-center">
          <div
            className="flex h-24 w-24 items-center justify-center rounded-2xl border border-accent/20 bg-accent/5"
            style={{ boxShadow: '0 0 60px hsl(194 44% 68% / 0.12)' }}
          >
            <ServerCog className="h-12 w-12 text-accent animate-pulse-glow" />
          </div>
          <div className="space-y-2">
            <h2 className="font-display text-xl font-bold uppercase tracking-widest text-foreground/90">
              {t.heroTitle}
            </h2>
            <p className="text-sm text-muted-foreground max-w-xs leading-relaxed">
              {t.heroDescription}
            </p>
          </div>
          <div className="flex flex-wrap justify-center gap-2 mt-2">
            {[copy.loginPage.featureServerControl, copy.loginPage.featureAutoRestart, copy.loginPage.featureBackups, copy.loginPage.featureWorkshopMods, copy.loginPage.featureAuditLog].map((f) => (
              <span
                key={f}
                className="rounded-full border border-accent/20 bg-accent/5 px-3 py-1 text-xs text-accent/80"
              >
                {f}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
