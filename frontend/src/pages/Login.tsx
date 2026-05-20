import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { useAuthStore } from '@/stores/authStore'
import { Input } from '@/components/ui/Input'
import { Button } from '@/components/ui/Button'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { Shield } from 'lucide-react'

export function Login() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { setToken, setUser } = useAuthStore()
  const [error, setError] = useState('')
  const [form, setForm] = useState({ username: '', password: '', otp: '' })
  const [requires2FA, setRequires2FA] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    try {
      const res = await api<{ access_token: string; requires_2fa: boolean }>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({
          username: form.username,
          password: form.password,
          otp_code: form.otp || null,
        }),
      })

      if (res.requires_2fa) {
        setRequires2FA(true)
        return
      }

      setToken(res.access_token)
      const user = await api('/auth/me')
      setUser(user)
      navigate('/')
    } catch (err: any) {
      setError(err.message || t('auth.loginFailed'))
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <div className="w-full max-w-md">
        <div className="flex justify-center mb-8">
          <div className="w-12 h-12 rounded-xl bg-primary/20 flex items-center justify-center shadow-accent-cta">
            <Shield className="w-7 h-7 text-primary" />
          </div>
        </div>

        <Card>
          <CardHeader className="text-center">
            <CardTitle>{t('auth.login')}</CardTitle>
            <CardDescription>{t('auth.loginDescription')}</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <Input
                label={t('auth.username')}
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
                required
                disabled={requires2FA}
              />
              <Input
                label={t('auth.password')}
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                required
                disabled={requires2FA}
              />
              {requires2FA && (
                <Input
                  label={t('auth.otpCode')}
                  value={form.otp}
                  onChange={(e) => setForm({ ...form, otp: e.target.value })}
                  required
                  pattern="\d{6}"
                  maxLength={6}
                />
              )}
              {error && <p className="text-sm text-destructive">{error}</p>}
              <Button type="submit" className="w-full" size="lg">
                {requires2FA ? t('auth.verify2FA') : t('auth.signIn')}
              </Button>
            </form>

            <div className="mt-4 flex justify-between text-sm">
              <Link to="/register" className="text-primary hover:underline">
                {t('auth.noAccount')}
              </Link>
              <Link to="/forgot-password" className="text-muted-foreground hover:text-foreground">
                {t('auth.forgotPassword')}
              </Link>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
