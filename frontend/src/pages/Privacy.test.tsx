import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter, Outlet } from 'react-router-dom';
import { Privacy } from './Privacy';
import { DATENSCHUTZ_VERSION } from './datenschutzStand';
import { RechtlichesEinstellungen } from '@/desktop/einstellungsreiter/RechtlichesEinstellungen';
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
  'memorySearch',
  'attachments',
  'autonomy',
  'tools',
  // Seit 09/2026: die KI hat kein Werkzeug mehr, das den Messenger anfasst.
  // Steht bewusst direkt hinter `tools` — der eine Punkt sagt, was die
  // Werkzeuge erreichen, der andere, was sie ausdruecklich nicht erreichen.
  'noMessenger',
  'voice',
  'guardian',
  'tasks',
  'mailboxes',
  'calendars',
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
  it('weist die Fassung 3.39 vom 2026-10-09 aus (Updates fragen GitHub)', () => {
    const { container } = renderPrivacy();

    expect(
      screen.getByText(new RegExp(`${i18n.t('privacyPolicy.versionLabel')}\\s+v?3\\.39`)),
    ).toBeInTheDocument();
    expect(i18n.t('privacyPolicy.sections.messenger.items.pushMetadata')).toMatch(/Gerät entfernst/);
    // Die Zusage und ihre Grenze stehen zusammen: verschluesselt ist die
    // Datenbank, nicht der Weg durch das Panel.
    expect(i18n.t('privacyPolicy.sections.ai.items.messages')).toMatch(/nur verschlüsselt/);
    expect(i18n.t('privacyPolicy.sections.ai.items.messages')).toMatch(/das Panel selbst entschlüsselt/);
    expect(i18n.t('privacyPolicy.sections.ai.items.messages')).toMatch(/zugestellte Berichtsmail wird aus dem Ausgangskorb gelöscht/);
    // 3.8: vorher war nur der Wert eines Eintrags verschluesselt, sein Name nicht.
    expect(i18n.t('privacyPolicy.sections.ai.items.memory')).toMatch(/samt ihrem Namen verschlüsselt/);
    // 3.9: der Offline-Speicher war an keinen Account gebunden und ueberlebte das Abmelden.
    expect(i18n.t('privacyPolicy.sections.storage.items.offlineNotesAndCalendar')).toMatch(/Beim Abmelden wird er gelöscht/);
    // 3.10: der Export; was nur das Geraet oeffnen kann, geht nicht ueber den Server.
    expect(i18n.t('privacyPolicy.sections.retention.items.export')).toMatch(/nicht über den Server/);
    // 3.12: Stories lagen im Klartext in der Datenbank; lesen kann die Instanz sie weiterhin.
    expect(i18n.t('privacyPolicy.sections.messenger.items.stories')).toMatch(/nicht Ende-zu-Ende/);
    expect(i18n.t('privacyPolicy.sections.messenger.items.stories')).toMatch(/Datenbank stehen Text und Bild verschlüsselt/);
    // 3.13: Tresor-Cloud; was die Instanz trotzdem sieht, und die Update-Abfrage bei GitHub.
    expect(i18n.t('privacyPolicy.sections.vault.items.dateien')).toMatch(/wie groß sie aufgerundet sind/);
    // 3.14: die Datenbank ordnet Tresor und Dateien keinem Konto mehr im Klartext zu.
    expect(i18n.t('privacyPolicy.sections.vault.items.speicher')).toMatch(/nur als Prüfwert/);
    expect(i18n.t('privacyPolicy.sections.vault.items.speicher')).toMatch(/Wer Zugriff auf den Server selbst hat/);
    expect(i18n.t('privacyPolicy.sections.vault.items.dateien')).toMatch(/je Tresor/);
    // Zuruecksetzen loescht seither alles; bis dahin blieben die Eintraege liegen.
    expect(i18n.t('privacyPolicy.sections.vault.items.verlust')).toMatch(/entfernt die Instanz alle Einträge sofort/);
    expect(i18n.t('privacyPolicy.sections.vault.items.verlust')).toMatch(/nur die Kennung des alten Tresors/);
    expect(i18n.t('privacyPolicy.sections.desktopApp.body')).toMatch(/GitHub sieht dabei Ihre IP-Adresse/);
    // 3.15: die Leck-Prüfung war ein ungenannter Fremdanbieter, der Hinweis hiess
    // „verschlüsselt", obwohl die Instanz ihn liest, und „keinem Konto
    // zuordnen" galt nicht für Prüfprotokoll und Auszeichnung.
    expect(i18n.t('privacyPolicy.sections.desktopApp.body')).toMatch(/Have I Been Pwned/);
    expect(i18n.t('privacyPolicy.sections.desktopApp.body')).not.toMatch(/einzige Ausnahme/);
    expect(i18n.t('privacyPolicy.sections.vault.items.hinweis')).toMatch(/Die Instanz kann ihn also lesen/);
    expect(i18n.t('privacyPolicy.sections.vault.items.speicher')).toMatch(/Prüfprotokoll vermerkt/);
    expect(i18n.t('privacyPolicy.sections.vault.items.dateien')).toMatch(/bis zu 5 frühere Fassungen/);
    expect(i18n.t('privacyPolicy.sections.vault.items.geraet')).toMatch(/512 MiB/);
    expect(i18n.t('privacyPolicy.sections.retention.items.export')).toMatch(/Dateien aus dem Tresor enthält es nicht/);
    expect(screen.getByText(i18n.t('privacyPolicy.sections.vault.items.hinweis'))).toBeInTheDocument();
    // 3.16: Kamera-Sicherung; den Aufnahmeort zog der Tresor nie eigens in den Eintrag, wie 3.15 behauptete.
    expect(screen.getByText(i18n.t('privacyPolicy.sections.vault.items.kamera'))).toBeInTheDocument();
    expect(i18n.t('privacyPolicy.sections.vault.items.kamera')).toMatch(/aus, bis Sie sie einschalten/);
    expect(i18n.t('privacyPolicy.sections.vault.items.kamera')).toMatch(/Android fragt vorher/);
    expect(i18n.t('privacyPolicy.sections.vault.items.dateien')).toMatch(/liest der Tresor nicht eigens aus/);
    expect(i18n.t('privacyPolicy.sections.vault.items.geraet')).toMatch(/auf diesem Gerät hochgeladen/);
    // 3.17: bei gesperrtem Tresor über den Posteingang; was die Instanz davon sieht.
    expect(i18n.t('privacyPolicy.sections.vault.items.kamera')).toMatch(/gesperrtem Tresor/);
    expect(i18n.t('privacyPolicy.sections.vault.items.kamera')).toMatch(/Vom Posteingang sieht die Instanz nur/);
    expect(i18n.t('privacyPolicy.sections.vault.items.kamera')).toMatch(/der Datenexport enthält sie nicht/);
    // 3.18: unter Android lief nie ein Dienst, der die Instanz erreichte; Erinnerungen plant jetzt Android selbst.
    expect(i18n.t('privacyPolicy.sections.desktopApp.items.benachrichtigungen')).toMatch(/Für Erinnerungen läuft unter Android kein Hintergrunddienst/);
    expect(i18n.t('privacyPolicy.sections.desktopApp.items.benachrichtigungen')).toMatch(/fragt dafür nicht bei der Instanz nach/);
    expect(i18n.t('privacyPolicy.sections.desktopApp.items.autostart')).not.toMatch(/Handystart/);
    // 3.19: die Kamera-Sicherung lief nur bei offener App; jetzt im Hintergrund mit eigenem Zugang.
    expect(i18n.t('privacyPolicy.sections.vault.items.kamera')).not.toMatch(/solange die App offen ist/);
    expect(i18n.t('privacyPolicy.sections.vault.items.kamera')).toMatch(/auch bei geschlossener App/);
    expect(i18n.t('privacyPolicy.sections.vault.items.kamera')).toMatch(/Sicherungszugang/);
    expect(i18n.t('privacyPolicy.sections.vault.items.kamera')).toMatch(/seine IP-Adresse/);
    // 3.20: Bildschirmfotos nur mit eigenem Schalter.
    expect(i18n.t('privacyPolicy.sections.vault.items.kamera')).toMatch(/Bildschirmfotos .* nur, wenn Sie das eigens einschalten/);
    // 3.21: nach einem Netz- oder Serverfehler fragt das Telefon alle 15 Minuten erneut.
    expect(i18n.t('privacyPolicy.sections.vault.items.kamera')).toMatch(/nach einem Netz- oder Serverfehler alle 15 Minuten/);
    // 3.22: nicht mehr nur DCIM, sondern auch Ordner anderer Apps; Teilen in den Tresor.
    expect(i18n.t('privacyPolicy.sections.vault.items.kamera')).not.toMatch(/Aufnahme im Kamera-Ordner/);
    expect(i18n.t('privacyPolicy.sections.vault.items.kamera')).toMatch(/Ordner anderer Apps wie Messenger oder Downloads/);
    expect(i18n.t('privacyPolicy.sections.vault.items.kamera')).toMatch(/„Tresor“ im Teilen-Menü/);
    // 3.29: Karten und Bankkonten sind eigene Einträge im Tresor.
    expect(i18n.t('privacyPolicy.sections.vault.items.eintraege')).toMatch(/Zahlungskarten, Bankkonten/);
    // 3.30: der Browser hat einen eigenen Abschnitt; Absturzberichte der Tabs gehen nicht an Microsoft.
    expect(screen.getByText(i18n.t('privacyPolicy.sections.browser.heading'))).toBeInTheDocument();
    expect(i18n.t('privacyPolicy.sections.browser.items.verbindungen')).toMatch(/easylist\.to .* beide Server sehen dabei Ihre IP-Adresse/);
    expect(i18n.t('privacyPolicy.sections.browser.items.microsoft')).toMatch(/kein Absturzbericht an Microsoft/);
    expect(i18n.t('privacyPolicy.sections.browser.items.passwoerter')).toMatch(/Ein Master-Passwort hält der Browser dafür nicht im Speicher/);
    // 3.31: Kategorielisten des Jugend- und Suchtschutzes kommen von GitHub, nur für aktive Kategorien.
    expect(i18n.t('privacyPolicy.sections.browser.items.schutz')).toMatch(/GitHub sieht dabei Ihre IP-Adresse und welche Liste Sie laden/);
    // 3.32: Downloads prüft der Virenschutz von Windows, der je nach Einstellung Proben an seinen Hersteller schickt.
    expect(i18n.t('privacyPolicy.sections.browser.items.downloads')).toMatch(/gehen verdächtige Dateien oder Angaben über sie an dessen Hersteller/);
    // 3.33: der Browser unter Android; private Tabs liegen dort bis zum letzten auf dem Telefon, Sprache und Foto gehen an fremde Dienste.
    expect(i18n.t('privacyPolicy.sections.browser.items.privat')).toMatch(/^Private Tabs .* Unter Windows liegen ihre Cookies/);
    expect(i18n.t('privacyPolicy.sections.browser.items.android')).toMatch(/Private Tabs teilen sich ein Profil auf dem Telefon/);
    expect(i18n.t('privacyPolicy.sections.browser.items.widget')).toMatch(/Spracherkennungsdienst des Telefons/);
    expect(i18n.t('privacyPolicy.sections.browser.items.msmSuche')).toMatch(/gehen Suchbegriffe .* an Ihre MSM-Instanz/);
    expect(i18n.t('privacyPolicy.sections.browser.items.nachrichten')).toMatch(/bleibt im Browser; gefiltert wird auf dem Gerät/);
    expect(i18n.t('privacyPolicy.sections.browser.items.widget')).toMatch(/Bildsuche der gewählten Suchmaschine/);
    // 3.34: das Token liegt unter Android im Keystore-Fach, Sicherung und Umzug sind ausgeschlossen.
    expect(i18n.t('privacyPolicy.sections.browser.items.kopplung')).toMatch(/unter Android verschlüsselt mit einem Schlüssel aus dem Android-Keystore/);
    expect(i18n.t('privacyPolicy.sections.browser.items.android')).toMatch(/nimmt sich von der Android-Datensicherung und vom Umzug auf ein neues Gerät aus/);
    // 3.35: Lockern misst an der Uhrzeit von GitHub; die Serie ohne Sperrtreffer geht nirgendwohin.
    expect(i18n.t('privacyPolicy.sections.browser.items.schutz')).toMatch(/an der Uhrzeit, die raw\.githubusercontent\.com .* nennt/);
    expect(i18n.t('privacyPolicy.sections.browser.items.schutz')).toMatch(/zählt er nur auf dem Gerät; die Zahl geht an keinen Dienst und nicht an MSM/);
    expect(i18n.t('privacyPolicy.sections.browser.items.schutz')).toMatch(/DuckDuckGo und Brave/);
    // 3.36: Android-Downloads lädt der Browser selbst; Cookies gehen nicht mehr an den Download-Dienst.
    expect(i18n.t('privacyPolicy.sections.browser.items.android')).toMatch(/Cookies schickt er dabei nur an die Seite, der sie gehören/);
    expect(i18n.t('privacyPolicy.sections.browser.items.android')).toMatch(/den Prozess der WebView teilen sich Seiten und Oberfläche unter Android/);
    // 3.37: die Cookie-Liste kommt von einem weiteren Server; das Seitensymbol holt die Oberfläche nicht mehr selbst.
    expect(i18n.t('privacyPolicy.sections.browser.items.verbindungen')).toMatch(/secure\.fanboy\.co\.nz/);
    expect(i18n.t('privacyPolicy.sections.browser.items.verbindungen')).not.toMatch(/direkt von der Seite/);
    expect(i18n.t('privacyPolicy.sections.browser.items.uebersetzung')).toMatch(/Der Text der Seite geht an keinen Dienst .*Mozilla .firefox-settings-attachments\.cdn\.mozilla\.net/);
    expect(i18n.t('privacyPolicy.sections.browser.items.verbindungen')).toMatch(/Übersetzung/);
    // 3.38: Cookie-Hinweise lehnt der Browser ab (autoconsent), ohne Anfrage nach außen.
    expect(i18n.t('privacyPolicy.sections.browser.items.https')).toMatch(/eine Einwilligung gibt er nie/);
    expect(i18n.t('privacyPolicy.sections.browser.items.https')).toMatch(/autoconsent \(DuckDuckGo\).*keine Anfrage hinaus/);
    expect(i18n.t('privacyPolicy.sections.browser.items.android')).toMatch(/Links aus anderen Apps in einem neuen, normalen Tab/);
    // 3.39: unter Windows fragt der Browser beim Start GitHub nach Updates.
    expect(i18n.t('privacyPolicy.sections.browser.items.updates')).toMatch(/fragt der Browser bei GitHub/);
    expect(i18n.t('privacyPolicy.sections.browser.items.updates')).toMatch(/erst auf Ihren Klick/);

    const stand = container.querySelector('time');
    expect(stand).not.toBeNull();
    // Maschinenlesbar und sichtbar muessen dasselbe Datum tragen: ein Leser
    // vergleicht den Text, ein Archiv das Attribut.
    expect(stand).toHaveAttribute('datetime', '2026-10-09');
    expect(stand).toHaveTextContent('2026-10-09');
  });

  it('nennt in den Einstellungen der App dieselbe Fassung wie die Erklärung selbst', () => {
    // Bis 04.10.2026 stand dort fest „v2.7", während die Erklärung bei 3.22 war.
    apiMock.mockResolvedValue({ imprint_enabled: false, imprint_url: '' });
    renderPrivacy();
    const nummer = DATENSCHUTZ_VERSION.replaceAll('.', '\\.');
    expect(screen.getByText(new RegExp(`${i18n.t('privacyPolicy.versionLabel')}\\s+v?${nummer}`))).toBeInTheDocument();
    const fassung = new RegExp(`v${nummer}$`);
    const einstellungen = render(
      <MemoryRouter>
        <RechtlichesEinstellungen />
      </MemoryRouter>,
    );
    expect(within(einstellungen.container).getByText(fassung)).toBeInTheDocument();
  });

  /**
   * Ablehnen heisst beim Koppeln nur „kein Verlauf". Das Gerät bleibt ein Gerät
   * des Kontos und bekommt jede neue Nachricht und den Notizschlüssel — wer das
   * nicht will, muss es entfernen. Das gehört in die Erklärung, nicht erst in
   * die Meldung nach dem Klick.
   */
  it('sagt, was nach dem Ablehnen beim Koppeln weiterläuft und wie man ein Gerät loswird', () => {
    const de = i18n.t('privacyPolicy.sections.messenger.items.deviceHistory', { lng: 'de' })
    expect(de).toMatch(/nur mit der Unterschrift des übergebenden Geräts/)
    expect(de).toMatch(/bleibt das Gerät gekoppelt, aber ohne Freigabe/)
    const en = i18n.t('privacyPolicy.sections.messenger.items.deviceHistory', { lng: 'en' })
    expect(en).toMatch(/only accepts with the signature of the handing-over device/)
    expect(en).toMatch(/the device stays paired but unapproved/)
  });

  /**
   * Seit 24.09. sperrt Entfernen die Sitzung sofort; die 15 Minuten eines
   * schon ausgestellten Zugangs sind weg. Was bleibt: der Notizschlüssel, den
   * ein freigegebenes Gerät schon hatte, wird nicht erneuert. Und die
   * Webversion schützt nicht gegen ihren eigenen Server — das steht dabei.
   */
  it('verspricht vom Entfernen nicht mehr, als es hält', () => {
    const de = i18n.t('privacyPolicy.sections.messenger.items.deviceHistory', { lng: 'de' })
    expect(de).toMatch(/gilt ab der nächsten Anfrage nicht mehr/)
    expect(de).toMatch(/beim Entfernen wird er nicht erneuert/)
    expect(de).toMatch(/Gegen einen Betreiber, der ihn verändert/)
    expect(de).not.toMatch(/15 Minuten/)
    const en = i18n.t('privacyPolicy.sections.messenger.items.deviceHistory', { lng: 'en' })
    expect(en).toMatch(/stops working with its next request/)
    expect(en).toMatch(/removing it does not replace that key/)
    expect(en).toMatch(/an operator who changes it/)
    expect(en).not.toMatch(/15 minutes/)
    for (const lng of ['de', 'en']) {
      expect(i18n.t('ai.profile.devicePairRemoved', { lng })).not.toMatch(/15/)
    }
  });

  /**
   * Die Download-Hinweise in der Seitenleiste sind der einzige Ort, an dem das Panel auf
   * einen Fremdserver verweist, den der Betreiber nicht selbst eingetragen hat. Wer darauf
   * klickt, gibt GitHub seine IP-Adresse — das gehoert benannt, und zwar bevor jemand klickt.
   */
  it('benennt, dass ein Klick auf die Download-Hinweise GitHub erreicht', () => {
    renderPrivacy();

    const punkt = i18n.t('privacyPolicy.sections.providers.items.downloads');
    expect(punkt).not.toBe('privacyPolicy.sections.providers.items.downloads');
    expect(punkt).toMatch(/GitHub/);
    expect(punkt).toMatch(/IP-Adresse/);
    expect(screen.getByText(punkt)).toBeInTheDocument();
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
    // Guardian-Kopplung, zehn danach, elf seit den stehenden KI-Aufgaben,
    // dreizehn seit verknüpften Postfächern und Kalendern, vierzehn mit
    // Sprachmodus, fuenfzehn seit die KI den Messenger nicht mehr erreicht,
    // sechzehn seit dem Google-Rückfall der Bedeutungssuche (24.09.2026).
    expect(gerendert).toHaveLength(16);
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

