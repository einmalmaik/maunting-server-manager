import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@/i18n'

// Die Sprache festlegen: die Behauptungen unten prüfen deutsche Texte, und
// ohne diese Zeile entscheidet navigator.language der Testumgebung.
beforeAll(async () => {
  await i18n.changeLanguage('de')
})

const getGroupInviteInfo = vi.fn()
vi.mock('@/api/social', () => ({
  getGroupInviteInfo: (code: string) => getGroupInviteInfo(code),
}))
vi.mock('@/config/api', () => ({ apiUrl: (pfad: string) => `https://panel.test${pfad}` }))

const { GruppenEinladungsKarte, findeEinladungsCode } = await import('./GruppenEinladungsKarte')

const ORIGIN = 'https://panel.test'

const INFO = {
  group_id: 7,
  name: 'Serverteam',
  description: null,
  avatar_url: '/api/social/groups/avatar/group_7_abc.png',
  member_count: 4,
  live_call: false,
  live_participants: 0,
}

beforeEach(() => {
  getGroupInviteInfo.mockReset()
  getGroupInviteInfo.mockResolvedValue(INFO)
})

describe('findeEinladungsCode', () => {
  it('erkennt einen Einladungslink im Fliesstext', () => {
    const text = `Komm rein: ${ORIGIN}/chat/join/AbCd1234efGH bis später`
    expect(findeEinladungsCode(text, ORIGIN)).toBe('AbCd1234efGH')
  })

  it('ignoriert Links auf ein fremdes Panel', () => {
    // Sonst müsste die Karte Daten von dort nachladen.
    const text = `https://boeses-panel.example/chat/join/AbCd1234efGH`
    expect(findeEinladungsCode(text, ORIGIN)).toBeNull()
  })

  it('ignoriert Text ohne Einladung', () => {
    expect(findeEinladungsCode('nur eine normale Nachricht', ORIGIN)).toBeNull()
    expect(findeEinladungsCode('', ORIGIN)).toBeNull()
  })

  it('greift nicht bei zu kurzen Codes', () => {
    expect(findeEinladungsCode(`${ORIGIN}/chat/join/kurz`, ORIGIN)).toBeNull()
  })
})

describe('GruppenEinladungsKarte', () => {
  it('zeigt Logo, Name und Mitgliederzahl', async () => {
    render(<GruppenEinladungsKarte inviteCode="AbCd1234efGH" onJoin={vi.fn()} />)

    expect(await screen.findByText('Serverteam')).toBeInTheDocument()
    expect(screen.getByText(i18n.t('social.invite.memberCount', { count: 4 }))).toBeInTheDocument()
    // Das Logo trägt bewusst ein leeres alt: der Gruppenname steht daneben,
    // ein Vorlesen der Grafik wäre eine Dopplung. Deshalb hier über das Tag.
    expect(document.querySelector('img')).toHaveAttribute(
      'src',
      'https://panel.test/api/social/groups/avatar/group_7_abc.png',
    )
    expect(getGroupInviteInfo).toHaveBeenCalledWith('AbCd1234efGH')
  })

  it('setzt die Einzahl bei genau einem Mitglied', async () => {
    getGroupInviteInfo.mockResolvedValue({ ...INFO, member_count: 1 })
    render(<GruppenEinladungsKarte inviteCode="AbCd1234efGH" onJoin={vi.fn()} />)

    expect(await screen.findByText(i18n.t('social.invite.memberCount', { count: 1 }))).toBeInTheDocument()
  })

  it('zeigt einen laufenden Anruf mit Teilnehmerzahl an', async () => {
    getGroupInviteInfo.mockResolvedValue({ ...INFO, live_call: true, live_participants: 3 })
    render(<GruppenEinladungsKarte inviteCode="AbCd1234efGH" onJoin={vi.fn()} />)

    expect(await screen.findByText(/Live/)).toBeInTheDocument()
    expect(screen.getByText(/· 3/)).toBeInTheDocument()
  })

  it('zeigt kein Live-Abzeichen, wenn niemand telefoniert', async () => {
    render(<GruppenEinladungsKarte inviteCode="AbCd1234efGH" onJoin={vi.fn()} />)

    await screen.findByText('Serverteam')
    expect(screen.queryByText(/Live/)).not.toBeInTheDocument()
  })

  it('tritt auf Klick bei', async () => {
    const onJoin = vi.fn().mockResolvedValue(undefined)
    render(<GruppenEinladungsKarte inviteCode="AbCd1234efGH" onJoin={onJoin} />)

    await screen.findByText('Serverteam')
    fireEvent.click(screen.getByRole('button', { name: i18n.t('social.invite.join') }))

    await waitFor(() => expect(onJoin).toHaveBeenCalledWith('AbCd1234efGH'))
  })

  it('sagt es, wenn die Einladung nicht mehr gilt', async () => {
    getGroupInviteInfo.mockRejectedValue(new Error('404'))
    render(<GruppenEinladungsKarte inviteCode="AbCd1234efGH" onJoin={vi.fn()} />)

    expect(await screen.findByText(/gilt nicht mehr/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('social.invite.join') })).not.toBeInTheDocument()
  })

  it('fällt bei fehlendem Logo auf ein Symbol zurück', async () => {
    getGroupInviteInfo.mockResolvedValue({ ...INFO, avatar_url: null })
    render(<GruppenEinladungsKarte inviteCode="AbCd1234efGH" onJoin={vi.fn()} />)

    await screen.findByText('Serverteam')
    expect(document.querySelector('img')).toBeNull()
  })
})
