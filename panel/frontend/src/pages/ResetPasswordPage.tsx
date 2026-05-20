import { useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ServerCog, Eye, EyeOff, Loader2, CheckCircle, AlertTriangle } from 'lucide-react'
import toast from 'react-hot-toast'
import { authApi } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

export default function ResetPasswordPage() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPw, setShowPw] = useState(false)
  const [pending, setPending] = useState(false)
  const [done, setDone] = useState(false)

  if (!token) {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-background">
        <div className="max-w-sm space-y-4 text-center">
          <AlertTriangle className="mx-auto h-10 w-10 text-destructive" />
          <h1 className="text-lg font-semibold">Invalid Reset Link</h1>
          <p className="text-sm text-muted-foreground">
            The reset link is missing or invalid. Please request a new one.
          </p>
          <Link to="/forgot-password">
            <Button className="w-full">Request New Link</Button>
          </Link>
        </div>
      </div>
    )
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!password || password.length < 8) {
      toast.error('Password must be at least 8 characters.')
      return
    }
    if (password !== confirmPassword) {
      toast.error('Passwords do not match.')
      return
    }
    setPending(true)
    try {
      const res = await authApi.resetPassword(token, password)
      toast.success(res.message)
      setDone(true)
    } catch (err: any) {
      toast.error(err?.message || 'Reset failed.')
    } finally {
      setPending(false)
    }
  }

  if (done) {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-background">
        <div className="max-w-sm space-y-4 text-center">
          <CheckCircle className="mx-auto h-10 w-10 text-accent" />
          <h1 className="text-lg font-semibold">Password Reset</h1>
          <p className="text-sm text-muted-foreground">
            Your password has been updated successfully.
          </p>
          <Link to="/login">
            <Button className="w-full">Go to Login</Button>
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      <div className="flex w-full flex-col items-center justify-center px-8 sm:w-[55%]">
        <div className="w-full max-w-sm space-y-6">
          <div className="flex flex-col items-center gap-3">
            <div className="flex h-14 w-14 items-center justify-center rounded-xl border border-accent/30 bg-accent/10 shadow-lg"
              style={{ boxShadow: '0 0 32px hsl(194 44% 68% / 0.15)' }}>
              <ServerCog className="h-7 w-7 text-accent" />
            </div>
            <div className="text-center">
              <h1 className="font-display text-2xl font-bold uppercase tracking-widest text-foreground">
                New Password
              </h1>
              <p className="mt-1 text-xs tracking-wide text-muted-foreground">
                Enter your new password below
              </p>
            </div>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="password">New Password</Label>
              <div className="relative">
                <Input
                  id="password"
                  type={showPw ? 'text' : 'password'}
                  autoComplete="new-password"
                  placeholder="Min. 8 characters"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={pending}
                  required
                  className="h-10 pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowPw((s) => !s)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
                >
                  {showPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="confirm">Confirm Password</Label>
              <Input
                id="confirm"
                type="password"
                autoComplete="new-password"
                placeholder="Repeat password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                disabled={pending}
                required
                className="h-10"
              />
            </div>

            <Button type="submit" className="h-10 w-full" disabled={pending}>
              {pending ? <><Loader2 className="h-4 w-4 animate-spin" />&nbsp;Saving...</> : 'Reset Password'}
            </Button>

            <p className="text-center text-xs text-muted-foreground">
              <Link to="/login" className="text-accent hover:underline">
                Back to login
              </Link>
            </p>
          </form>
        </div>
      </div>

      <div className="relative hidden w-[45%] flex-col items-center justify-center overflow-hidden border-l border-border sm:flex"
        style={{ background: 'var(--el-1)' }}>
        <div className="pointer-events-none absolute inset-0"
          style={{ background: 'radial-gradient(ellipse 80% 70% at 50% 50%, hsl(194 44% 68% / 0.08) 0%, transparent 70%)' }} />
        <div className="relative z-10 flex flex-col items-center gap-6 p-12 text-center">
          <div className="flex h-24 w-24 items-center justify-center rounded-2xl border border-accent/20 bg-accent/5"
            style={{ boxShadow: '0 0 60px hsl(194 44% 68% / 0.12)' }}>
            <ServerCog className="h-12 w-12 animate-pulse-glow text-accent" />
          </div>
          <div className="space-y-2">
            <h2 className="font-display text-xl font-bold uppercase tracking-widest text-foreground/90">
              Maunting Server Panel
            </h2>
            <p className="max-w-xs text-sm leading-relaxed text-muted-foreground">
              Keep your account secure. Use a strong, unique password.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
