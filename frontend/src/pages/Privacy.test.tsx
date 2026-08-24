import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Outlet } from 'react-router-dom';
import { Privacy } from './Privacy';
import App from '@/App';
import { useAuthStore } from '@/stores/authStore';
import i18n from '@/i18n';

// Die Shell zieht das halbe Panel nach — für diese Zusicherung zählt nur, DASS
// sie gerendert wird, nicht was in ihr steht.
vi.mock('@/components/layout/Shell', () => ({
  Shell: () => (
    <div data-testid="shell">
      <Outlet />
    </div>
  ),
}));

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }));
vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  api: apiMock,
}));

function renderPrivacy() {
  return render(
    <MemoryRouter>
      <Privacy />
    </MemoryRouter>,
  );
}

/**
 * Die Punkte des KI-Abschnitts in genau der Reihenfolge, in der sie im Dokument
 * stehen sollen. Die Liste ist absichtlich vollstaendig statt "mindestens":
 * ein Punkt, der bei einem spaeteren Umbau still herausfaellt, waere aus der
 * Oberflaeche nicht zu erkennen — die Seite saehe weiterhin vollstaendig aus,
 * nur die zugesagte Aussage fehlte. Ein Vergleich auf Gleichheit macht sowohl
 * das Entfernen als auch das unbeabsichtigte Umsortieren sichtbar.
 */
const KI_PUNKTE = [
  'messages',
  'context',
  'credentials',
  'usage',
  'memory',
  'memoryConsent',
  'attachments',
  'autonomy',
  'tools',
  'guardian',
  'tasks',
] as const;

describe('Privacy page', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de');
    // Reset auth state
    useAuthStore.setState({ isAuthenticated: false });
  });

  it('renders privacy policy sections when unauthenticated (public page)', () => {
    useAuthStore.setState({ isAuthenticated: false });
    renderPrivacy();

    expect(screen.getByRole('link', { name: new RegExp(i18n.t('common.back')) })).toHaveAttribute('href', '/login');

    expect(screen.getAllByText(i18n.t('privacyPolicy.title')).length).toBeGreaterThan(0);
    expect(screen.getByText(i18n.t('privacyPolicy.sections.scope.heading'))).toBeInTheDocument();
    const calloutText1 = i18n.t('privacyPolicy.callout').replace(/^Kurzfassung:\s*|^Summary:\s*/i, '');
    expect(screen.getAllByText((content) => content.includes(calloutText1.substring(0, 15))).length).toBeGreaterThan(0);
    expect(screen.getByText(i18n.t('privacyPolicy.sections.accounts.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.infrastructure.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.protection.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.providers.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.storage.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.retention.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.responsibility.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.documentLabel'))).toBeInTheDocument();
  });


  it('renders S3 encrypted backup section', () => {
    useAuthStore.setState({ isAuthenticated: false });
    renderPrivacy();

    expect(screen.getByText(i18n.t('privacyPolicy.sections.protection.items.backups'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.providers.items.s3'))).toBeInTheDocument();
  });

  it('renders privacy policy sections when authenticated (in-app page)', () => {
    useAuthStore.setState({ isAuthenticated: true });
    renderPrivacy();

    expect(screen.getByRole('link', { name: new RegExp(i18n.t('common.back')) })).toHaveAttribute('href', '/docs');

    expect(screen.getAllByText(i18n.t('privacyPolicy.title')).length).toBeGreaterThan(0);
    expect(screen.getByText(i18n.t('privacyPolicy.sections.scope.heading'))).toBeInTheDocument();
    const calloutText2 = i18n.t('privacyPolicy.callout').replace(/^Kurzfassung:\s*|^Summary:\s*/i, '');
    expect(screen.getAllByText((content) => content.includes(calloutText2.substring(0, 15))).length).toBeGreaterThan(0);
    expect(screen.getByText(i18n.t('privacyPolicy.sections.accounts.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.infrastructure.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.protection.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.providers.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.storage.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.retention.heading'))).toBeInTheDocument();
    expect(screen.getByText(i18n.t('privacyPolicy.sections.responsibility.heading'))).toBeInTheDocument();
  });

  /**
   * Die Kopplung der KI an die Guardian-Engine ist die eine Aussage im
   * KI-Abschnitt, die aus keinem der anderen Punkte folgt: alle uebrigen
   * beschreiben, was mit einer EINGABE des Nutzers geschieht, dieser beschreibt
   * eine Verarbeitung OHNE Eingabe — ein Guardian-Vorfall kann sie ausloesen,
   * waehrend niemand am Panel sitzt. Genau deshalb darf sie nicht als
   * Selbstverstaendlichkeit unter "Werkzeuge" mitlaufen, sondern muss als
   * eigener, sichtbar gerenderter Punkt dastehen.
   */
  it('nennt die Guardian-gestartete Verarbeitung als eigenen Punkt im KI-Abschnitt', () => {
    renderPrivacy();

    const punkt = i18n.t('privacyPolicy.sections.ai.items.guardian');
    // Ein leerer oder auf den Schluesselnamen zurueckgefallener Text waere
    // gerendert, aber ohne Aussage — beides schliessen wir hier aus.
    expect(punkt).not.toBe('privacyPolicy.sections.ai.items.guardian');
    expect(punkt.length).toBeGreaterThan(40);
    expect(screen.getByText(punkt)).toBeInTheDocument();
  });

  /**
   * Version und Stand sind die einzige Handhabe, an der ein Nutzer erkennt, dass
   * sich die Zusagen geaendert haben. Ein neuer Absatz ohne neue Versionsnummer
   * ist praktisch eine stille Aenderung — deshalb haengt die Zusage hier an den
   * konkreten Werten und nicht an "irgendeiner" Version.
   */
  it('weist die zur Desktop-App gehoerende Fassung 2.7 vom 2026-08-24 aus', () => {
    const { container } = renderPrivacy();

    expect(
      screen.getByText(new RegExp(`${i18n.t('privacyPolicy.versionLabel')}\\s+v?2\\.7`)),
    ).toBeInTheDocument();

    const stand = container.querySelector('time');
    expect(stand).not.toBeNull();
    // Maschinenlesbar und sichtbar muessen dasselbe Datum tragen: ein Leser
    // vergleicht den Text, ein Archiv das Attribut.
    expect(stand).toHaveAttribute('datetime', '2026-08-24');
    expect(stand).toHaveTextContent('2026-08-24');
  });

  /**
   * Zaehlt und vergleicht die Punkte des KI-Abschnitts als Ganzes. Der Abschnitt
   * ist mit der Guardian-Kopplung um einen Punkt gewachsen; ein spaeter
   * entfernter oder verschobener Punkt faellt hier als Diff der ganzen Liste auf
   * und nicht erst dann, wenn jemand die Seite liest.
   */
  it('fuehrt genau die zugesagten Punkte des KI-Abschnitts, in dieser Reihenfolge', () => {
    renderPrivacy();

    const ueberschrift = screen.getByText(i18n.t('privacyPolicy.sections.ai.heading'));
    const abschnitt = ueberschrift.closest('section');
    expect(abschnitt).not.toBeNull();

    const gerendert = Array.from(abschnitt!.querySelectorAll('li')).map((li) => li.textContent);
    expect(gerendert).toEqual(
      KI_PUNKTE.map((schluessel) => i18n.t(`privacyPolicy.sections.ai.items.${schluessel}`)),
    );
    // Ausdruecklich als Zahl festgehalten: neun Punkte vor der
    // Guardian-Kopplung, zehn danach, elf seit den stehenden KI-Aufgaben.
    expect(gerendert).toHaveLength(11);
  });
});

/**
 * /privacy ist die einzige Seite, die es zweimal gibt: einmal öffentlich neben
 * den Anmeldeformularen und einmal innerhalb der Shell. Welche der beiden ein
 * harter Reload trifft, hängt allein daran, ob der Anmeldezustand zu diesem
 * Zeitpunkt schon geladen wurde — und die statische Route gewinnt gegen das
 * Splat, ProtectedRoute mountet also nie. Ohne einen eigenen Anstoß in App
 * bliebe ein angemeldeter Benutzer dauerhaft auf der öffentlichen Fassung
 * sitzen, ohne Navigation und mit einem Zurück-Knopf aufs Anmeldeformular.
 */
describe('Privacy nach hartem Reload', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('de');
    apiMock.mockReset();
    useAuthStore.setState({ user: null, isAuthenticated: false, isLoading: true });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ setup_required: false, email_configured: true }),
      })),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('zeigt einem angemeldeten Benutzer das Panel statt der öffentlichen Fassung', async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path === '/auth/me') {
        return { id: 1, username: 'admin', email: 'admin@example.test' };
      }
      return { permissions: [], is_owner: false };
    });

    render(
      <MemoryRouter initialEntries={['/privacy']}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('shell')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: new RegExp(i18n.t('common.back')) })).toHaveAttribute(
      'href',
      '/docs',
    );
  });

  /**
   * Die Desktop-App stand bis zum 23.08.2026 in keiner Zeile dieses
   * Dokuments — kein Treffer fuer „Mikrofon", „Bildschirm" oder „Rechner",
   * obwohl sie dauerhaft mithoert, den Bildschirm fotografiert und
   * ausserhalb der Sandbox loescht. Dieser Test haelt die beiden Aussagen
   * fest, die ein Leser am wenigsten erwartet und am dringendsten braucht.
   */
  it('nennt das Dauermikrofon und die liegenbleibenden Stimmaufnahmen', () => {
    renderPrivacy();

    const dauerhaft = i18n.t('privacyPolicy.sections.desktopApp.items.wakeword');
    const aufnahmen = i18n.t(
      'privacyPolicy.sections.desktopApp.items.wakewordAufnahmen',
    );
    // Kein durchgereichter Schluessel und keine Ueberschrift ohne Inhalt.
    expect(dauerhaft).not.toContain('privacyPolicy.');
    expect(dauerhaft.length).toBeGreaterThan(40);
    expect(screen.getByText(dauerhaft)).toBeInTheDocument();
    expect(screen.getByText(aufnahmen)).toBeInTheDocument();

    // Und die beiden Tatsachen ausdruecklich, nicht nur irgendein Text:
    expect(dauerhaft).toMatch(/dauerhaft/i);
    expect(aufnahmen).toMatch(/unbefristet/i);
  });

  it('nennt die Standard-Deaktivierung und Bestaetigungspflicht von Computer-Use', () => {
    renderPrivacy();

    const computerUse = i18n.t(
      'privacyPolicy.sections.desktopApp.items.computerUse',
    );
    expect(computerUse).not.toContain('privacyPolicy.');
    expect(computerUse.length).toBeGreaterThan(40);
    expect(screen.getByText(computerUse)).toBeInTheDocument();
    expect(computerUse).toMatch(/deaktiviert/i);
  });

  /**
   * Die Nummerierung traegt die Verweise im Text („siehe Abschnitt 6").
   * Ein eingeschobener Abschnitt, der die folgenden nicht mitverschiebt,
   * erzeugt zwei Abschnitte mit derselben Nummer — genau das ist beim
   * Einbau am 23.08.2026 einmal passiert.
   */
  it('vergibt jede Abschnittsnummer genau einmal', () => {
    renderPrivacy();

    const nummern = screen
      .getAllByRole('heading')
      .map((kopf) => kopf.textContent ?? '')
      .map((text) => /^(\d+)\./.exec(text)?.[1])
      .filter((n): n is string => Boolean(n));

    expect(nummern.length).toBeGreaterThan(5);
    expect(new Set(nummern).size).toBe(nummern.length);
  });
});

