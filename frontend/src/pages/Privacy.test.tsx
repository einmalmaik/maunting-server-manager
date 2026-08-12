import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Privacy } from './Privacy';
import { useAuthStore } from '@/stores/authStore';
import i18n from '@/i18n';

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
  it('weist die zur Guardian-Kopplung gehoerende Fassung 2.4 vom 2026-08-12 aus', () => {
    const { container } = renderPrivacy();

    expect(
      screen.getByText(`${i18n.t('privacyPolicy.versionLabel')} v2.4`),
    ).toBeInTheDocument();

    const stand = container.querySelector('time');
    expect(stand).not.toBeNull();
    // Maschinenlesbar und sichtbar muessen dasselbe Datum tragen: ein Leser
    // vergleicht den Text, ein Archiv das Attribut.
    expect(stand).toHaveAttribute('datetime', '2026-08-12');
    expect(stand).toHaveTextContent('2026-08-12');
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
    // Ausdruecklich als Zahl festgehalten: vor der Guardian-Kopplung waren es
    // neun Punkte, jetzt sind es zehn.
    expect(gerendert).toHaveLength(10);
  });
});

