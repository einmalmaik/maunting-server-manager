import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ServerCog, CheckCircle, AlertTriangle, Loader2 } from 'lucide-react'
import toast from 'react-hot-toast'
import { authApi } from '@/lib/api'
import { Button } from '@/components/ui/button'

export default function VerifyEmailPage() {
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')
  const [status, setStatus] = useState<'loading' | 'success' | 'error'>('loading')
  const [message, setMessage] = useState('')

  useEffect(() => {
    if (!token) {
      setStatus('error')
      setMessage('Verification token is missing.')
      return
    }
    authApi.verifyEmail(token)
      .then((res) => {
        setStatus('success')
        setMessage(res.message)
        toast.success(res.message)
      })
      .catch((err: any) => {
        setStatus('error')
        setMessage(err?.message || 'Verification failed.')
        toast.error(err?.message || 'Verification failed.')
      })
  }, [token])

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      <div className="flex w-full flex-col items-center justify-center px-8 sm:w-[55%]">
        <div className="w-full max-w-sm space-y-6 text-center">
          <div className="flex flex-col items-center gap-3">
            <div className="flex h-14 w-14 items-center justify-center rounded-xl border border-accent/30 bg-accent/10 shadow-lg"
              style={{ boxShadow: '0 0 32px hsl(194 44% 68% / 0.15)' }}>
              <ServerCog className="h-7 w-7 text-accent" />
            </div>
            <h1 className="font-display text-2xl font-bold uppercase tracking-widest text-foreground">
              Email Verification
            </h1>
          </div>

          {status === 'loading' && (
            <div className="flex flex-col items-center gap-3">
              <Loader2 className="h-8 w-8 animate-spin text-accent" />
              <p className="text-sm text-muted-foreground">Verifying your email address...</p>
            </div>
          )}

          {status === 'success' && (
            <div className="space-y-4 rounded-xl border border-accent/20 bg-accent/5 p-6">
              <CheckCircle className="mx-auto h-10 w-10 text-accent" />
              <p className="text-sm text-foreground">{message}</p>
              <Link to="/login">
                <Button className="w-full mt-2">Go to Login</Button>
              </Link>
            </div>
          )}

          {status === 'error' && (
            <div className="space-y-4 rounded-xl border border-destructive/20 bg-destructive/5 p-6">
              <AlertTriangle className="mx-auto h-10 w-10 text-destructive" />
              <p className="text-sm text-foreground">{message}</p>
              <div className="flex gap-2">
                <Link to="/login" className="flex-1">
                  <Button variant="outline" className="w-full">Back to Login</Button>
                </Link>
                <Link to="/register" className="flex-1">
                  <Button className="w-full">Register</Button>
                </Link>
              </div>
            </div>
          )}
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
              Secure authentication with email verification.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
