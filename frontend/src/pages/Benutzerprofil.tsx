import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, CalendarDays, Clock, EyeOff, MessageSquare, Phone, UserPlus, Video } from 'lucide-react'

import { getFriends, getProfile, sendFriendRequest, type PublicProfileResponse } from '@/api/social'
import { SanitizedApiError } from '@/api/client'
import { DeviceBadge } from '@/components/social/DeviceBadge'
import { FunkenAbzeichen } from '@/components/social/FunkenBadge'
import { Meilensteine } from '@/components/social/Meilensteine'
import { Nutzungszeit } from '@/components/social/Nutzungszeit'
import { StatusDot } from '@/components/social/StatusIndicator'
import { Spinner } from '@/components/ui/Spinner'
import { Avatar, Button, Kurzinfo } from '@/Singra/UI'
import { useAuthStore } from '@/stores/authStore'
import { useCallStore } from '@/stores/useCallStore'
import { useFunkenStore } from '@/stores/funkenStore'
import { toast } from '@/stores/toastStore'

type Stand =
  | { art: 'laedt' }
  | { art: 'da'; profil: PublicProfileResponse }
  | { art: 'verborgen' }
  | { art: 'fehler' }

/**
 * Das Profil eines anderen Kontos: Name, Bild, seit wann dabei, Status,
 * Funken, Nutzungszeit und Errungenschaften — dazu Chat und Anruf.
 *
 * Was hier steht, entscheidet der Server nach der Profil-Sichtbarkeit des
 * Kontos. Darf man es nicht sehen (privat, nur Freunde, blockiert), antwortet
 * er wie für ein Konto, das es nicht gibt, und die Seite sagt auch nicht mehr.
 */
export function Benutzerprofil() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { userId } = useParams()
  const ich = useAuthStore((s) => s.user?.id)
  const [stand, setStand] = useState<Stand>({ art: 'laedt' })
  const [anfrageGesendet, setAnfrageGesendet] = useState(false)

  const id = Number(userId)
  const gueltig = Number.isSafeInteger(id) && id > 0

  const lade = useCallback(async () => {
    if (!gueltig) {
      setStand({ art: 'verborgen' })
      return
    }
    setStand({ art: 'laedt' })
    try {
      setStand({ art: 'da', profil: await getProfile(id) })
    } catch (err) {
      setStand(err instanceof SanitizedApiError && err.status === 404 ? { art: 'verborgen' } : { art: 'fehler' })
    }
  }, [gueltig, id])

  useEffect(() => {
    setAnfrageGesendet(false)
    void lade()
  }, [lade])

  // Der Funke braucht den Beginn der Freundschaft. Wer direkt über einen Link
  // kommt, war vielleicht weder im Messenger noch in der Freundesliste.
  const istFreund = stand.art === 'da' && Boolean(stand.profil.is_friend)
  useEffect(() => {
    if (!istFreund || useFunkenStore.getState().freunde[id]) return
    void getFriends()
      .then((liste) =>
        useFunkenStore.getState().setzeFreunde(
          liste
            .filter((f) => f.status === 'accepted')
            .map((f) => ({ userId: Number(f.user_id ?? f.id), seit: f.created_at })),
        ),
      )
      .catch(() => {})
  }, [istFreund, id])

  if (stand.art === 'laedt') {
    return (
      <div className="flex justify-center py-16">
        <Spinner size="md" />
      </div>
    )
  }

  if (stand.art !== 'da') {
    const verborgen = stand.art === 'verborgen'
    return (
      <div className="mx-auto max-w-md py-16 text-center space-y-4">
        <EyeOff className="mx-auto h-10 w-10 text-on-surface-variant/60" aria-hidden="true" />
        <h1 className="font-headline text-title-lg font-semibold text-on-surface">
          {verborgen ? t('social.profile.unavailableTitle') : t('social.profile.loadFailed')}
        </h1>
        {verborgen && <p className="text-sm text-on-surface-variant">{t('social.profile.unavailableHint')}</p>}
        <div className="flex justify-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => navigate(-1)} className="gap-1.5">
            <ArrowLeft className="h-4 w-4" />
            <span>{t('common.back')}</span>
          </Button>
          {!verborgen && (
            <Button variant="secondary" size="sm" onClick={() => void lade()}>
              {t('common.retry')}
            </Button>
          )}
        </div>
      </div>
    )
  }

  const { profil } = stand
  const selbst = profil.user_id === ich
  const status = profil.presence?.status === 'invisible' ? 'offline' : profil.presence?.status
  // Nur ein Tag, ohne Zeitzone: als Ortszeit lesen, sonst rutscht er westlich
  // von Greenwich auf den Vortag.
  const dabeiSeit = profil.member_since
    ? new Date(`${profil.member_since}T00:00:00`).toLocaleDateString(i18n.language, { month: 'long', year: 'numeric' })
    : null
  const stats = profil.stats
  const erfolge = profil.achievements ?? []

  const anrufen = async (art: 'audio' | 'video') => {
    try {
      await useCallStore.getState().initiateCall(
        { userId: profil.user_id, username: profil.username, avatarUrl: profil.avatar_url },
        art,
      )
    } catch (err: unknown) {
      toast.error(err instanceof Error && err.message ? err.message : t('messenger.callStartFailedSingle'))
    }
  }

  const freundHinzufuegen = async () => {
    try {
      const res = await sendFriendRequest(profil.username)
      toast.success(res.message || t('social.contacts.requestSent'))
      setAnfrageGesendet(true)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : t('social.contacts.requestSendFailed'))
    }
  }

  return (
    <div className="mx-auto w-full max-w-4xl space-y-6 pb-8">
      <Button variant="ghost" size="sm" onClick={() => navigate(-1)} className="gap-1.5 -ml-2">
        <ArrowLeft className="h-4 w-4" />
        <span>{t('common.back')}</span>
      </Button>

      <section className="msm-card p-6" aria-labelledby="profil-name">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-4 min-w-0">
            <div className="relative shrink-0">
              <Avatar src={profil.avatar_url} name={profil.username} size="2xl" />
              {status && <StatusDot status={status} size="lg" className="absolute bottom-1 right-1" />}
            </div>
            <div className="min-w-0 space-y-1">
              <div className="flex items-center gap-2 min-w-0">
                <h1 id="profil-name" className="font-headline text-2xl font-bold text-on-surface truncate">
                  {profil.username}
                </h1>
                {istFreund && <FunkenAbzeichen partnerId={profil.user_id} name={profil.username} />}
              </div>
              {profil.presence && (
                <p className="flex items-center gap-1.5 text-sm text-on-surface-variant">
                  <span>{t(`social.status.${status}`)}</span>
                  {status !== 'offline' && <DeviceBadge deviceType={profil.presence.device_type} />}
                  {profil.presence.activity_label && (
                    <span className="truncate">· {profil.presence.activity_label}</span>
                  )}
                </p>
              )}
              {profil.presence?.custom_status && (
                <p className="text-sm text-on-surface truncate">{profil.presence.custom_status}</p>
              )}
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-on-surface-variant">
                {dabeiSeit && (
                  <span className="inline-flex items-center gap-1">
                    <CalendarDays className="h-3.5 w-3.5" aria-hidden="true" />
                    {t('social.profile.memberSince', { date: dabeiSeit })}
                  </span>
                )}
                {istFreund && !selbst && <span className="text-primary">{t('social.profile.friend')}</span>}
              </div>
            </div>
          </div>

          {!selbst && (
            <div className="flex flex-wrap gap-2 sm:justify-end">
              <Button size="sm" onClick={() => navigate(`/chat?userId=${profil.user_id}`)} className="gap-1.5">
                <MessageSquare className="h-4 w-4" />
                <span>{t('social.profile.chat')}</span>
              </Button>
              {/* Anrufen dürfen nur Freunde; der Server lehnt alles andere ab. */}
              {istFreund ? (
                <>
                  <Button variant="secondary" size="sm" onClick={() => void anrufen('audio')} className="gap-1.5">
                    <Phone className="h-4 w-4" />
                    <span>{t('social.profile.call')}</span>
                  </Button>
                  <Kurzinfo text={t('messenger.videoCall')} seite="ende">
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => void anrufen('video')}
                      aria-label={t('messenger.videoCall')}
                    >
                      <Video className="h-4 w-4" />
                    </Button>
                  </Kurzinfo>
                </>
              ) : (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => void freundHinzufuegen()}
                  disabled={anfrageGesendet}
                  className="gap-1.5"
                >
                  <UserPlus className="h-4 w-4" />
                  <span>{anfrageGesendet ? t('social.profile.requestSent') : t('messenger.sendFriendRequest')}</span>
                </Button>
              )}
            </div>
          )}
        </div>
      </section>

      <section className="msm-card p-6" aria-labelledby="profil-zeit">
        <div className="flex items-center gap-2 mb-4">
          <Clock className="h-5 w-5 text-secondary" aria-hidden="true" />
          <h2 id="profil-zeit" className="font-headline text-title-lg font-semibold text-on-surface">
            {t('social.profile.activityTitle')}
          </h2>
        </div>
        <Nutzungszeit stats={stats} />
      </section>

      <Meilensteine
        key={profil.user_id}
        achievements={erfolge}
        freigeschaltet={stats?.unlocked_achievements ?? erfolge.filter((a) => a.unlocked).length}
        gesamt={stats?.total_achievements ?? erfolge.length}
        punkte={stats?.earned_points ?? 0}
        titel={t('social.profile.achievementsTitle')}
        startFilter={erfolge.some((a) => a.unlocked) ? 'unlocked' : 'all'}
      />
    </div>
  )
}
