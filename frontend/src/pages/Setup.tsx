import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api } from '@/api/client'
import { Input } from '@/components/ui/Input'
import { Button } from '@/components/ui/Button'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/Card'
import { Shield } from 'lucide-react'

export function Setup() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [form, setForm] = useState({ username: '', email: '', password: '', confirm: '' })

  useEffect(() => {
    api<{ setup_required: boolean }>('/auth/setup-status')
      .then((res) => {
        if (!res.setup_required) navigate('/login', { replace: true })
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [navigate])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (form.password !== form.confirm) {
      setError(t('setup.passwordMismatch'))
      return
    }
    if (form.password.length < 8) {
      setError(t('setup.passwordTooShort'))
      return
    }

    try {
      await api('/auth/setup', {
        method: 'POST',
        body: JSON.stringify({
          username: form.username,
          email: form.email,
          password: form.password,
        }),
      })
      navigate('/login', { replace: true })
    } catch (err: any) {
      setError(err.message || t('setup.error'))
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
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
            <CardTitle>{t('setup.title')}</CardTitle>
            <CardDescription>{t('setup.description')}</CardDescription>
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
                {t('setup.createOwner')}
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
