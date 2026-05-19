import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Shield, KeyRound, Loader2, QrCode, Eye, EyeOff, CheckCircle2, Download } from 'lucide-react'
import toast from 'react-hot-toast'
import { QRCodeSVG } from 'qrcode.react'
import { accountApi, ApiError } from '@/lib/api'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { useUiLanguage } from '@/lib/ui-language'

function downloadBackupCodesFile(codes: string[]) {
  const content = [
    'Conan Exiles Panel 2FA Backup Codes',
    '',
    'Store these codes somewhere safe.',
    'Each code can be used once for login only.',
    '',
    ...codes,
    '',
  ].join('\n')
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = 'conan-panel-backup-codes.txt'
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function ChangePasswordCard() {
  const { copy } = useUiLanguage()
  const t = copy.accountPage
  const [currentPw, setCurrentPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [showCurrent, setShowCurrent] = useState(false)
  const [showNew, setShowNew] = useState(false)

  const mutation = useMutation({
    mutationFn: () => accountApi.changePassword(currentPw, newPw),
    onSuccess: () => {
      toast.success(t.passwordChanged)
      setCurrentPw('')
      setNewPw('')
      setConfirmPw('')
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : t.passwordChangeFailed)
    },
  })

  const canSubmit = currentPw && newPw.length >= 8 && newPw === confirmPw && !mutation.isPending

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <KeyRound className="h-4 w-4 text-accent" />
          {t.changePasswordTitle}
        </CardTitle>
        <CardDescription>{t.changePasswordDescription}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-1">
          <Label htmlFor="cp-current">{t.currentPassword}</Label>
          <div className="relative">
            <Input id="cp-current" type={showCurrent ? 'text' : 'password'} value={currentPw} onChange={(e) => setCurrentPw(e.target.value)} className="pr-9" />
            <button type="button" onClick={() => setShowCurrent((v) => !v)} className="absolute right-2.5 top-2.5 text-muted-foreground hover:text-foreground">
              {showCurrent ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
        </div>
        <div className="space-y-1">
          <Label htmlFor="cp-new">{t.newPassword}</Label>
          <div className="relative">
            <Input id="cp-new" type={showNew ? 'text' : 'password'} value={newPw} onChange={(e) => setNewPw(e.target.value)} className="pr-9" />
            <button type="button" onClick={() => setShowNew((v) => !v)} className="absolute right-2.5 top-2.5 text-muted-foreground hover:text-foreground">
              {showNew ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
          {newPw && newPw.length < 8 && <p className="text-xs text-destructive">{t.minPassword}</p>}
        </div>
        <div className="space-y-1">
          <Label htmlFor="cp-confirm">{t.confirmNewPassword}</Label>
          <Input id="cp-confirm" type="password" value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} />
          {confirmPw && newPw !== confirmPw && <p className="text-xs text-destructive">{t.passwordMismatch}</p>}
        </div>
        <Button disabled={!canSubmit} onClick={() => mutation.mutate()} className="w-full">
          {mutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {t.updatePassword}
        </Button>
      </CardContent>
    </Card>
  )
}

function Setup2FADialog({ onClose }: { onClose: () => void }) {
  const { copy } = useUiLanguage()
  const t = copy.accountPage
  const queryClient = useQueryClient()
  const [step, setStep] = useState<'qr' | 'verify'>('qr')
  const [code, setCode] = useState('')
  const [showSecret, setShowSecret] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['2fa-setup'],
    queryFn: accountApi.setup2fa,
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })

  const enableMutation = useMutation({
    mutationFn: () => accountApi.enable2fa(data?.secret ?? '', code),
    onSuccess: async () => {
      toast.success(t.twoFactorEnabled)
      await queryClient.invalidateQueries({ queryKey: ['account-me'] })
      onClose()
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : t.twoFactorEnableFailed)
    },
  })

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{t.setup2faTitle}</DialogTitle>
          <DialogDescription>{t.setup2faDescription}</DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <div className="flex justify-center py-8"><Loader2 className="h-6 w-6 animate-spin" /></div>
        ) : data ? (
          <div className="space-y-4">
            {step === 'qr' && (
              <>
                <div className="flex justify-center rounded-lg bg-white p-4">
                  <QRCodeSVG value={data.uri} size={180} />
                </div>
                <div className="space-y-1 text-center">
                  <p className="text-xs text-muted-foreground">{t.cannotScan}</p>
                  <button type="button" onClick={() => setShowSecret((v) => !v)} className="mx-auto flex items-center gap-1.5 rounded bg-muted px-2 py-1 font-mono text-xs hover:bg-muted/80">
                    {showSecret ? data.secret : '*'.repeat(data.secret.length)}
                    {showSecret ? <EyeOff className="h-3 w-3" /> : <Eye className="h-3 w-3" />}
                  </button>
                </div>
                <Button className="w-full" onClick={() => setStep('verify')}>
                  {t.scannedCode}
                </Button>
              </>
            )}
            {step === 'verify' && (
              <>
                <div className="space-y-1">
                  <Label htmlFor="2fa-code">{t.verificationCode}</Label>
                  <Input
                    id="2fa-code"
                    value={code}
                    onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                    placeholder="123456"
                    className="text-center font-mono text-lg tracking-widest"
                    maxLength={6}
                    autoFocus
                  />
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" onClick={() => setStep('qr')} className="flex-1">{t.back}</Button>
                  <Button className="flex-1" disabled={code.length !== 6 || enableMutation.isPending} onClick={() => enableMutation.mutate()}>
                    {enableMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                    {t.confirm}
                  </Button>
                </div>
              </>
            )}
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

function Disable2FADialog({ onClose }: { onClose: () => void }) {
  const { copy } = useUiLanguage()
  const t = copy.accountPage
  const queryClient = useQueryClient()
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')

  const mutation = useMutation({
    mutationFn: () => accountApi.disable2fa(password, code),
    onSuccess: async () => {
      toast.success(t.twoFactorDisabled)
      await queryClient.invalidateQueries({ queryKey: ['account-me'] })
      onClose()
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : t.twoFactorDisableFailed)
    },
  })

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t.disable2faTitle}</DialogTitle>
          <DialogDescription>{t.disable2faDescription}</DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="d2fa-pw">{t.currentPassword}</Label>
          <Input id="d2fa-pw" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <div className="space-y-2">
          <Label htmlFor="d2fa-code">{t.authenticatorCode}</Label>
          <Input id="d2fa-code" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))} placeholder="123456" maxLength={6} />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{copy.files.cancel}</Button>
          <Button variant="destructive" disabled={!password || code.length !== 6 || mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {t.disable2fa}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function TwoFACard() {
  const { copy } = useUiLanguage()
  const t = copy.accountPage
  const queryClient = useQueryClient()
  const [setup2faOpen, setSetup2faOpen] = useState(false)
  const [disable2faOpen, setDisable2faOpen] = useState(false)

  const { data } = useQuery({
    queryKey: ['account-me'],
    queryFn: accountApi.me,
    staleTime: 60_000,
  })

  const downloadMutation = useMutation({
    mutationFn: accountApi.downloadBackupCodes,
    onSuccess: async (response) => {
      downloadBackupCodesFile(response.codes)
      toast.success(t.downloadSuccess)
      await queryClient.invalidateQueries({ queryKey: ['account-me'] })
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : t.downloadFailed)
    },
  })

  const enabled = data?.totp_enabled ?? false
  const canDownloadBackupCodes = data?.can_download_backup_codes ?? false
  const backupCodesRemaining = data?.backup_codes_remaining ?? 0

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Shield className="h-4 w-4 text-accent" />
            {t.twoFactorTitle}
            {enabled && (
              <Badge className="ml-2 gap-1 border-emerald-500/40 bg-emerald-500/20 text-emerald-400">
                <CheckCircle2 className="h-3 w-3" />
                {t.enabled}
              </Badge>
            )}
          </CardTitle>
          <CardDescription>{t.twoFactorDescription}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {enabled ? (
            <>
              <div className="flex items-center justify-between">
                <p className="text-sm text-muted-foreground">{t.activeOnAccount}</p>
                <Button variant="outline" size="sm" onClick={() => setDisable2faOpen(true)}>
                  {t.disable2fa}
                </Button>
              </div>
              <div className="rounded-lg border border-border/60 bg-background/40 p-3 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="font-medium text-foreground">{t.backupCodes}</p>
                    <p className="text-xs text-muted-foreground">
                      {canDownloadBackupCodes
                        ? t.backupCodesDownload
                        : t.backupCodesRemaining(backupCodesRemaining)}
                    </p>
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    className="gap-2"
                    disabled={!canDownloadBackupCodes || downloadMutation.isPending}
                    onClick={() => downloadMutation.mutate()}
                  >
                    {downloadMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                    {t.downloadOnce}
                  </Button>
                </div>
              </div>
            </>
          ) : (
            <Button onClick={() => setSetup2faOpen(true)} className="gap-2">
              <QrCode className="h-4 w-4" />
              {t.enable2fa}
            </Button>
          )}
        </CardContent>
      </Card>

      {setup2faOpen && <Setup2FADialog onClose={() => setSetup2faOpen(false)} />}
      {disable2faOpen && <Disable2FADialog onClose={() => setDisable2faOpen(false)} />}
    </>
  )
}

export default function AccountPage() {
  const { copy } = useUiLanguage()
  const t = copy.accountPage
  const { data } = useQuery({
    queryKey: ['account-me'],
    queryFn: accountApi.me,
    staleTime: 60_000,
  })

  return (
    <div className="max-w-lg space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{t.title}</h1>
        {data && (
          <p className="mt-1 text-sm text-muted-foreground">
            {t.loggedInAs} <span className="font-medium text-foreground">{data.username}</span>
            {' · '}
            <span className="capitalize">{data.role}</span>
          </p>
        )}
      </div>

      <ChangePasswordCard />
      <TwoFACard />
    </div>
  )
}
