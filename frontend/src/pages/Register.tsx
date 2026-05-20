import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { Input } from '@/components/ui/Input'
import { Button } from '@/components/ui/Button'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { Shield } from 'lucide-react'

export function Register() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)
  const [form, setForm] = useState({ username: '', email: '', password: '', confirm: '' })

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (form.password !== form.confirm) {
      setError(t('auth.passwordMismatch'))
      return
    }
    if (form.password.length < 8) {
      setError(t('auth.passwordTooShort'))
      return
    }

    try {
      await api('/auth/register', {
        method: 'POST',
        body: JSON.stringify({
          username: form.username,
          email: form.email,
          password: form.password,
        }),
      })
      setSuccess(true)
    } catch (err: any) {
      setError(err.message || t('auth.registerFailed'))
    }
  }

  if (success) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background p-4">
        <div className="w-full max-w-md">
          <Card>
            <CardHeader className="text-center">
              <CardTitle>{t('auth.registerSuccess')}</CardTitle>
              <CardDescription>{t('auth.verifyEmailHint')}</CardDescription>
            </CardHeader>
            <CardContent>
              <Link to="/login">
                <Button className="w-full">{t('auth.goToLogin')}</Button>
              </Link>
            </CardContent>
          </Card>
        </div>
      </div>
    )
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
            <CardTitle>{t('auth.register')}</CardTitle>
            <CardDescription>{t('auth.registerDescription')}</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-4">
              <Input
                label={t('auth.username')}
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
                required
                minLength={3}
              />
              <Input
                label={t('auth.email')}
                type="email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                required
              />
              <Input
                label={t('auth.password')}
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
                required
                minLength={8}
              />
              <Input
                label={t('auth.confirmPassword')}
                type="password"
                value={form.confirm}
                onChange={(e) => setForm({ ...form, confirm: e.target.value })}
                required
              />
              {error && <p className="text-sm text-destructive">{error}</p>}
              <Button type="submit" className="w-full" size="lg">
                {t('auth.createAccount')}
              </Button>
            </form>

            <div className="mt-4 text-center text-sm">
              <Link to="/login" className="text-primary hover:underline">
                {t('auth.hasAccount')}
              </Link>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
