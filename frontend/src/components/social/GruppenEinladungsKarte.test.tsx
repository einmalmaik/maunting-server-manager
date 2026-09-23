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

const { GruppenEinladungsKarte, findeEinladung, findeEinladungsCode } = await import(
  './GruppenEinladungsKarte'
)
const { baueEinladungsKarte, einladungsschluesselAus } = await import(
  '@/services/einladungsKarte'
)

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

  it('nimmt den Schluessel hinter der Raute mit', () => {
    // Er steht im Nachrichtentext, nicht in location.hash: die Karte wird aus
    // einer Chatnachricht gezeichnet, und dort steht der ganze Link.
    const k = 'a'.repeat(64)
    const text = `Komm rein: ${ORIGIN}/chat/join/AbCd1234efGH#k=${k}`
    expect(findeEinladung(text, ORIGIN)).toEqual({ code: 'AbCd1234efGH', schluessel: k })
  })

  it('kommt ohne Schluessel aus', () => {
    expect(findeEinladung(`${ORIGIN}/chat/join/AbCd1234efGH`, ORIGIN)).toEqual({
      code: 'AbCd1234efGH',
      schluessel: null,
    })
  })

  it('ignoriert einen krummen Schluessel, behaelt aber den Code', () => {
    // Sonst fiele die ganze Einladung weg, weil jemand den Link beim Kopieren
    // abgeschnitten hat.
    expect(findeEinladung(`${ORIGIN}/chat/join/AbCd1234efGH#k=zukurz`, ORIGIN)).toEqual({
      code: 'AbCd1234efGH',
      schluessel: null,
    })
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

    // Ohne Schlüssel gibt es keinen Karteninhalt weiterzureichen.
    await waitFor(() => expect(onJoin).toHaveBeenCalledWith('AbCd1234efGH', null))
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

describe('GruppenEinladungsKarte, verschlüsselt', () => {
  const GEHEIMNIS = 'A'.repeat(43) + '='
  const CODE = 'AbCd1234efGH'
  const LOGO = 'data:image/webp;base64,UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4HAA=='

  /** Der Server, wie er antwortet, sobald eine Gruppe eine Karte hat. */
  async function mitKarte(inhalt: Record<string, unknown> = {}) {
    return {
      ...INFO,
      // Klartext ist dann weg — beides nebeneinander wäre Verschlüsselung als
      // Zierde.
      name: null,
      description: null,
      avatar_url: null,
      invite_card: await baueEinladungsKarte(GEHEIMNIS, CODE, {
        name: 'Serverteam',
        beschreibung: 'Wir bauen Dinge',
        ...inhalt,
      }),
    }
  }

  it('zeigt Name und Logo aus der entschlüsselten Karte', async () => {
    getGroupInviteInfo.mockResolvedValue(await mitKarte({ logo: LOGO }))
    const schluessel = await einladungsschluesselAus(GEHEIMNIS)

    render(
      <GruppenEinladungsKarte inviteCode={CODE} schluessel={schluessel} onJoin={vi.fn()} />,
    )

    expect(await screen.findByText('Serverteam')).toBeInTheDocument()
    // Als Data-URL, nicht als Adresse: eine Adresse müsste der Server
    // ausliefern und wüsste dabei, wer die Einladung gerade ansieht.
    await waitFor(() => expect(document.querySelector('img')).toHaveAttribute('src', LOGO))
  })

  it('bleibt ohne Schlüssel zu und sagt das', async () => {
    /*
     * Der Link wurde ohne die Raute weitergereicht — oder jemand ruft den
     * Endpunkt direkt auf. Beitreten geht trotzdem: der Code allein reicht
     * dafür, und das ist seit jeher so. Nur die Vorschau fehlt.
     */
    getGroupInviteInfo.mockResolvedValue(await mitKarte())

    render(<GruppenEinladungsKarte inviteCode={CODE} onJoin={vi.fn()} />)

    expect(await screen.findByText(i18n.t('social.invite.sealed'))).toBeInTheDocument()
    expect(screen.queryByText('Serverteam')).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: i18n.t('social.invite.join') }),
    ).toBeInTheDocument()
  })

  it('bleibt mit dem falschen Schlüssel zu', async () => {
    getGroupInviteInfo.mockResolvedValue(await mitKarte())
    const fremd = await einladungsschluesselAus('B'.repeat(43) + '=')

    render(<GruppenEinladungsKarte inviteCode={CODE} schluessel={fremd} onJoin={vi.fn()} />)

    expect(await screen.findByText(i18n.t('social.invite.sealed'))).toBeInTheDocument()
  })

  it('zieht die Karte dem Klartext vor', async () => {
    /*
     * Der Vorrang ist nicht beliebig. Läge der Klartext vorn, zeigte die Karte
     * bis Stufe 6 weiter den Serverstand — und niemandem fiele auf, dass die
     * Verschlüsselung nichts bewirkt.
     */
    getGroupInviteInfo.mockResolvedValue({
      ...(await mitKarte()),
      name: 'Name vom Server',
      avatar_url: '/api/social/groups/avatar/group_7_abc.png',
    })
    const schluessel = await einladungsschluesselAus(GEHEIMNIS)

    render(
      <GruppenEinladungsKarte inviteCode={CODE} schluessel={schluessel} onJoin={vi.fn()} />,
    )

    expect(await screen.findByText('Serverteam')).toBeInTheDocument()
    expect(screen.queryByText('Name vom Server')).not.toBeInTheDocument()
  })

  it('nimmt weiter den Klartext, solange es keine Karte gibt', async () => {
    // Der Altweg. Er hält Gruppen am Leben, die noch nie einen Link geteilt
    // haben. Seit Stufe 6 kommt dieser Klartext nur noch von einem Panel, das
    // die Räumung noch nicht mitgemacht hat — das eigene schickt dort `null`.
    getGroupInviteInfo.mockResolvedValue({ ...INFO, invite_card: null })

    render(<GruppenEinladungsKarte inviteCode={CODE} schluessel={null} onJoin={vi.fn()} />)

    expect(await screen.findByText('Serverteam')).toBeInTheDocument()
  })

  it('reicht den geöffneten Karteninhalt an den Beitritt weiter', async () => {
    /*
     * Seit Stufe 6 die Klartextspalten geräumt hat, ist diese Karte die
     * einzige Stelle, an der der Name einer fremden Gruppe **vor** dem
     * Beitritt bekannt ist: der Server kennt ihn nicht, und den
     * verschlüsselten Gruppenblock kann ein Beitretender noch nicht lesen —
     * das Gruppengeheimnis kommt erst mit der ersten Nachricht. Fällt der
     * Inhalt hier auf den Boden, heisst die frisch betretene Gruppe für immer
     * „Verschlüsselte Gruppe".
     */
    getGroupInviteInfo.mockResolvedValue(await mitKarte({ logo: LOGO }))
    const schluessel = await einladungsschluesselAus(GEHEIMNIS)
    const onJoin = vi.fn().mockResolvedValue(undefined)

    render(<GruppenEinladungsKarte inviteCode={CODE} schluessel={schluessel} onJoin={onJoin} />)

    await screen.findByText('Serverteam')
    fireEvent.click(screen.getByRole('button', { name: i18n.t('social.invite.join') }))

    await waitFor(() =>
      expect(onJoin).toHaveBeenCalledWith(CODE, {
        name: 'Serverteam',
        beschreibung: 'Wir bauen Dinge',
        logo: LOGO,
      }),
    )
  })
})
